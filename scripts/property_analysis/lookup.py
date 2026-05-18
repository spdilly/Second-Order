"""
Lookup chain orchestrator.

Resolves each data point (rent, market_rent, tax, beds) by gathering attempts
from every available source, then handing the list to provenance.resolve() to
pick a winner with proper confidence and reconciliation.

Source priority chain per field:
  monthly_rent (Section 8 underwriting rent):
    Property_Overrides.market_rent -> HUD SAFMR -> HUD county FMR -> manual
  market_rent (open-market sanity check, separate from underwriting):
    RentCast (when integrated) -> Property_Overrides (rare) -> none
  property_tax_annual:
    Property_Overrides.annual_tax -> Property_Overrides.prior_year_tax*1.10
    -> Realtor.com (Phase 2) -> manual
  beds:
    prompt -> Property_Overrides -> RentCast property records (Phase 2) -> default 3
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook

from scripts.property_analysis.fmr import lookup_fmr, FMRResult, FMRLookupError
from scripts.property_analysis.normalize import ParsedAddress
from scripts.property_analysis.provenance import (
    Provenance, Attempt, resolve, single, manual_required,
)
from scripts.property_analysis.rentcast import (
    RentCastClient, PropertyRecord, RentEstimate, get_client,
)
from scripts.property_analysis.wprdc import (
    WPRDCClient, ParcelLookup, ParcelDetails, get_client as get_wprdc_client,
)
from scripts.property_analysis.allegheny_millages import compute_annual_tax

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DATA_PATH = PROJECT_ROOT / "output" / "projects" / "Joe Berlin" / "reference_data.xlsx"
REFERENCE_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "property_analysis" / "reference_data_template.xlsx"


@dataclass
class Lookup:
    """A resolved value with provenance."""
    value: Optional[float | int | str]
    source: str
    confidence: str    # "high", "medium", "low", "manual"
    note: str = ""

    @property
    def found(self) -> bool:
        return self.value is not None


@dataclass
class PropertyOverride:
    address_normalized: str
    beds: Optional[int] = None
    baths: Optional[float] = None
    annual_tax: Optional[float] = None
    prior_year_tax: Optional[float] = None
    market_rent: Optional[float] = None
    notes: str = ""


@dataclass
class ThresholdSet:
    cap_rate_min: float = 0.08
    cash_on_cash_min: float = 0.10
    dscr_min: float = 1.25
    irr_min: float = 0.15
    max_price_to_arv: float = 0.75
    vacancy_default: float = 0.04
    rent_growth_default: float = 0.03
    expense_growth_default: float = 0.03


def _reference_path() -> Path:
    """Return Joe's reference file if it exists, else the bundled template."""
    if REFERENCE_DATA_PATH.exists():
        return REFERENCE_DATA_PATH
    return REFERENCE_TEMPLATE_PATH


