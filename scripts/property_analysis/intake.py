"""
Deal intake — DB-backed (SQLite). Replaces the Excel-first reference_data
flow for new deals; reference_data.xlsx stays for persistent OVERRIDES only.

Schema follows Sean's DB_AND_UI_REVIEW.md Deal Intake table (22 user-facing
columns + 3 system columns). Each row is one deal that can be created,
listed, retrieved, updated, and analyzed.

A deal becomes "analyze-ready" when the required fields are present:
  address, arv_base, rehab_budget, section8_status

If purchase_price is None, the analyzer runs in bid-guidance mode (max-bid
solver) for that deal.

Public API:
  - schema_init(db_path)
  - create_deal(**fields) -> Deal
  - get_deal(deal_id) -> Deal
  - update_deal(deal_id, **fields) -> Deal
  - list_deals(status=None) -> list[Deal]
  - analyze_ready_deals() -> list[Deal]
  - mark_analyzed(deal_id, run_id, packet_path)

The CLI integration lives in analyze.py via:
  --create-deal       (interactive creation; prints deal_id)
  --from-intake <id>  (load fields from DB and run pipeline)
  --list-deals
  --analyze-ready     (analyze every deal in 'ready' status)
"""
from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field, fields as dc_fields, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Tests + the regression suite set PROPERTY_ANALYSIS_DB to an isolated path so
# they never touch the canonical intake DB. Production code path is unchanged.
DB_PATH = Path(
    os.environ.get(
        "PROPERTY_ANALYSIS_DB",
        str(PROJECT_ROOT / "templates" / "property_analysis" / "data" / "deal_intake.db"),
    )
)


VALID_STATUS = ("new", "needs_inputs", "ready", "analyzed", "archived")
VALID_SECTION8 = ("confirmed", "assumed", "not_section8", "unknown")


# ──────────────────────────────────────────────
# DEAL DATACLASS
# ──────────────────────────────────────────────

@dataclass
class Deal:
    """One row of deal_intake. Mirrors the SQLite schema 1:1."""
    deal_id: str = ""
    status: str = "new"                          # VALID_STATUS

    # Required user inputs
    address: str = ""
    arv_base: Optional[float] = None
    rehab_budget: Optional[float] = None
    section8_status: str = "assumed"             # VALID_SECTION8

    # Optional user inputs
    purchase_price: Optional[float] = None
    beds_override: Optional[int] = None
    baths_override: Optional[float] = None
    sqft_override: Optional[int] = None
    annual_tax_override: Optional[float] = None
    prior_year_tax: Optional[float] = None
    monthly_rent_override: Optional[float] = None
    insurance_annual: Optional[float] = None
    owner_utilities_annual: Optional[float] = 0.0
    hoa_annual: Optional[float] = 0.0
    hold_period_years: Optional[int] = None
    exit_cap_rate: Optional[float] = None
    down_payment_pct: Optional[float] = None
    interest_rate: Optional[float] = None
    refi_year: Optional[int] = None
    refi_ltv: Optional[float] = None
    notes: str = ""

    # T-613: per-deal threshold overrides. NULL falls through to the global
    # ThresholdSet read from reference_data.xlsx; non-NULL beats global.
    override_cap_rate_min: Optional[float] = None
    override_cash_on_cash_min: Optional[float] = None
    override_dscr_min: Optional[float] = None
    override_irr_min: Optional[float] = None
    override_max_price_to_arv: Optional[float] = None

    # System columns
    created_at: str = ""
    updated_at: str = ""
    last_analyzed_run_id: str = ""
    last_packet_path: str = ""

    @property
    def is_ready(self) -> bool:
        """True if the minimum required fields are populated."""
        return bool(
            self.address
            and self.arv_base is not None
            and self.rehab_budget is not None
            and self.section8_status in VALID_SECTION8
        )

    def required_missing(self) -> list[str]:
        """Names of required fields that are still unpopulated."""
        missing = []
        if not self.address: missing.append("address")
        if self.arv_base is None: missing.append("arv_base")
        if self.rehab_budget is None: missing.append("rehab_budget")
        if self.section8_status not in VALID_SECTION8:
            missing.append("section8_status")
        return missing


