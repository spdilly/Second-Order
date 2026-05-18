"""
HUD Fair Market Rent lookup.

Reads from the SQLite database built by build_fmr_db.py. Primary path is
SAFMR (ZIP-level, ~52K rows, available for ~24 designated metros including
Pittsburgh). Fallback path is county-level FMR (~4.7K rows, full US coverage).

Usage:
    from scripts.property_analysis.fmr import lookup_fmr

    result = lookup_fmr(zip_code="15215", beds=3)
    # -> FMRResult(rent=1670, source="SAFMR", area="Pittsburgh, PA HUD Metro FMR Area",
    #              fy=2026, confidence="high")

If the ZIP is not in the SAFMR table, pass state+county to fall back:
    result = lookup_fmr(zip_code="55555", beds=3, state="PA", county_name="Greene County")
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "templates" / "property_analysis" / "data" / "hud_fmr.db"

# Bedroom column mapping
_BED_COLS = {0: "fmr_0br", 1: "fmr_1br", 2: "fmr_2br", 3: "fmr_3br", 4: "fmr_4br"}


@dataclass
class FMRResult:
    """Result of an FMR lookup."""
    rent: int                    # Monthly rent in whole dollars
    source: str                  # "SAFMR" or "FMR" (county-level)
    area: str                    # HUD area name
    fy: int                      # Fiscal year
    confidence: str              # "high" for SAFMR, "medium" for county-level
    zip: Optional[str] = None
    hud_area_code: Optional[str] = None
    # Ambiguity tracking: if this ZIP appears in multiple HUD areas, the
    # alternates list captures the other candidates so the caller can
    # surface the ambiguity to the user.
    ambiguous: bool = False
    alternates: list[dict] = None  # list of {hud_area_code, hud_area_name, rent}

    def cite(self) -> str:
        """One-line citation for the report."""
        scope = "ZIP-level SAFMR" if self.source == "SAFMR" else "County-level FMR"
        base = f"HUD FY{self.fy} {scope}, {self.area}"
        if self.ambiguous and self.alternates:
            alt_count = len(self.alternates)
            base += f" (AMBIGUOUS: ZIP also maps to {alt_count} other HUD area{'s' if alt_count != 1 else ''})"
        return base


class FMRLookupError(Exception):
    pass


def _validate_beds(beds: int) -> str:
    if beds not in _BED_COLS:
        raise FMRLookupError(
            f"Unsupported bedroom count: {beds}. HUD FMR covers 0-4 BR only."
        )
    return _BED_COLS[beds]


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FMRLookupError(
            f"HUD FMR database not found at {DB_PATH}. "
            "Run: python -m scripts.property_analysis.build_fmr_db"
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def lookup_safmr(zip_code: str, beds: int,
                 hud_area_hint: Optional[str] = None,
                 county_hint: Optional[str] = None) -> Optional[FMRResult]:
    """ZIP-level SAFMR lookup. Returns None if ZIP not in SAFMR table.

    Per Sean's data audit (2026-05-17), 10,106 US ZIPs map to multiple HUD
    areas. When that happens, this function:
      1. If hud_area_hint or county_hint provided, filters to the match.
      2. Otherwise picks the highest-rent row (most generous Section 8 figure
         is typically the metro area, which is also usually the right one
         for urban properties) AND attaches the alternates so the caller
         can surface the ambiguity.

    Confidence drops to "medium" when ambiguous and no hint was provided.
    """
    col = _validate_beds(beds)
    zip_code = str(zip_code).strip().zfill(5)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT zip, hud_area_code, hud_area_name, {col} AS rent "
            "FROM safmr WHERE zip = ?",
            (zip_code,),
        ).fetchall()
    valid_rows = [r for r in rows if r["rent"] is not None]
    if not valid_rows:
        return None

    # Single match: no ambiguity, return directly with full confidence
    if len(valid_rows) == 1:
        r = valid_rows[0]
        return FMRResult(
            rent=int(r["rent"]),
            source="SAFMR",
            area=r["hud_area_name"],
            fy=2026,
            confidence="high",
            zip=r["zip"],
            hud_area_code=r["hud_area_code"],
            ambiguous=False,
            alternates=[],
        )

    # Multiple matches: try to disambiguate with hints
    chosen = None
    if hud_area_hint:
        chosen = next((r for r in valid_rows if r["hud_area_code"] == hud_area_hint), None)
    if chosen is None and county_hint:
        # Match county name appearing in hud_area_name (loose)
        county_lower = county_hint.lower().replace(" county", "").strip()
        chosen = next((r for r in valid_rows
                       if county_lower in r["hud_area_name"].lower()), None)

    if chosen is None:
        # No hint or no match — pick highest rent (typically metro area) and
        # FLAG as ambiguous so the caller surfaces it.
        chosen = max(valid_rows, key=lambda r: r["rent"])

    alternates = [
        {"hud_area_code": r["hud_area_code"],
         "hud_area_name": r["hud_area_name"],
         "rent": int(r["rent"])}
        for r in valid_rows if r["hud_area_code"] != chosen["hud_area_code"]
    ]

    return FMRResult(
        rent=int(chosen["rent"]),
        source="SAFMR",
        area=chosen["hud_area_name"],
        fy=2026,
        # Downgrade confidence when we had to pick from multiple without a hint
        confidence="medium" if not (hud_area_hint or county_hint) else "high",
        zip=chosen["zip"],
        hud_area_code=chosen["hud_area_code"],
        ambiguous=True,
        alternates=alternates,
    )


def lookup_county_fmr(state: str, county_name: str, beds: int) -> Optional[FMRResult]:
    """County-level FMR fallback. state is 2-letter (e.g., 'PA')."""
    col = _validate_beds(beds)
    state = state.upper().strip()
    # Try exact match first, then LIKE
    with _connect() as conn:
        row = conn.execute(
            f"SELECT stusps, countyname, hud_area_code, hud_area_name, {col} AS rent "
            "FROM fmr WHERE stusps = ? AND countyname = ? LIMIT 1",
            (state, county_name),
        ).fetchone()
        if row is None:
            row = conn.execute(
                f"SELECT stusps, countyname, hud_area_code, hud_area_name, {col} AS rent "
                "FROM fmr WHERE stusps = ? AND countyname LIKE ? LIMIT 1",
                (state, f"%{county_name}%"),
            ).fetchone()
    if row is None or row["rent"] is None:
        return None
    return FMRResult(
        rent=int(row["rent"]),
        source="FMR",
        area=row["hud_area_name"],
        fy=2026,
        confidence="medium",
        hud_area_code=row["hud_area_code"],
    )


def lookup_fmr(
    zip_code: str,
    beds: int,
    state: Optional[str] = None,
    county_name: Optional[str] = None,
) -> FMRResult:
    """
    Look up FMR with the SAFMR -> county fallback chain.

    Args:
        zip_code: 5-digit ZIP code (string or int, zero-padded automatically)
        beds: 0, 1, 2, 3, or 4 bedrooms
        state: 2-letter state code, required for county fallback
        county_name: county name (e.g., "Allegheny County"); also used to
                     disambiguate SAFMR matches when a ZIP maps to multiple
                     HUD areas.

    Raises:
        FMRLookupError: if neither lookup succeeds.
    """
    # Try ZIP first, passing county_hint so ambiguous ZIPs can disambiguate
    result = lookup_safmr(zip_code, beds, county_hint=county_name)
    if result is not None:
        return result

    # Fall back to county
    if state and county_name:
        result = lookup_county_fmr(state, county_name, beds)
        if result is not None:
            return result

    raise FMRLookupError(
        f"No FMR found for ZIP {zip_code} ({beds} BR). "
        f"SAFMR coverage is ~24 metros; for ZIPs outside SAFMR, pass state and county_name."
    )


if __name__ == "__main__":
    # Smoke test against 1417 S Canal St, Pittsburgh PA 15215
    for beds in (2, 3, 4):
        r = lookup_fmr("15215", beds, state="PA", county_name="Allegheny County")
        print(f"ZIP 15215, {beds} BR: ${r.rent}/mo via {r.source} ({r.cite()})")