def load_property_overrides() -> list[PropertyOverride]:
    """Property_Overrides authority chain (T-611):

      DB (deal_intake.property_overrides)  -- writable, primary
        -> on first call when DB is empty, one-time migration from
           reference_data.xlsx (if it exists)
        -> if both DB and migration produce nothing, fall back to reading
           the xlsx directly so the V1 behavior never regresses

    The DB is authoritative once anything has been written there. The xlsx
    is kept readable as a transitional path and as a backup format.
    """
    # Late import: keep lookup.py importable in contexts where the intake
    # module hasn't been initialized yet (e.g., the offline build_fmr_db
    # script).
    try:
        from scripts.property_analysis import intake as _intake
    except Exception:
        _intake = None  # type: ignore

    if _intake is not None:
        try:
            _intake.schema_init()
            db_rows = _intake.list_overrides()
            if not db_rows:
                # One-time migration: import the xlsx if the table is empty.
                # No-op if no xlsx is on disk.
                _intake.migrate_overrides_from_xlsx()
                db_rows = _intake.list_overrides()
            if db_rows:
                return [
                    PropertyOverride(
                        address_normalized=r.address_normalized,
                        beds=r.beds,
                        baths=r.baths,
                        annual_tax=r.annual_tax,
                        prior_year_tax=r.prior_year_tax,
                        market_rent=r.market_rent,
                        notes=r.notes or "",
                    )
                    for r in db_rows
                ]
        except Exception:
            # If the DB layer is unavailable for any reason, fall through
            # to the xlsx path rather than silently returning [].
            pass

    # Fallback: V1 behavior reading the xlsx directly.
    path = _reference_path()
    if not path.exists():
        return []
    wb = load_workbook(path, data_only=True)
    if "Property_Overrides" not in wb.sheetnames:
        return []
    ws = wb["Property_Overrides"]
    out: list[PropertyOverride] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 2:
            continue
        addr = row[1]
        if not isinstance(addr, str) or not addr.strip():
            continue
        if not any(c.isdigit() for c in addr[:6]):
            continue
        out.append(PropertyOverride(
            address_normalized=addr.strip().lower(),
            beds=int(row[2]) if len(row) > 2 and row[2] is not None else None,
            baths=float(row[3]) if len(row) > 3 and row[3] is not None else None,
            annual_tax=float(row[4]) if len(row) > 4 and row[4] is not None else None,
            prior_year_tax=float(row[5]) if len(row) > 5 and row[5] is not None else None,
            market_rent=float(row[6]) if len(row) > 6 and row[6] is not None else None,
            notes=row[8] if len(row) > 8 and isinstance(row[8], str) else "",
        ))
    return out


def load_thresholds() -> ThresholdSet:
    """Read Joe's threshold values from the Thresholds tab."""
    path = _reference_path()
    t = ThresholdSet()
    if not path.exists():
        return t
    wb = load_workbook(path, data_only=True)
    if "Thresholds" not in wb.sheetnames:
        return t
    ws = wb["Thresholds"]
    by_name = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 3:
            continue
        name, val = row[1], row[2]
        if isinstance(name, str) and val is not None:
            by_name[name.strip()] = val

    for field in (
        "cap_rate_min", "cash_on_cash_min", "dscr_min", "irr_min",
        "max_price_to_arv", "vacancy_default",
        "rent_growth_default", "expense_growth_default",
    ):
        if field in by_name:
            setattr(t, field, float(by_name[field]))
    return t


def find_override(addr: ParsedAddress,
                  overrides: list[PropertyOverride]) -> Optional[PropertyOverride]:
    """Match a parsed address against the Property_Overrides table."""
    target = addr.normalized.lower()
    for ov in overrides:
        if ov.address_normalized == target:
            return ov
        # Loose match: just street + zip
        if addr.zip and ov.address_normalized.endswith(addr.zip) and \
           ov.address_normalized.startswith(addr.street_only):
            return ov
    return None


def fetch_wprdc_data(addr: ParsedAddress,
                     client: Optional[WPRDCClient] = None
                     ) -> tuple[ParcelLookup, Optional[ParcelDetails]]:
    """Fetch Allegheny parcel ID + WPRDC details for an address.

    Returns (lookup, details). Lookup always present; details None if not
    found or not in Allegheny.
    """
    if client is None:
        client = get_wprdc_client()
    return client.fetch_for_address(addr)


def fetch_rentcast_data(addr: ParsedAddress,
                        client: Optional[RentCastClient] = None,
                        beds: Optional[int] = None) -> tuple[Optional[PropertyRecord], Optional[RentEstimate]]:
    """Fetch property record + rent estimate from RentCast (any mode).

    Returns (PropertyRecord or None, RentEstimate or None).
    """
    if client is None:
        client = get_client()
    if client.mode == "off":
        return None, None
    address_str = addr.raw if addr.raw else (
        f"{addr.street_only}, {addr.city or ''}, {addr.state or ''} {addr.zip or ''}"
    )
    rec = client.property_records(address_str)
    est = client.rent_estimate(
        address_str,
        beds=beds if beds is not None else (rec.beds if rec else None),
    )
    return rec, est