# ──────────────────────────────────────────────
# SCHEMA
# ──────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS deal_intake (
    deal_id                 TEXT PRIMARY KEY,
    status                  TEXT NOT NULL DEFAULT 'new',
    address                 TEXT NOT NULL,
    arv_base                REAL,
    rehab_budget            REAL,
    section8_status         TEXT NOT NULL DEFAULT 'assumed',
    purchase_price          REAL,
    beds_override           INTEGER,
    baths_override          REAL,
    sqft_override           INTEGER,
    annual_tax_override     REAL,
    prior_year_tax          REAL,
    monthly_rent_override   REAL,
    insurance_annual        REAL,
    owner_utilities_annual  REAL DEFAULT 0,
    hoa_annual              REAL DEFAULT 0,
    hold_period_years       INTEGER,
    exit_cap_rate           REAL,
    down_payment_pct        REAL,
    interest_rate           REAL,
    refi_year               INTEGER,
    refi_ltv                REAL,
    notes                   TEXT DEFAULT '',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    last_analyzed_run_id    TEXT DEFAULT '',
    last_packet_path        TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_deal_intake_status ON deal_intake(status);
CREATE INDEX IF NOT EXISTS idx_deal_intake_address ON deal_intake(address);

-- T-611: Property_Overrides migrated from reference_data.xlsx into the DB
-- so Joe can manage them via the webapp without opening Excel. Address is
-- the natural key (lowercased + trimmed).
CREATE TABLE IF NOT EXISTS property_overrides (
    address_normalized      TEXT PRIMARY KEY,
    beds                    INTEGER,
    baths                   REAL,
    annual_tax              REAL,
    prior_year_tax          REAL,
    market_rent             REAL,
    notes                   TEXT DEFAULT '',
    updated_at              TEXT NOT NULL
);

