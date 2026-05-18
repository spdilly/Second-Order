"""
Populate the SFH proforma template with a specific property's inputs (V2).

Reads the empty template, writes inputs to the named ranges on the Inputs tab,
and saves to a per-property output filename. Never touches calculation cells —
all model logic is in the template's formulas, which recalculate on Excel open.

V2 changes
----------
- Writes source-text fields (monthly_rent_source, property_tax_source, etc.)
  via their named ranges, same mechanism as numeric fields.
- Optionally writes the Sources tab provenance log when caller passes
  source_meta dict (called from analyze.py).

Usage:
    from scripts.property_analysis.populate import populate_proforma

    output_path = populate_proforma(
        inputs=Inputs(...),
        output_dir="output/projects/Joe Berlin",
        property_label="1417_S_Canal_St",
        sources_meta={"monthly_rent": {...}, ...}  # optional
    )
"""
from __future__ import annotations

import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from openpyxl import load_workbook

from scripts.property_analysis.compute import Inputs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "property_analysis" / "proforma_template.xlsx"


# Sources-tab row labels (must match tab_sources.INPUT_FIELDS labels).
SOURCES_INPUT_LABELS = {
    "address":             "Property Address",
    "zip":                 "ZIP Code",
    "beds":                "Bedrooms",
    "baths":               "Bathrooms",
    "purchase_price":      "Purchase Price",
    "arv":                 "ARV",
    "rehab_cost":          "Rehab Cost",
    "monthly_rent":        "Monthly Rent (underwriting)",
    "market_rent":         "Market Rent (comparable)",
    "property_tax_annual": "Property Tax (annual)",
    "insurance_annual":    "Insurance (annual)",
}

# Freshness section labels (must match tab_sources.FRESHNESS_FIELDS labels)
SOURCES_FRESHNESS_LABELS = {
    "hud_fmr":         "HUD FMR vintage",
    "hud_safmr":       "HUD SAFMR vintage",
    "geo":             "Geo (Census ZCTA-county)",
    "wprdc":           "WPRDC (Allegheny County)",
    "allegheny":       "Allegheny millage tax year",
    "rentcast":        "RentCast mode",
    "generated_at":    "Report generated at",
}


