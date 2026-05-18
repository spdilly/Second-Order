"""
ZIP -> county / state lookup against the local geo.db.

Used by the SAFMR disambiguation chain when an address resolves to multiple
HUD areas. For Allegheny PA addresses, WPRDC provides the canonical county;
for everything else, this module is the deterministic resolver.

Data source: Census Bureau ZCTA-county relationship (2020 vintage). Built
locally via `python -m scripts.property_analysis.build_geo_db`.

Trust posture: read-only against a static SQLite. No network. No LLM.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "templates" / "property_analysis" / "data" / "geo.db"


@dataclass
class CountyInfo:
    """Dominant county for a ZIP, with alternates when ZIP spans counties."""
    zip: str
    county_fips: str
    county_name: str
    state_fips: str
    state: str                              # 2-letter abbreviation
    land_area_m2: int
    ambiguous: bool = False                 # True if ZIP spans multiple counties
    alternates: list[dict] = field(default_factory=list)


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"geo.db not found at {DB_PATH}. "
            "Run: python -m scripts.property_analysis.build_geo_db"
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def lookup_county(zip_code: str) -> Optional[CountyInfo]:
    """Return the dominant county for a ZIP. None if ZIP not found.

    When a ZIP spans multiple counties (common — ~15% of US ZIPs), this
    returns the county with the largest land overlap and flags
    `ambiguous=True` with the alternates listed.
    """
    if not zip_code:
        return None
    zip_code = str(zip_code).strip().zfill(5)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT zip, county_fips, county_name, state_fips, state, land_area_m2 "
            "FROM zip_county WHERE zip = ? "
            "ORDER BY land_area_m2 DESC",
            (zip_code,),
        ).fetchall()
    if not rows:
        return None
    dominant = rows[0]
    alternates = [
        {"county_fips": r["county_fips"], "county_name": r["county_name"],
         "state": r["state"], "land_area_m2": r["land_area_m2"]}
        for r in rows[1:]
    ]
    return CountyInfo(
        zip=dominant["zip"],
        county_fips=dominant["county_fips"],
        county_name=dominant["county_name"],
        state_fips=dominant["state_fips"],
        state=dominant["state"],
        land_area_m2=dominant["land_area_m2"],
        ambiguous=len(rows) > 1,
        alternates=alternates,
    )


def lookup_state(zip_code: str) -> Optional[str]:
    """Return the 2-letter state for a ZIP. Useful when address parsing
    misses the state but the ZIP is unambiguous."""
    info = lookup_county(zip_code)
    return info.state if info else None


if __name__ == "__main__":
    samples = [
        ("15215", "Allegheny County (PA)"),
        ("72202", "Pulaski County (AR)"),
        ("40601", "Franklin County (KY)"),
        ("19103", "Philadelphia County (PA)"),
        ("78701", "Travis County (TX)"),
        ("99999", "None"),
    ]
    print(f"{'ZIP':>5}  {'state':>5}  {'dominant county':<30}  ambig?  alts")
    for zip_code, expected in samples:
        info = lookup_county(zip_code)
        if info:
            print(f"{info.zip:>5}  {info.state:>5}  {info.county_name:<30}  "
                  f"{info.ambiguous!s:5}   {len(info.alternates)}")
        else:
            print(f"{zip_code:>5}     -   None                            -        -")