-- T-612: Global buy thresholds migrated from reference_data.xlsx Thresholds
-- tab. Key/value so adding a new threshold does not require a schema
-- migration. Per-deal overrides live as nullable columns on deal_intake.
CREATE TABLE IF NOT EXISTS thresholds (
    name        TEXT PRIMARY KEY,
    value       REAL NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

THRESHOLD_NAMES = (
    "cap_rate_min",
    "cash_on_cash_min",
    "dscr_min",
    "irr_min",
    "max_price_to_arv",
    "vacancy_default",
    "rent_growth_default",
    "expense_growth_default",
)

THRESHOLD_DEFAULTS = {
    "cap_rate_min": 0.08,
    "cash_on_cash_min": 0.10,
    "dscr_min": 1.25,
    "irr_min": 0.15,
    "max_price_to_arv": 0.75,
    "vacancy_default": 0.04,
    "rent_growth_default": 0.03,
    "expense_growth_default": 0.03,
}


# T-613: nullable per-deal threshold override columns. ALTER TABLE in a
# separate block so older DBs (V1 schema) auto-upgrade on next schema_init.
THRESHOLD_OVERRIDE_COLUMNS = [
    "override_cap_rate_min",
    "override_cash_on_cash_min",
    "override_dscr_min",
    "override_irr_min",
    "override_max_price_to_arv",
]


def schema_init(db_path: Optional[Path] = None) -> Path:
    """Create the SQLite DB and schema if it does not exist. Idempotent.

    Also auto-upgrades older V1 schemas to V1.1 by adding any missing
    threshold-override columns (T-613). ALTER TABLE ADD COLUMN is a
    no-op on already-current schemas.
    """
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # contextlib.closing is required on top of the `with sqlite3.connect(...)`
    # context manager — that built-in form commits/rolls-back but does NOT
    # close the connection on exit, which leaves the file locked on Windows
    # and breaks shutil.rmtree on the regression temp dir.
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(SCHEMA_SQL)
        # T-613 migration for older DBs: add the 5 override columns if absent.
        existing_cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(deal_intake)"
            ).fetchall()
        }
        for col in THRESHOLD_OVERRIDE_COLUMNS:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE deal_intake ADD COLUMN {col} REAL")
        conn.commit()
    return db_path


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = db_path or DB_PATH
    if not db_path.exists():
        schema_init(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextlib.contextmanager
def _open(db_path: Optional[Path] = None):
    """Open a connection, commit on clean exit, rollback on error, ALWAYS close.

    The plain `sqlite3` connection context manager commits/rolls-back but does
    not close the file handle. On Windows that prevents temp-dir cleanup.
    """
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ──────────────────────────────────────────────
# ID GENERATION
# ──────────────────────────────────────────────

def _slugify(address: str) -> str:
    """Stable slug for deal IDs. Same logic as packet folder slug."""
    s = unicodedata.normalize("NFKD", address)
    s = "".join(c for c in s if not unicodedata.combining(c))
    street_only = re.split(r"[,]", s)[0]
    return re.sub(r"[^A-Za-z0-9]+", "_", street_only).strip("_")[:48]


def _generate_deal_id(address: str, db_path: Optional[Path] = None) -> str:
    """Generate a deterministic, collision-safe deal ID.

    Format: <slug>_<YYYYMMDD>_<n> where <n> increments per-day per-slug.
    """
    slug = _slugify(address)
    today = datetime.now().strftime("%Y%m%d")
    base = f"{slug}_{today}"
    with _open(db_path) as conn:
        rows = conn.execute(
            "SELECT deal_id FROM deal_intake WHERE deal_id LIKE ?",
            (f"{base}_%",),
        ).fetchall()
    nums = []
    for r in rows:
        m = re.match(rf"^{re.escape(base)}_(\d+)$", r["deal_id"])
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"{base}_{n}"


# ──────────────────────────────────────────────
# CRUD
# ──────────────────────────────────────────────

def _row_to_deal(row: sqlite3.Row) -> Deal:
    """Convert SQLite row to Deal dataclass."""
    data = {k: row[k] for k in row.keys() if k in {f.name for f in dc_fields(Deal)}}
    return Deal(**data)


def _deal_status(deal: Deal) -> str:
    """Derive the status from the deal's content. 'ready' overrides 'new'
    when required fields are populated; user can still override via update."""
    if deal.status == "archived":
        return "archived"
    if deal.status == "analyzed":
        return "analyzed"
    if deal.is_ready:
        return "ready"
    return "needs_inputs" if any([
        deal.arv_base is not None, deal.rehab_budget is not None,
        deal.purchase_price is not None,
    ]) else "new"


def create_deal(*, db_path: Optional[Path] = None, **fields) -> Deal:
    """Create a new deal. address is required. Returns the persisted Deal."""
    if not fields.get("address"):
        raise ValueError("create_deal requires address")

    # Build Deal with provided fields + system defaults
    deal_id = fields.pop("deal_id", None) or _generate_deal_id(fields["address"], db_path)
    now = datetime.now().isoformat(timespec="seconds")

    # Filter fields to valid Deal columns
    valid_cols = {f.name for f in dc_fields(Deal)}
    clean_fields = {k: v for k, v in fields.items() if k in valid_cols}

    deal = Deal(deal_id=deal_id, created_at=now, updated_at=now, **clean_fields)
    deal.status = _deal_status(deal)

    with _open(db_path) as conn:
        cols = ", ".join(f.name for f in dc_fields(Deal))
        placeholders = ", ".join("?" for _ in dc_fields(Deal))
        values = tuple(getattr(deal, f.name) for f in dc_fields(Deal))
        conn.execute(f"INSERT INTO deal_intake ({cols}) VALUES ({placeholders})", values)
    return deal


def get_deal(deal_id: str, db_path: Optional[Path] = None) -> Optional[Deal]:
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM deal_intake WHERE deal_id = ?", (deal_id,)
        ).fetchone()
    return _row_to_deal(row) if row else None


def update_deal(deal_id: str, db_path: Optional[Path] = None, **fields) -> Optional[Deal]:
    deal = get_deal(deal_id, db_path)
    if deal is None:
        return None
    valid_cols = {f.name for f in dc_fields(Deal)} - {"deal_id", "created_at"}
    for k, v in fields.items():
        if k in valid_cols:
            setattr(deal, k, v)
    deal.updated_at = datetime.now().isoformat(timespec="seconds")
    deal.status = _deal_status(deal)
    with _open(db_path) as conn:
        cols_set = ", ".join(f"{f.name} = ?" for f in dc_fields(Deal) if f.name != "deal_id")
        values = tuple(getattr(deal, f.name) for f in dc_fields(Deal) if f.name != "deal_id") + (deal_id,)
        conn.execute(f"UPDATE deal_intake SET {cols_set} WHERE deal_id = ?", values)
    return deal


