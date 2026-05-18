"""
Build the reference data Excel template that Joe maintains.

Three tabs:
  - Property_Overrides — manual overrides per address (tax, rent, beds)
  - Markets — known markets and ZIP coverage
  - Thresholds — Joe's buy/no-buy criteria

The skill reads this file at every run. Joe can edit it in Excel anytime.

Run:
    python -m scripts.property_analysis.build_reference_template

Output:
    templates/property_analysis/reference_data_template.xlsx
"""
from __future__ import annotations

import sys
from pathlib import Path

FRAMEWORK_ROOT = Path(__file__).resolve().parents[2] / "financial_model_framework"
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK_ROOT))

from openpyxl import Workbook

from core.styles import (
    Fonts, NumberFormats, Alignments, Colors,
    apply_header_style, apply_data_style, apply_input_style,
)
from core.layout import (
    create_sheet, freeze_panes, set_column_widths,
    write_sheet_title, write_section_title,
)
from core.print_setup import apply_print_setup


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = PROJECT_ROOT / "templates" / "property_analysis" / "reference_data_template.xlsx"


def build_property_overrides_tab(wb: Workbook):
    ws = create_sheet(wb, "Property_Overrides", tab_role="input", zoom=100)
    set_column_widths(ws, {
        "A": 2, "B": 45, "C": 8, "D": 8,
        "E": 14, "F": 16, "G": 14, "H": 14, "I": 30,
    })

    row = write_sheet_title(ws, "Property Overrides", row=1, col=2)

    sub = ws.cell(row=row, column=2,
                  value="Specific properties Joe has researched. Highest priority — beats market estimates.")
    sub.font = Fonts.SUBTITLE
    row += 2

    headers = [
        "Address (normalized)", "Beds", "Baths",
        "Annual Tax ($)", "Prior Year Tax ($)",
        "Market Rent ($/mo)", "Last Updated", "Notes",
    ]
    for i, h in enumerate(headers):
        apply_header_style(ws.cell(row=row, column=2 + i, value=h))
    header_row = row
    row += 1

    # Sample rows
    samples = [
        ["1417 s canal st, pittsburgh, pa, 15215", 3, 1.0, 3249, 2954, 2500, "2024-03-01", "Joe's existing analysis"],
        ["", "", "", "", "", "", "", "Add your own rows below this line"],
    ]
    for s in samples:
        for i, v in enumerate(s):
            cell = ws.cell(row=row, column=2 + i, value=v if v != "" else None)
            apply_input_style(cell)
            if i == 0 or i == 7:
                cell.alignment = Alignments.LEFT
            if i in (3, 4, 5):
                cell.number_format = NumberFormats.USD
            elif i == 2:
                cell.number_format = NumberFormats.DECIMAL_1
            elif i == 1:
                cell.number_format = NumberFormats.INTEGER
        row += 1

    freeze_panes(ws, row=header_row + 1, col=2)
    return ws


def build_markets_tab(wb: Workbook):
    ws = create_sheet(wb, "Markets", tab_role="input", zoom=100)
    set_column_widths(ws, {
        "A": 2, "B": 6, "C": 24, "D": 22, "E": 36, "F": 12, "G": 30,
    })

    row = write_sheet_title(ws, "Markets — Joe's Target Areas", row=1, col=2)

    sub = ws.cell(row=row, column=2,
                  value="Informational. Helps the skill confirm it knows the market and applies the right county-level fallback.")
    sub.font = Fonts.SUBTITLE
    row += 2

    headers = ["State", "County", "City", "ZIP Codes (comma-sep)", "Preferred", "Notes"]
    for i, h in enumerate(headers):
        apply_header_style(ws.cell(row=row, column=2 + i, value=h))
    header_row = row
    row += 1

    # Pre-populate with Joe's known Pittsburgh focus
    samples = [
        ["PA", "Allegheny County", "Pittsburgh",
         "15201, 15202, 15203, 15204, 15205, 15206, 15207, 15208, 15209, 15210, 15212, 15213, 15214, 15215, 15216, 15217, 15218, 15219, 15220, 15221, 15222, 15223, 15224, 15226, 15227, 15228, 15229, 15232, 15233, 15234, 15235, 15236, 15237, 15238, 15239, 15243",
         "Y", "Primary market"],
        ["PA", "Beaver County", "", "", "Y", "Surrounding"],
        ["PA", "Westmoreland County", "", "", "Y", "Surrounding"],
        ["", "", "", "", "", "Add your own rows below"],
    ]
    for s in samples:
        for i, v in enumerate(s):
            cell = ws.cell(row=row, column=2 + i, value=v if v != "" else None)
            apply_input_style(cell)
            cell.alignment = Alignments.LEFT
        row += 1

    freeze_panes(ws, row=header_row + 1, col=2)
    return ws


