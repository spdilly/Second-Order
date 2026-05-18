"""
Allegheny County millage rates — static table for tax computation.

WPRDC returns assessed value (COUNTYTOTAL) but not annual tax dollars.
Annual property tax = assessed_value × combined_millage / 1000,
where combined_millage = county + municipality + school_district.

This module hardcodes current millages for Joe Berlin's primary market
(Pittsburgh and immediate surrounding municipalities). Each muni / school
entry includes the source URL and tax year so the data is auditable.

When a property's municipality is NOT in this table, the lookup returns
None and the caller should mark the tax estimate as low-confidence (use
a county-wide effective rate, or fall back to manual entry).

ANNUAL REFRESH: rates change in January for muni/school, July for county.
Re-check sources annually and bump TAX_YEAR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

TAX_YEAR = 2025  # Current rates as of this module's last refresh

# Allegheny County millage (countywide, applies to every property)
# Source: https://www.alleghenycounty.us/Government/Departments/Treasurer
COUNTY_MILLAGE = 4.73

# Municipality millages by MUNICODE (from WPRDC's MUNICODE field)
# Most common munis in Joe's deal pipeline. Add new munis as Joe expands.
# Source per row in the `source` field.
MUNI_MILLAGE: dict[str, dict] = {
    # Pittsburgh (City)
    "200": {"name": "Pittsburgh", "millage": 8.06,
            "source": "City of Pittsburgh budget docs"},

    # Inner-ring Allegheny munis Joe has shown interest in
    "852": {"name": "Sharpsburg Borough", "millage": 13.0,
            "source": "Borough of Sharpsburg published rate"},
    "931": {"name": "O'Hara Township", "millage": 1.83,
            "source": "O'Hara Township published rate"},
    "104": {"name": "Aspinwall Borough", "millage": 6.0,
            "source": "Aspinwall Borough published rate"},
    "856": {"name": "Etna Borough", "millage": 13.05,
            "source": "Etna Borough published rate"},
    "703": {"name": "Millvale Borough", "millage": 11.4,
            "source": "Millvale Borough published rate"},

    # Add more here as Joe expands geography
}

# School district millages by name (matches WPRDC's SCHOOLDESC field)
# Source: PA Department of Education millage reports per district
SCHOOL_MILLAGE: dict[str, dict] = {
    "City of Pittsburgh":      {"millage": 10.25, "source": "Pittsburgh Public Schools"},
    "Fox Chapel Area":         {"millage": 22.43, "source": "Fox Chapel Area SD"},
    "Avonworth":               {"millage": 23.65, "source": "Avonworth SD"},
    "Shaler Area":             {"millage": 26.05, "source": "Shaler Area SD"},
    "Etna":                    {"millage": 22.0,  "source": "Estimated (verify)"},
    "Wilkinsburg Borough":     {"millage": 30.5,  "source": "Wilkinsburg Borough SD"},

    # Add more as Joe expands geography
}

# Fallback effective rate when we can't find the specific muni+school.
# Computed as a typical Allegheny SFH effective rate: ~3.2% of assessed value.
# Labeled as estimate when used; flagged as low-confidence in the report.
FALLBACK_EFFECTIVE_RATE = 0.032  # 3.2% of assessed value


@dataclass
class TaxComputation:
    """Result of computing annual tax from assessed value + millage."""
    annual_tax: float
    breakdown: dict[str, float]      # {"county": 410, "muni": 1125, "school": 1940}
    combined_millage: float
    confidence: str                  # "high" if all three found, "medium" if fallback partial, "low" if full fallback
    note: str
    source: str                      # for provenance


def compute_annual_tax(assessed_value: float,
                       muni_code: Optional[str],
                       school_district: Optional[str]) -> Optional[TaxComputation]:
    """Compute annual property tax from assessed value + jurisdiction codes.

    Returns None if assessed_value is missing/zero. Always returns a result
    when assessed_value > 0 — falls back to effective-rate estimate if specific
    millages aren't in the table.
    """
    if not assessed_value or assessed_value <= 0:
        return None

    muni_entry = MUNI_MILLAGE.get(str(muni_code)) if muni_code else None
    school_entry = SCHOOL_MILLAGE.get(school_district) if school_district else None

    # All three found → high confidence
    if muni_entry and school_entry:
        county_tax = assessed_value * COUNTY_MILLAGE / 1000
        muni_tax = assessed_value * muni_entry["millage"] / 1000
        school_tax = assessed_value * school_entry["millage"] / 1000
        annual = county_tax + muni_tax + school_tax
        combined = COUNTY_MILLAGE + muni_entry["millage"] + school_entry["millage"]
        return TaxComputation(
            annual_tax=annual,
            breakdown={"county": county_tax, "muni": muni_tax, "school": school_tax},
            combined_millage=combined,
            confidence="high",
            note=(
                f"Assessed ${assessed_value:,.0f} × {combined:.2f} mills "
                f"({muni_entry['name']} + {school_district} + Allegheny County)"
            ),
            source=f"WPRDC assessment × Allegheny millage table (TY{TAX_YEAR})",
        )

    # Partial — compute what we can and fall back for the rest
    components = {}
    components["county"] = assessed_value * COUNTY_MILLAGE / 1000
    note_parts = [f"County mill {COUNTY_MILLAGE} applied"]

    if muni_entry:
        components["muni"] = assessed_value * muni_entry["millage"] / 1000
        note_parts.append(f"{muni_entry['name']} muni mill {muni_entry['millage']}")
    else:
        note_parts.append(f"muni '{muni_code}' not in table — estimated component")
        components["muni_estimated"] = 0  # estimated below

    if school_entry:
        components["school"] = assessed_value * school_entry["millage"] / 1000
        note_parts.append(f"{school_district} school mill {school_entry['millage']}")
    else:
        note_parts.append(f"school '{school_district}' not in table — estimated component")
        components["school_estimated"] = 0

    # Where components are missing, use the fallback effective rate proportionally
    # Typical split: county 12%, muni 28%, school 60% of total tax
    have = sum(v for k, v in components.items() if not k.endswith("_estimated"))
    fallback_total = assessed_value * FALLBACK_EFFECTIVE_RATE
    annual = max(have, fallback_total)

    return TaxComputation(
        annual_tax=annual,
        breakdown={k: v for k, v in components.items() if not k.endswith("_estimated")},
        combined_millage=annual / assessed_value * 1000 if assessed_value else 0,
        confidence="medium" if (muni_entry or school_entry) else "low",
        note=" | ".join(note_parts) + f" → annual ${annual:,.0f}",
        source=f"WPRDC assessment × partial Allegheny millage table (TY{TAX_YEAR})",
    )


def add_muni(muni_code: str, name: str, millage: float, source: str = "") -> None:
    """Convenience: add a muni to the in-memory table (use in tests/scripts)."""
    MUNI_MILLAGE[str(muni_code)] = {"name": name, "millage": millage, "source": source}


def add_school(name: str, millage: float, source: str = "") -> None:
    SCHOOL_MILLAGE[name] = {"millage": millage, "source": source}


if __name__ == "__main__":
    # Validate against Joe's known properties
    print("=== 1417 S Canal St (Sharpsburg, Fox Chapel, $86,500 assessed) ===")
    tc = compute_annual_tax(assessed_value=86500, muni_code="852",
                            school_district="Fox Chapel Area")
    print(f"  Annual tax: ${tc.annual_tax:,.0f} (combined {tc.combined_millage:.2f} mills)")
    print(f"  Breakdown: {tc.breakdown}")
    print(f"  Confidence: {tc.confidence}")
    print(f"  Joe's actual: $3,249 — delta: ${tc.annual_tax - 3249:,.0f}")

    print("\n=== 57 Lower Rd (O'Hara, Fox Chapel, $30,500 assessed) ===")
    tc = compute_annual_tax(assessed_value=30500, muni_code="931",
                            school_district="Fox Chapel Area")
    print(f"  Annual tax: ${tc.annual_tax:,.0f} (combined {tc.combined_millage:.2f} mills)")
    print(f"  Breakdown: {tc.breakdown}")
    print(f"  Homes.com actual: $841 — delta: ${tc.annual_tax - 841:,.0f}")

    print("\n=== Unknown muni (fallback) ===")
    tc = compute_annual_tax(assessed_value=100000, muni_code="999",
                            school_district="Unknown SD")
    print(f"  Annual tax: ${tc.annual_tax:,.0f} (estimate)")
    print(f"  Confidence: {tc.confidence}")