def list_deals(status: Optional[str] = None, db_path: Optional[Path] = None) -> list[Deal]:
    with _open(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM deal_intake WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM deal_intake ORDER BY updated_at DESC"
            ).fetchall()
    return [_row_to_deal(r) for r in rows]


def analyze_ready_deals(db_path: Optional[Path] = None) -> list[Deal]:
    return list_deals(status="ready", db_path=db_path)


def mark_analyzed(deal_id: str, run_id: str, packet_path: str,
                  db_path: Optional[Path] = None) -> Optional[Deal]:
    """Update deal after a successful analyze run."""
    return update_deal(
        deal_id, db_path=db_path,
        status="analyzed",
        last_analyzed_run_id=run_id,
        last_packet_path=packet_path,
    )


# ──────────────────────────────────────────────
# PROPERTY OVERRIDES (T-611, DB-backed)
# ──────────────────────────────────────────────

@dataclass
class PropertyOverrideRow:
    address_normalized: str = ""
    beds: Optional[int] = None
    baths: Optional[float] = None
    annual_tax: Optional[float] = None
    prior_year_tax: Optional[float] = None
    market_rent: Optional[float] = None
    notes: str = ""
    updated_at: str = ""


_OVERRIDE_FIELDS = ("address_normalized", "beds", "baths", "annual_tax",
                    "prior_year_tax", "market_rent", "notes", "updated_at")


def _row_to_override(row: sqlite3.Row) -> PropertyOverrideRow:
    return PropertyOverrideRow(**{k: row[k] for k in _OVERRIDE_FIELDS if k in row.keys()})


def list_overrides(db_path: Optional[Path] = None) -> list[PropertyOverrideRow]:
    """Every Property_Overrides row, address-sorted."""
    with _open(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM property_overrides ORDER BY address_normalized"
        ).fetchall()
    return [_row_to_override(r) for r in rows]


def get_override(address_normalized: str,
                 db_path: Optional[Path] = None) -> Optional[PropertyOverrideRow]:
    key = (address_normalized or "").strip().lower()
    if not key:
        return None
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM property_overrides WHERE address_normalized = ?",
            (key,),
        ).fetchone()
    return _row_to_override(row) if row else None


def upsert_override(address_normalized: str,
                    db_path: Optional[Path] = None,
                    **fields) -> PropertyOverrideRow:
    """Insert or update a property override row. Empty values clear the field."""
    key = (address_normalized or "").strip().lower()
    if not key:
        raise ValueError("address_normalized is required")
    now = datetime.now().isoformat(timespec="seconds")
    row = PropertyOverrideRow(address_normalized=key, updated_at=now)
    for k, v in fields.items():
        if k in {f.name for f in dc_fields(PropertyOverrideRow)}:
            setattr(row, k, v)
    with _open(db_path) as conn:
        conn.execute("""
            INSERT INTO property_overrides
              (address_normalized, beds, baths, annual_tax, prior_year_tax,
               market_rent, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(address_normalized) DO UPDATE SET
              beds=excluded.beds, baths=excluded.baths,
              annual_tax=excluded.annual_tax,
              prior_year_tax=excluded.prior_year_tax,
              market_rent=excluded.market_rent,
              notes=excluded.notes, updated_at=excluded.updated_at
        """, (row.address_normalized, row.beds, row.baths, row.annual_tax,
              row.prior_year_tax, row.market_rent, row.notes, row.updated_at))
    return row


def delete_override(address_normalized: str,
                    db_path: Optional[Path] = None) -> bool:
    key = (address_normalized or "").strip().lower()
    if not key:
        return False
    with _open(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM property_overrides WHERE address_normalized = ?",
            (key,),
        )
        return cur.rowcount > 0