def populate_proforma(
    inputs: Inputs,
    output_dir: Path | str,
    property_label: str,
    template_path: Path | None = None,
    sources_meta: Optional[dict[str, dict[str, Any]]] = None,
    freshness_meta: Optional[dict[str, str]] = None,
    blockers: Optional[list[str]] = None,
) -> Path:
    """Copy the proforma template, write inputs and Sources tab, save.

    Args:
        inputs:          Fully-specified Inputs dataclass.
        output_dir:      Where to write the populated xlsx.
        property_label:  Slug for the filename (e.g. "1417_S_Canal_St").
        template_path:   Override the template location.
        sources_meta:    {field_name -> {"value":..., "source":..., "confidence":..., "note":...}}
                         If provided, Sources tab rows are written for each field.
        freshness_meta:  {key -> str} matching SOURCES_FRESHNESS_LABELS.
        blockers:        Optional list of blocker strings written to Sources tab.

    Returns:
        Path to the populated xlsx.
    """
    template_path = template_path or TEMPLATE_PATH
    if not template_path.exists():
        raise FileNotFoundError(
            f"Proforma template not found at {template_path}. "
            "Run: python -m financial_model_framework.templates.sfh_proforma.builder"
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{property_label}_proforma.xlsx"
    if output_path.exists():
        stamp = datetime.now().strftime("%H%M%S")
        output_path = output_dir / f"{property_label}_proforma_{stamp}.xlsx"
        counter = 1
        while output_path.exists():
            output_path = output_dir / f"{property_label}_proforma_{stamp}_{counter}.xlsx"
            counter += 1
    shutil.copyfile(template_path, output_path)

    wb = load_workbook(output_path)

    # ── Write inputs via named ranges ──
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

    name_to_addr: dict[str, tuple[str, int, int]] = {}
    for name in wb.defined_names:
        defn = wb.defined_names[name]
        attr = defn.attr_text
        if not attr:
            continue
        try:
            sheet_part, cell_part = attr.split("!", 1)
            sheet_name = sheet_part.strip("'")
            ref = cell_part.replace("$", "").split(":")[0]
            col_letter, row_num = coordinate_from_string(ref)
            col_idx = column_index_from_string(col_letter)
            name_to_addr[name] = (sheet_name, row_num, col_idx)
        except Exception:
            continue

    input_dict = asdict(inputs)
    for field_name, value in input_dict.items():
        if field_name not in name_to_addr:
            continue
        sheet, row, col = name_to_addr[field_name]
        wb[sheet].cell(row=row, column=col, value=value)

    # ── Write Sources tab if metadata provided ──
    if "Sources" in wb.sheetnames:
        ws = wb["Sources"]
        # Build a label -> row index map by scanning column B
        b_label_to_row: dict[str, int] = {}
        for r in range(1, ws.max_row + 1):
            v = ws.cell(row=r, column=2).value
            if isinstance(v, str):
                b_label_to_row[v] = r

        # Input provenance rows
        if sources_meta:
            for field, label in SOURCES_INPUT_LABELS.items():
                row = b_label_to_row.get(label)
                if row is None:
                    continue
                meta = sources_meta.get(field) or {}
                ws.cell(row=row, column=3, value=meta.get("value"))
                ws.cell(row=row, column=4, value=meta.get("source", ""))
                ws.cell(row=row, column=5, value=meta.get("confidence", ""))
                # Note: keep hint already in col F if not overriding
                note_val = meta.get("note", "")
                if note_val:
                    ws.cell(row=row, column=6, value=note_val)

        # Freshness rows
        if freshness_meta:
            for key, label in SOURCES_FRESHNESS_LABELS.items():
                row = b_label_to_row.get(label)
                if row is None:
                    continue
                val = freshness_meta.get(key)
                if val is None:
                    continue
                ws.cell(row=row, column=3, value=str(val))

        # Blockers (max 6 lines)
        if blockers:
            # Locate the blockers section: find row with "Blockers / Warnings"
            blockers_section_row = None
            for r in range(1, ws.max_row + 1):
                if ws.cell(row=r, column=2).value == "Blockers / Warnings":
                    blockers_section_row = r
                    break
            if blockers_section_row is not None:
                start = blockers_section_row + 1
                for i, msg in enumerate(blockers[:6]):
                    ws.cell(row=start + i, column=2, value=str(msg))

    wb.save(output_path)
    return output_path


if __name__ == "__main__":
    # Smoke test on 1417 S Canal St using Joe's existing assumptions
    inputs = Inputs(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        zip="15215",
        beds=3,
        purchase_price=130000,
        arv=255000,
        rehab_cost=55000,
        closing_cost_pct=0.07,
        holding_months=3,
        down_payment_pct=1.0,
        interest_rate=0.095,
        loan_term_yrs=30,
        refi_year=3,
        refi_ltv=0.75,
        refi_interest_rate=0.075,
        refi_term_yrs=30,
        refi_closing_cost_pct=0.04,
        monthly_rent=2500,
        rent_growth=0.04,
        vacancy_rate=0.04,
        management_pct=0.10,
        maintenance_pct=0.04,
        capex_reserve_pct=0.0,
        property_tax_annual=3249,
        insurance_annual=85 * 12,
        hoa_annual=0,
        utilities_annual=0,
        expense_growth=0.0,
        hold_period_yrs=10,
        exit_cap_rate=0.08,
        cost_of_sale_pct=0.01,
        # V2 additions
        market_rent=2300,
        appreciation_rate=0.05,
        wholesaler_fee=7000,
        inspection_fee=350,
        appraisal_fee=750,
        holding_utilities_monthly=500,
        gp_lp_split_lp=0.50,
        monthly_rent_source="Joe override: $2,500 (HUD FMR FY2026 would be $1,670)",
        market_rent_source="RentCast comparable (fixture mode)",
        property_tax_source="Joe override: $3,249",
        insurance_source="Manual estimate: $85/mo × 12",
        arv_source="Joe override / wholesaler quote",
        purchase_price_source="Wholesaler quote (off-market)",
        beds_source="Joe override",
    )
    sources_meta = {
        "address": {"value": inputs.address, "source": "prompt", "confidence": "high"},
        "zip": {"value": inputs.zip, "source": "parsed from address", "confidence": "high"},
        "beds": {"value": inputs.beds, "source": "override", "confidence": "high"},
        "baths": {"value": inputs.baths, "source": "default", "confidence": "low"},
        "purchase_price": {"value": inputs.purchase_price, "source": "wholesaler quote", "confidence": "high"},
        "arv": {"value": inputs.arv, "source": "Joe override", "confidence": "high"},
        "rehab_cost": {"value": inputs.rehab_cost, "source": "Joe estimate", "confidence": "medium"},
        "monthly_rent": {"value": inputs.monthly_rent, "source": "Joe override",
                         "confidence": "high",
                         "note": "Section 8 voucher; FMR-fallback would be $1,670 for 3BR ZCTA 15215."},
        "market_rent": {"value": inputs.market_rent, "source": "RentCast comp", "confidence": "medium"},
        "property_tax_annual": {"value": inputs.property_tax_annual, "source": "Joe override",
                                "confidence": "high"},
        "insurance_annual": {"value": inputs.insurance_annual, "source": "industry default",
                             "confidence": "medium"},
    }
    freshness_meta = {
        "hud_fmr": "FY2026",
        "geo": "Census 2020 ZCTA-county",
        "wprdc": "Allegheny County (live ArcGIS)",
        "allegheny": "TAX_YEAR=2025",
        "rentcast": "off",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    out = populate_proforma(
        inputs=inputs,
        output_dir=PROJECT_ROOT / "output" / "projects" / "Joe Berlin",
        property_label="1417_S_Canal_St_V2_SMOKE",
        sources_meta=sources_meta,
        freshness_meta=freshness_meta,
        blockers=[],
    )
    print(f"Populated: {out}")
    print(f"Size: {out.stat().st_size/1024:.1f} KB")