def resolve_rent(addr: ParsedAddress, beds: int,
                 override: Optional[PropertyOverride] = None,
                 county_hint: Optional[str] = None) -> Provenance:
    """Underwriting rent: Property_Overrides -> HUD SAFMR -> HUD county FMR -> manual.

    county_hint disambiguates ZIPs that map to multiple HUD areas (e.g.,
    rural ZIPs spanning a metro boundary). For Allegheny addresses, pass
    the WPRDC-derived municipality / county; otherwise pass parsed county
    from the address if known.
    """
    attempts: list[Attempt] = []

    # 1. Override
    if override and override.market_rent:
        attempts.append(Attempt(
            source="Property_Overrides",
            value=float(override.market_rent),
            succeeded=False,
            note=f"Manual rent override for {addr.normalized}",
        ))
    else:
        attempts.append(Attempt(
            source="Property_Overrides",
            value=None, succeeded=False,
            note="no rent override for this address",
        ))

    # 2. HUD FMR chain. fmr.source is "SAFMR" or "FMR" — prefix with "HUD " explicitly.
    if addr.zip:
        try:
            fmr = lookup_fmr(
                zip_code=addr.zip, beds=beds,
                state=addr.state, county_name=county_hint,
            )
            source_label = _hud_source_label(fmr.source)
            note = fmr.cite()
            # When ambiguous, append a clear ambiguity reconciliation note
            if fmr.ambiguous and fmr.alternates:
                alt_summary = "; ".join(
                    f"{a['hud_area_name']} (${a['rent']})"
                    for a in fmr.alternates[:3]
                )
                note += (
                    f" — ZIP also matches: {alt_summary}"
                    f"{' (and others)' if len(fmr.alternates) > 3 else ''}. "
                    "Consider passing --county to disambiguate."
                )
            attempts.append(Attempt(
                source=source_label,
                value=float(fmr.rent), succeeded=False,
                note=note,
            ))
        except FMRLookupError as e:
            attempts.append(Attempt(
                source="HUD SAFMR", value=None, succeeded=False,
                note=str(e),
            ))
    else:
        attempts.append(Attempt(
            source="HUD SAFMR", value=None, succeeded=False,
            note="No ZIP parsed from address",
        ))

    return resolve("monthly_rent", attempts)


def _hud_source_label(fmr_source: str) -> str:
    """Map fmr.py's 'SAFMR' / 'FMR' tokens to the report-facing 'HUD SAFMR' /
    'HUD FMR' labels. Idempotent — does not double-prefix if already labeled.
    """
    if fmr_source.startswith("HUD "):
        return fmr_source
    if fmr_source == "SAFMR":
        return "HUD SAFMR"
    if fmr_source == "FMR":
        return "HUD FMR"
    return f"HUD {fmr_source}"


def resolve_market_rent(addr: ParsedAddress, beds: int,
                        override: Optional[PropertyOverride] = None,
                        rentcast_estimate: Optional[RentEstimate] = None) -> Provenance:
    """Open-market rent (separate from underwriting rent — used for variance flag).

    Sources: RentCast estimate -> none.
    """
    attempts: list[Attempt] = []
    if rentcast_estimate and rentcast_estimate.rent:
        note_parts = [f"market rent estimate"]
        if rentcast_estimate.rent_range_low and rentcast_estimate.rent_range_high:
            note_parts.append(
                f"range ${rentcast_estimate.rent_range_low:,.0f}-${rentcast_estimate.rent_range_high:,.0f}"
            )
        if rentcast_estimate.comparables:
            note_parts.append(f"{len(rentcast_estimate.comparables)} comps")
        attempts.append(Attempt(
            source="RentCast",
            value=float(rentcast_estimate.rent),
            succeeded=False,
            note="; ".join(note_parts),
        ))
    else:
        attempts.append(Attempt(
            source="RentCast", value=None, succeeded=False,
            note="RentCast adapter unavailable or returned no rent estimate",
        ))

    p = resolve("market_rent", attempts)
    if p.value is None:
        p.confidence = "low"
        p.note = "Market rent estimate unavailable. Variance check skipped."
    return p