def migrate_overrides_from_xlsx(xlsx_path: Optional[Path] = None,
                                db_path: Optional[Path] = None) -> int:
    """One-time import of Property_Overrides from reference_data.xlsx into
    the DB. Runs ONLY when the DB property_overrides table is empty. Returns
    the number of rows imported. Idempotent: a second call is a no-op once
    the DB has rows.
    """
    with _open(db_path) as conn:
        existing = conn.execute(
            "SELECT COUNT(*) FROM property_overrides"
        ).fetchone()[0]
    if existing > 0:
        return 0
    # Late import so a missing openpyxl in some test contexts doesn't
    # explode the module import.
    try:
        from openpyxl import load_workbook
    except Exception:
        return 0
    if xlsx_path is None:
        # Default Joe path; lookup.py uses the same resolution.
        candidates = [
            PROJECT_ROOT / "output" / "projects" / "Joe Berlin" / "reference_data.xlsx",
            PROJECT_ROOT / "templates" / "property_analysis" / "reference_data_template.xlsx",
        ]
        xlsx_path = next((c for c in candidates if c.exists()), None)
    if not xlsx_path or not Path(xlsx_path).exists():
        return 0
    try:
        wb = load_workbook(xlsx_path, data_only=True)
    except Exception:
        return 0
    if "Property_Overrides" not in wb.sheetnames:
        return 0
    ws = wb["Property_Overrides"]
    imported = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 2:
            continue
        addr = row[1]
        if not isinstance(addr, str) or not addr.strip():
            continue
        if not any(c.isdigit() for c in addr[:6]):
            continue
        upsert_override(
            address_normalized=addr.strip().lower(),
            db_path=db_path,
            beds=int(row[2]) if len(row) > 2 and row[2] is not None else None,
            baths=float(row[3]) if len(row) > 3 and row[3] is not None else None,
            annual_tax=float(row[4]) if len(row) > 4 and row[4] is not None else None,
            prior_year_tax=float(row[5]) if len(row) > 5 and row[5] is not None else None,
            market_rent=float(row[6]) if len(row) > 6 and row[6] is not None else None,
            notes=row[8] if len(row) > 8 and isinstance(row[8], str) else "",
        )
        imported += 1
    return imported


# ──────────────────────────────────────────────
# THRESHOLDS (T-612, DB-backed)
# ──────────────────────────────────────────────

def get_thresholds(db_path: Optional[Path] = None) -> dict[str, float]:
    """Return every threshold as {name: value}. Missing keys fall through
    to THRESHOLD_DEFAULTS so the caller always gets a complete dict."""
    with _open(db_path) as conn:
        rows = conn.execute("SELECT name, value FROM thresholds").fetchall()
    out = dict(THRESHOLD_DEFAULTS)
    for r in rows:
        if r["name"] in THRESHOLD_NAMES:
            out[r["name"]] = float(r["value"])
    return out


def set_thresholds(updates: dict[str, float],
                   db_path: Optional[Path] = None) -> dict[str, float]:
    """Upsert one or more threshold values. Returns the full set after write."""
    now = datetime.now().isoformat(timespec="seconds")
    with _open(db_path) as conn:
        for name, value in updates.items():
            if name not in THRESHOLD_NAMES:
                continue
            if value is None:
                continue
            conn.execute("""
                INSERT INTO thresholds (name, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                  value=excluded.value, updated_at=excluded.updated_at
            """, (name, float(value), now))
    return get_thresholds(db_path)


def migrate_thresholds_from_xlsx(xlsx_path: Optional[Path] = None,
                                  db_path: Optional[Path] = None) -> int:
    """One-time import of the Thresholds tab from reference_data.xlsx. Runs
    only when the DB table is empty. Idempotent."""
    with _open(db_path) as conn:
        existing = conn.execute("SELECT COUNT(*) FROM thresholds").fetchone()[0]
    if existing > 0:
        return 0
    try:
        from openpyxl import load_workbook
    except Exception:
        return 0
    if xlsx_path is None:
        candidates = [
            PROJECT_ROOT / "output" / "projects" / "Joe Berlin" / "reference_data.xlsx",
            PROJECT_ROOT / "templates" / "property_analysis" / "reference_data_template.xlsx",
        ]
        xlsx_path = next((c for c in candidates if c.exists()), None)
    if not xlsx_path or not Path(xlsx_path).exists():
        return 0
    try:
        wb = load_workbook(xlsx_path, data_only=True)
    except Exception:
        return 0
    if "Thresholds" not in wb.sheetnames:
        return 0
    ws = wb["Thresholds"]
    updates: dict[str, float] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 3:
            continue
        name, val = row[1], row[2]
        if isinstance(name, str) and val is not None and name.strip() in THRESHOLD_NAMES:
            try:
                updates[name.strip()] = float(val)
            except (TypeError, ValueError):
                continue
    if updates:
        set_thresholds(updates, db_path=db_path)
    return len(updates)