def build_thresholds_tab(wb: Workbook):
    ws = create_sheet(wb, "Thresholds", tab_role="input", zoom=100)
    set_column_widths(ws, {
        "A": 2, "B": 32, "C": 16, "D": 12, "E": 50,
    })

    row = write_sheet_title(ws, "Decision Thresholds", row=1, col=2)

    sub = ws.cell(row=row, column=2,
                  value="Joe's buy/no-buy criteria. The skill will apply these to every property unless overridden in the prompt.")
    sub.font = Fonts.SUBTITLE
    row += 2

    headers = ["Metric", "Min Value", "Format", "Notes"]
    for i, h in enumerate(headers):
        apply_header_style(ws.cell(row=row, column=2 + i, value=h))
    header_row = row
    row += 1

    # Section 8 SFH defaults — Joe can adjust
    rows = [
        ("cap_rate_min",      0.08, "PCT", "Year 1 Cap Rate (NOI / cash invested) floor"),
        ("cash_on_cash_min",  0.10, "PCT", "Year 1 Cash-on-Cash floor"),
        ("dscr_min",          1.25, "RATIO", "Minimum DSCR across operating years"),
        ("irr_min",           0.15, "PCT", "Levered IRR over the hold"),
        ("max_price_to_arv",  0.75, "PCT", "Max purchase as a % of ARV (BRRRR rule of thumb)"),
        ("vacancy_default",   0.04, "PCT", "Section 8 vacancy assumption (override per deal if needed)"),
        ("rent_growth_default", 0.03, "PCT", "Default annual rent growth (HUD FMR historical)"),
        ("expense_growth_default", 0.03, "PCT", "Default annual property tax + insurance growth"),
    ]
    for metric, value, fmt, note in rows:
        ws.cell(row=row, column=2, value=metric).font = Fonts.DATA_BOLD
        v_cell = ws.cell(row=row, column=3, value=value)
        apply_input_style(v_cell)
        if fmt == "PCT":
            v_cell.number_format = NumberFormats.PCT_1
        elif fmt == "RATIO":
            v_cell.number_format = NumberFormats.MULTIPLE
        ws.cell(row=row, column=4, value=fmt).font = Fonts.DATA
        note_cell = ws.cell(row=row, column=5, value=note)
        note_cell.font = Fonts.SMALL_NOTE
        note_cell.alignment = Alignments.LEFT
        row += 1

    freeze_panes(ws, row=header_row + 1, col=2)
    return ws


def build_notes_tab(wb: Workbook):
    """Brief usage instructions for Joe."""
    ws = create_sheet(wb, "Notes", tab_role="util", zoom=100)
    set_column_widths(ws, {"A": 2, "B": 90})

    row = write_sheet_title(ws, "How to Maintain This File", row=1, col=2)

    notes = [
        "",
        "PROPERTY_OVERRIDES",
        "Add a row per property you have researched. The skill checks this tab first for tax,",
        "rent, and bedroom count. Use the normalized address format (lowercase, abbreviated",
        "street suffixes) — the skill normalizes input addresses to match.",
        "",
        "Tip: After running an analysis on a new property, copy the address and computed",
        "    inputs into Property_Overrides so the skill skips the web fetch next time.",
        "",
        "",
        "MARKETS",
        "Lists the counties and ZIPs Joe focuses on. The skill uses this to validate it knows",
        "the area. Adding new markets is optional — the skill will work for any US address,",
        "but pre-listing your target markets makes the report more informative.",
        "",
        "",
        "THRESHOLDS",
        "Your buy/no-buy criteria. Every analysis is scored against these. Adjust freely.",
        "If you want different thresholds for different property types (e.g., higher cap rate",
        "for distressed properties), the v1 skill applies one set globally. Future versions",
        "can support per-deal overrides via the prompt.",
        "",
        "",
        "ANNUAL REFRESH",
        "HUD publishes new Fair Market Rents each October. To refresh:",
        "  1. Download new FY xlsx files from huduser.gov/portal/datasets/fmr.html",
        "  2. Replace the files in templates/property_analysis/data/",
        "  3. Run: python -m scripts.property_analysis.build_fmr_db",
        "",
        "Property tax data refreshes on demand (the skill pulls Realtor.com per property).",
    ]
    for line in notes:
        cell = ws.cell(row=row, column=2, value=line)
        if line and line.isupper():
            cell.font = Fonts.SECTION_TITLE
        else:
            cell.font = Fonts.DATA
        cell.alignment = Alignments.LEFT
        row += 1

    freeze_panes(ws, row=3, col=2)
    return ws


def build_reference_template(output_path: Path | None = None) -> Path:
    output_path = output_path or OUTPUT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    default_ws = wb.active
    wb.remove(default_ws)

    overrides_ws  = build_property_overrides_tab(wb)
    markets_ws    = build_markets_tab(wb)
    thresholds_ws = build_thresholds_tab(wb)
    notes_ws      = build_notes_tab(wb)

    # Order: Notes first (landing), then the editable tabs
    wb.move_sheet("Notes", offset=-3)
    wb.active = wb.sheetnames.index("Notes")

    company_name = "Joe Berlin — Property Analysis"
    apply_print_setup(overrides_ws,  tab_name="Property Overrides", company_name=company_name, freeze_row=4)
    apply_print_setup(markets_ws,    tab_name="Markets",            company_name=company_name, freeze_row=4)
    apply_print_setup(thresholds_ws, tab_name="Thresholds",         company_name=company_name, freeze_row=4)
    apply_print_setup(notes_ws,      tab_name="Notes",              company_name=company_name, freeze_row=2)

    wb.properties.creator = "Sean Dillard"
    wb.properties.lastModifiedBy = "Sean Dillard"
    wb.properties.title = "Property Analysis Reference Data"
    wb.properties.description = "Joe Berlin's reference data for the property-analysis skill."

    wb.save(output_path)
    return output_path


if __name__ == "__main__":
    path = build_reference_template()
    print(f"Built reference template: {path}")
    print(f"Size: {path.stat().st_size/1024:.1f} KB")