def compute_rent_variance(underwriting_rent: float,
                          market_rent: Optional[float]) -> Optional[dict]:
    """Compare underwriting rent (e.g. HUD SAFMR) to market rent (RentCast).

    Returns a dict with the delta, percentage, and a flag:
      - "aligned"  : within 5%
      - "moderate" : 5-15% gap
      - "high"     : >15% gap
    """
    if not market_rent or not underwriting_rent:
        return None
    delta = underwriting_rent - market_rent
    pct = delta / market_rent if market_rent else 0
    if abs(pct) <= 0.05:
        flag = "aligned"
        msg = f"Underwriting rent and market rent within 5% (${delta:,.0f} delta)."
    elif abs(pct) <= 0.15:
        flag = "moderate"
        msg = (
            f"Underwriting rent is {abs(pct):.1%} "
            f"{'above' if delta > 0 else 'below'} market. "
            f"Moderate variance — confirm Section 8 occupancy supports the FMR."
        )
    else:
        flag = "high"
        msg = (
            f"Underwriting rent is {abs(pct):.1%} "
            f"{'above' if delta > 0 else 'below'} market. "
            f"High variance — Section 8 status is load-bearing for the deal."
        )
    return {
        "underwriting_rent": underwriting_rent,
        "market_rent": market_rent,
        "delta": delta,
        "pct": pct,
        "flag": flag,
        "message": msg,
    }


def resolve_tax(addr: ParsedAddress,
                override: Optional[PropertyOverride] = None,
                rentcast_record: Optional[PropertyRecord] = None,
                wprdc_details: Optional[ParcelDetails] = None) -> Provenance:
    """Property tax priority:
       Override -> prior_year*1.10 -> WPRDC assessed×millage -> RentCast -> manual.
    """
    attempts: list[Attempt] = []

    if override and override.annual_tax:
        attempts.append(Attempt(
            source="Property_Overrides", value=float(override.annual_tax),
            succeeded=False, note="explicit annual tax override",
        ))
    elif override and override.prior_year_tax:
        attempts.append(Attempt(
            source="Property_Overrides",
            value=float(override.prior_year_tax) * 1.10,
            succeeded=False,
            note=f"prior year ${override.prior_year_tax:,.0f} × 1.10",
        ))
    else:
        attempts.append(Attempt(
            source="Property_Overrides", value=None, succeeded=False,
            note="no tax data in overrides for this address",
        ))

    # WPRDC Allegheny: assessed value × millage table
    if wprdc_details and wprdc_details.assessed_total:
        tc = compute_annual_tax(
            assessed_value=wprdc_details.assessed_total,
            muni_code=wprdc_details.muni_code,
            school_district=wprdc_details.school_district,
        )
        if tc:
            attempts.append(Attempt(
                source="WPRDC (Allegheny)", value=float(tc.annual_tax),
                succeeded=False, note=tc.note,
            ))
    else:
        attempts.append(Attempt(
            source="WPRDC (Allegheny)", value=None, succeeded=False,
            note="no WPRDC parcel data (out of Allegheny or address not matched)",
        ))

    # RentCast tax assessment (prior year tax × 1.10 per Joe's rule)
    if rentcast_record and rentcast_record.tax_assessment_annual:
        attempts.append(Attempt(
            source="RentCast",
            value=float(rentcast_record.tax_assessment_annual) * 1.10,
            succeeded=False,
            note=f"RentCast tax history × 1.10 (prior year ${rentcast_record.tax_assessment_annual:,.0f})",
        ))
    else:
        attempts.append(Attempt(
            source="RentCast", value=None, succeeded=False,
            note="no tax data from RentCast property records",
        ))

    return resolve("property_tax_annual", attempts)