# ──────────────────────────────────────────────
# INTERACTIVE CREATE (CLI helper)
# ──────────────────────────────────────────────

REQUIRED_PROMPTS = [
    ("address", "Property address", str),
    ("arv_base", "ARV (after-repair value, $)", float),
    ("rehab_budget", "Rehab budget ($, enter 0 if turnkey)", float),
    ("section8_status",
     f"Section 8 status (one of {', '.join(VALID_SECTION8)})", str),
]
OPTIONAL_PROMPTS = [
    ("purchase_price", "Purchase price ($, blank = max-bid mode)", float),
    ("beds_override", "Beds (blank = let pipeline resolve)", int),
    ("prior_year_tax",
     "Prior-year property tax ($, will be scaled +10% in model)", float),
    ("notes", "Notes (free text)", str),
]


def interactive_create(db_path: Optional[Path] = None) -> Deal:
    """Run a stdin/stdout prompt session to create a deal. Returns the
    persisted Deal. Used by --create-deal CLI flag."""
    print("\n=== Create Deal ===\n")
    fields: dict[str, Any] = {}
    for key, prompt, typ in REQUIRED_PROMPTS:
        while True:
            raw = input(f"  {prompt}: ").strip()
            if not raw:
                if key == "rehab_budget":
                    fields[key] = 0.0; break
                print("    (required — please enter a value)")
                continue
            try:
                if typ is float:
                    fields[key] = float(raw.replace(",", "").replace("$", ""))
                elif typ is int:
                    fields[key] = int(raw)
                else:
                    val = raw
                    if key == "section8_status" and val not in VALID_SECTION8:
                        print(f"    (must be one of {VALID_SECTION8})")
                        continue
                    fields[key] = val
                break
            except (ValueError, TypeError):
                print(f"    (invalid value for {typ.__name__})")

    print("\n  Optional fields (press Enter to skip):")
    for key, prompt, typ in OPTIONAL_PROMPTS:
        raw = input(f"    {prompt}: ").strip()
        if not raw:
            continue
        try:
            if typ is float:
                fields[key] = float(raw.replace(",", "").replace("$", ""))
            elif typ is int:
                fields[key] = int(raw)
            else:
                fields[key] = raw
        except (ValueError, TypeError):
            print(f"      (invalid value for {key}; skipping)")

    deal = create_deal(db_path=db_path, **fields)
    print(f"\n  Created deal: {deal.deal_id}")
    print(f"  Status:       {deal.status}")
    if deal.required_missing():
        print(f"  Missing:      {deal.required_missing()}")
    return deal


def format_deal_short(deal: Deal) -> str:
    """One-line summary for list display."""
    price = f"${deal.purchase_price:,.0f}" if deal.purchase_price else "—"
    arv = f"${deal.arv_base:,.0f}" if deal.arv_base else "—"
    return (
        f"  {deal.deal_id:<40}  {deal.status:<14}  "
        f"price={price:<11}  arv={arv:<11}  {deal.address[:50]}"
    )


# ──────────────────────────────────────────────
# SMOKE TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "deal_intake.db"
    schema_init(tmp)

    d1 = create_deal(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        arv_base=255000, rehab_budget=55000, section8_status="confirmed",
        purchase_price=130000, beds_override=3,
        db_path=tmp,
    )
    print(f"Created: {d1.deal_id} status={d1.status} ready={d1.is_ready}")

    d2 = create_deal(
        address="57 Lower Rd, Pittsburgh, PA 15215",
        section8_status="assumed",
        db_path=tmp,
    )
    print(f"Created: {d2.deal_id} status={d2.status} missing={d2.required_missing()}")

    deals = list_deals(db_path=tmp)
    print(f"\nAll deals ({len(deals)}):")
    for d in deals:
        print(format_deal_short(d))

    ready = analyze_ready_deals(db_path=tmp)
    print(f"\nReady deals ({len(ready)}):")
    for d in ready:
        print(format_deal_short(d))

    updated = update_deal(d2.deal_id, db_path=tmp,
                          arv_base=35000, rehab_budget=15000)
    print(f"\nAfter update of d2: status={updated.status} ready={updated.is_ready}")

    ready = analyze_ready_deals(db_path=tmp)
    print(f"Ready deals ({len(ready)}):")
    for d in ready:
        print(format_deal_short(d))