def resolve_beds(addr: ParsedAddress,
                 override: Optional[PropertyOverride] = None,
                 explicit: Optional[int] = None,
                 rentcast_record: Optional[PropertyRecord] = None,
                 wprdc_details: Optional[ParcelDetails] = None) -> Provenance:
    """Beds: prompt -> Property_Overrides -> WPRDC -> RentCast -> default 3."""
    attempts: list[Attempt] = []

    if explicit is not None:
        attempts.append(Attempt(
            source="prompt", value=int(explicit),
            succeeded=False, note=f"{explicit} BR from prompt",
        ))
    if override and override.beds:
        attempts.append(Attempt(
            source="Property_Overrides", value=int(override.beds),
            succeeded=False, note=f"{override.beds} BR from overrides",
        ))
    if wprdc_details and wprdc_details.beds:
        attempts.append(Attempt(
            source="WPRDC (Allegheny)", value=int(wprdc_details.beds),
            succeeded=False,
            note=f"{wprdc_details.beds} BR from Allegheny County assessment record",
        ))
    if rentcast_record and rentcast_record.beds:
        attempts.append(Attempt(
            source="RentCast", value=int(rentcast_record.beds),
            succeeded=False, note=f"{rentcast_record.beds} BR from RentCast property records",
        ))

    p = resolve("beds", attempts, default=3, default_confidence="low")
    if p.source == "default":
        p.note = (
            "Bedroom count not provided and no data source returned a value. "
            "Defaulting to 3 BR — pass --beds for accuracy."
        )
    return p


# ──────────────────────────────────────────────
# Backward-compat shims (analyze.py currently uses these)
# ──────────────────────────────────────────────

def lookup_rent(addr: ParsedAddress, beds: int,
                override: Optional[PropertyOverride] = None) -> Lookup:
    p = resolve_rent(addr, beds, override)
    return Lookup(
        value=p.value, source=p.source, confidence=p.confidence,
        note=p.note or p.reconciliation or "",
    )


def lookup_tax(addr: ParsedAddress,
               override: Optional[PropertyOverride] = None) -> Lookup:
    p = resolve_tax(addr, override)
    return Lookup(
        value=p.value, source=p.source, confidence=p.confidence,
        note=p.note or p.reconciliation or "",
    )


def lookup_beds(addr: ParsedAddress,
                override: Optional[PropertyOverride] = None,
                explicit: Optional[int] = None) -> Lookup:
    p = resolve_beds(addr, override, explicit)
    return Lookup(
        value=p.value, source=p.source, confidence=p.confidence,
        note=p.note,
    )


if __name__ == "__main__":
    from scripts.property_analysis.normalize import parse_address

    overrides = load_property_overrides()
    print(f"Loaded {len(overrides)} property overrides")
    for ov in overrides[:3]:
        print(f"  {ov.address_normalized}: beds={ov.beds}, tax=${ov.annual_tax}, rent=${ov.market_rent}")

    thresh = load_thresholds()
    print(f"\nThresholds: cap_rate={thresh.cap_rate_min}, dscr={thresh.dscr_min}, irr={thresh.irr_min}")

    addr = parse_address("1417 S Canal St, Pittsburgh PA 15215")
    override = find_override(addr, overrides)
    print(f"\nMatch for {addr.normalized}: {'found' if override else 'none'}")

    beds_lk = lookup_beds(addr, override=override)
    print(f"Beds:  {beds_lk.value} ({beds_lk.source}) — {beds_lk.note}")
    rent_lk = lookup_rent(addr, beds=beds_lk.value, override=override)
    print(f"Rent:  ${rent_lk.value} ({rent_lk.source}) — {rent_lk.note}")
    tax_lk = lookup_tax(addr, override=override)
    print(f"Tax:   ${tax_lk.value} ({tax_lk.source}) — {tax_lk.note}")
