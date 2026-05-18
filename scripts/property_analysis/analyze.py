"""
Property analysis orchestrator — single entry point for the skill.

Pipeline:
  1. Parse the address
  2. Load Joe's reference data (overrides, thresholds)
  3. Look up beds, rent (FMR), tax (override or manual)
  4. Build the Inputs dataclass
  5. Run the math engine
  6. Populate the Excel template
  7. Render + save the markdown report
  8. Print the markdown to stdout for the Claude skill to surface

Usage:
    python -m scripts.property_analysis.analyze \\
        --address "1417 S Canal St, Pittsburgh, PA 15215" \\
        --price 130000 \\
        --arv 255000 \\
        --rehab 55000 \\
        --beds 3
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from scripts.property_analysis.compute import Inputs, compute_returns
from scripts.property_analysis.normalize import parse_address
from scripts.property_analysis.lookup import (
    load_property_overrides, load_thresholds, find_override,
    resolve_rent, resolve_market_rent, resolve_tax, resolve_beds,
    fetch_rentcast_data, fetch_wprdc_data, compute_rent_variance,
)
from scripts.property_analysis.provenance import Provenance, single
from scripts.property_analysis.max_bid import solve_max_bid, MaxBidResult
from scripts.property_analysis.sensitivity import run_sensitivity
from scripts.property_analysis.diligence import generate as generate_diligence
from scripts.property_analysis.populate import populate_proforma
from scripts.property_analysis.report import save_report, render_report
from scripts.property_analysis.rentcast import RentCastClient
from scripts.property_analysis.wprdc import WPRDCClient
from scripts.property_analysis.geo import lookup_county as geo_lookup_county

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output" / "projects" / "Joe Berlin"


def _slugify(raw: str) -> str:
    """Convert a raw address into a safe filename slug."""
    cleaned = unicodedata.normalize("NFKD", raw)
    cleaned = "".join(c for c in cleaned if not unicodedata.combining(c))
    # Keep the street number + name portion; drop punctuation, city, state, zip
    street_only = re.split(r"[,]", cleaned)[0]
    return re.sub(r"[^A-Za-z0-9]+", "_", street_only).strip("_")[:48]


def analyze(
    address: str,
    purchase_price: Optional[float] = None,
    arv: Optional[float] = None,
    rehab_cost: Optional[float] = None,
    beds: Optional[int] = None,
    baths: float = 1.0,
    output_dir: Optional[Path] = None,
    overrides_dict: Optional[dict] = None,
    rentcast_mode: Optional[str] = None,
    deal_id: Optional[str] = None,
) -> dict:
    """
    Run the full analysis pipeline.

    Args:
        address: Raw address string.
        purchase_price: Acquisition price.
        arv: After-repair value. Defaults to purchase_price if not provided.
        rehab_cost: Rehab budget. Default 0 (turnkey).
        beds: Bedroom count. If None, uses overrides or default 3.
        baths: Bathroom count.
        output_dir: Where to write outputs. Defaults to output/projects/Joe Berlin.
        overrides_dict: Optional dict to override any default input (e.g.,
            {"interest_rate": 0.085, "refi_year": 0}).

    Returns:
        dict with keys:
          result      - compute.Result
          excel_path  - Path to populated xlsx
          report_path - Path to markdown report
          report_md   - Markdown string
          provenance  - dict of {input: source}
    """
    output_dir = Path(output_dir) if output_dir else OUTPUT_DIR

    # Track what the caller actually supplied vs what we had to placeholder.
    # Placeholder underwriting is NEVER actionable — it must surface as a blocker
    # and a prominent banner so Joe is not handed a fabricated recommendation.
    price_provided  = purchase_price is not None and purchase_price > 0
    arv_provided    = arv is not None and arv > 0
    rehab_provided  = rehab_cost is not None  # 0 is a valid value (turnkey)

    if not arv_provided:
        # Without an ARV, the entire BRRRR exit calc is fictional and max-bid
        # is meaningless. We still let the engine run for diagnostics but mark
        # the output non-actionable.
        arv = (purchase_price or 0) or 100000  # last-resort placeholder
    if not price_provided:
        # Address-only with ARV: use a placeholder price; max-bid is the real answer.
        purchase_price = arv * 0.5
    if rehab_cost is None:
        rehab_cost = 0  # turnkey default

    placeholder_underwriting = not arv_provided

    # ── 1. Parse address ──
    parsed = parse_address(address)

    # ── 2. Load reference data ──
    overrides = load_property_overrides()
    thresholds = load_thresholds()
    override = find_override(parsed, overrides)

    # ── 2b. Fetch RentCast data once (property record + rent estimate) ──
    # Mode resolution: explicit arg -> env var -> "off" (safe default). Tests/demos
    # opt into fixture mode explicitly via rentcast_mode="fixture".
    rentcast_client = RentCastClient(mode=rentcast_mode)
    rentcast_record, rentcast_estimate = fetch_rentcast_data(parsed, rentcast_client)

    # ── 2c. Fetch WPRDC Allegheny parcel data (no-op for non-PA addresses) ──
    wprdc_client = WPRDCClient()
    wprdc_lookup, wprdc_details = fetch_wprdc_data(parsed, wprdc_client)

    # ── 3. Resolve each input with full provenance ──
    p_beds          = resolve_beds(parsed, override=override, explicit=beds,
                                   rentcast_record=rentcast_record,
                                   wprdc_details=wprdc_details)
    # County hint disambiguates ambiguous SAFMR ZIPs (10,106 US ZIPs map to
    # multiple HUD areas). Priority:
    #   1. WPRDC municipality (Allegheny addresses) — most precise
    #   2. geo.db ZCTA-to-county dominant match (anywhere in US) — deterministic
    #   3. None (lookup will pick highest-rent and flag ambiguity)
    county_hint = None
    county_hint_source = None
    geo_info = None
    if wprdc_details and wprdc_details.municipality:
        county_hint = wprdc_details.municipality
        county_hint_source = "WPRDC"
    elif parsed.zip:
        try:
            geo_info = geo_lookup_county(parsed.zip)
        except FileNotFoundError:
            geo_info = None
        if geo_info:
            # Use just the county portion (strip ' County' suffix) since
            # HUD area names sometimes include it and sometimes don't.
            county_hint = geo_info.county_name.replace(" County", "").strip()
            county_hint_source = "geo.db (Census ZCTA-county)"
    p_rent          = resolve_rent(parsed, beds=int(p_beds.value), override=override,
                                   county_hint=county_hint)
    # Annotate provenance with the county-hint source for the audit trail
    if county_hint and county_hint_source:
        for a in p_rent.attempts:
            if a.source.startswith("HUD ") and a.succeeded:
                a.note = (a.note or "") + f" | county disambiguated via {county_hint_source}: {county_hint}"
                break
    p_market_rent   = resolve_market_rent(parsed, beds=int(p_beds.value),
                                          override=override,
                                          rentcast_estimate=rentcast_estimate)
    p_tax           = resolve_tax(parsed, override=override,
                                  rentcast_record=rentcast_record,
                                  wprdc_details=wprdc_details)

    # Compute rent variance flag (HUD vs RentCast)
    rent_variance = compute_rent_variance(
        underwriting_rent=p_rent.value if p_rent.value else 0,
        market_rent=p_market_rent.value,
    )

    # Build full Provenance map for the report's source audit
    provenance_map: dict[str, Provenance] = {
        "address": single("address", address, "prompt", "high"),
        "zip": single("zip", parsed.zip, "parsed_from_address", "high")
               if parsed.zip else single("zip", None, "manual_required", "manual"),
        "beds": p_beds,
        "baths": single("baths", baths, "prompt" if baths != 1.0 else "default",
                        "high" if baths != 1.0 else "low"),
        "purchase_price": single("purchase_price", purchase_price, "prompt", "high"),
        "arv": single("arv", arv, "prompt", "high"),
        "rehab_cost": single("rehab_cost", rehab_cost, "prompt", "high"),
        "monthly_rent": p_rent,
        "market_rent": p_market_rent,
        "property_tax_annual": p_tax,
    }

    # Legacy provenance shape (string-per-field) for report.py backward compat
    provenance: dict[str, str] = {
        k: f"{v.source}" + (f" ({v.reconciliation})" if v.reconciliation else "")
        for k, v in provenance_map.items()
    }

    # Collect blockers — inputs the model cannot run without
    blockers: list[str] = []
    if placeholder_underwriting:
        blockers.append(
            "PLACEHOLDER UNDERWRITING: no ARV supplied. The model cannot compute a "
            "meaningful exit value, max bid, or returns without an after-repair value. "
            "Re-run with --arv (and ideally --rehab and --price) to get an actionable "
            "result. The report below uses placeholder values and is for diagnostics only."
        )
    if p_rent.value is None:
        blockers.append(
            f"RENT: no underwriting rent for ZIP={parsed.zip or '?'} ({p_beds.value} BR). "
            f"{p_rent.note} Add an entry to Property_Overrides with market_rent, "
            f"or pass --rent in the prompt."
        )
    if p_tax.value is None:
        blockers.append(
            f"PROPERTY TAX: not resolved. {p_tax.note} "
            f"Add prior_year_tax to Property_Overrides for this address."
        )
    if not parsed.zip:
        blockers.append(
            f"ZIP: could not parse a 5-digit ZIP from '{address}'. "
            f"Include the ZIP in the address (e.g., 'Pittsburgh PA 15215')."
        )

    # AMBIGUOUS ZIP blocker: SAFMR returned multiple HUD areas, and we couldn't
    # resolve to a single county (no WPRDC and no geo.db match). Without that,
    # we'd be silently picking the most generous rent, which is unsafe.
    if p_rent.value is not None:
        rent_atts = [a for a in p_rent.attempts if a.source.startswith("HUD ") and a.succeeded]
        if rent_atts:
            note = rent_atts[0].note or ""
            if "AMBIGUOUS" in note and county_hint is None:
                blockers.append(
                    f"AMBIGUOUS_ZIP: ZIP {parsed.zip} maps to multiple HUD FMR areas "
                    f"and no county could be auto-resolved. Pass --county to "
                    f"disambiguate, or add a Property_Overrides entry with the "
                    f"correct market_rent for this address."
                )

    # ── 4. Build Inputs ──
    inputs = Inputs(
        address=address,
        zip=parsed.zip or "",
        beds=int(p_beds.value),
        baths=baths,
        purchase_price=purchase_price,
        arv=arv,
        rehab_cost=rehab_cost,
        # Operating
        monthly_rent=float(p_rent.value) if p_rent.value is not None else 0.0,
        vacancy_rate=thresholds.vacancy_default,
        rent_growth=thresholds.rent_growth_default,
        expense_growth=thresholds.expense_growth_default,
        property_tax_annual=float(p_tax.value) if p_tax.value is not None else 0.0,
        # Thresholds
        cap_rate_min=thresholds.cap_rate_min,
        cash_on_cash_min=thresholds.cash_on_cash_min,
        dscr_min=thresholds.dscr_min,
        irr_min=thresholds.irr_min,
        max_price_to_arv=thresholds.max_price_to_arv,
    )

    # Apply any explicit overrides from the caller
    if overrides_dict:
        for k, v in overrides_dict.items():
            if hasattr(inputs, k):
                setattr(inputs, k, v)

    # ── 5. Compute ──
    result = compute_returns(inputs)

    # ── 5b. Solve max bid (only when underwriting is real, not placeholder) ──
    max_bid_result = None
    if p_rent.value and p_tax.value and not placeholder_underwriting:
        try:
            max_bid_result = solve_max_bid(inputs)
        except Exception as e:
            blockers.append(f"Max-bid solver failed: {e}")

    # ── 5c. Run sensitivity + diligence (Phase 3, deterministic, no LLM) ──
    sensitivity_report = None
    diligence_questions: list = []
    if p_rent.value and p_tax.value and not placeholder_underwriting:
        try:
            sensitivity_report = run_sensitivity(inputs)
        except Exception as e:
            blockers.append(f"Sensitivity engine failed: {e}")
        try:
            diligence_questions = generate_diligence(
                inputs=inputs,
                result=result,
                provenance_map=provenance_map,
                rent_variance=rent_variance,
                wprdc_details=wprdc_details,
                sensitivity_flippers=(sensitivity_report.flippers
                                       if sensitivity_report else []),
            )
        except Exception as e:
            blockers.append(f"Diligence generator failed: {e}")

    # ── 6. Build per-deal packet folder ──
    # Each run gets its own folder: output_dir/<slug>/<YYYY-MM-DD-HHMMSS>/
    # so a single run's artifacts (proforma, report, sources audit) live together
    # and re-runs never overwrite prior work.
    label = _slugify(address)
    run_stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    packet_dir = Path(output_dir) / label / run_stamp
    packet_dir.mkdir(parents=True, exist_ok=True)

    # 6a. Populate Excel inside the packet folder.
    # V2: pass full provenance metadata so the Sources tab carries the
    # audit trail.
    def _prov_meta(p):
        if p is None:
            return {"value": None, "source": "n/a", "confidence": "n/a", "note": ""}
        return {
            "value": p.value,
            "source": p.source or "",
            "confidence": p.confidence or "",
            "note": p.note or "",
        }

    sources_meta_for_xl = {
        "address":             _prov_meta(provenance_map.get("address")),
        "zip":                 _prov_meta(provenance_map.get("zip")),
        "beds":                _prov_meta(provenance_map.get("beds")),
        "baths":               _prov_meta(provenance_map.get("baths")),
        "purchase_price":      _prov_meta(provenance_map.get("purchase_price")),
        "arv":                 _prov_meta(provenance_map.get("arv")),
        "rehab_cost":          _prov_meta(provenance_map.get("rehab_cost")),
        "monthly_rent":        _prov_meta(provenance_map.get("monthly_rent")),
        "market_rent":         _prov_meta(provenance_map.get("market_rent")),
        "property_tax_annual": _prov_meta(provenance_map.get("property_tax_annual")),
        "insurance_annual":    {"value": inputs.insurance_annual,
                                "source": "industry default" if not inputs.insurance_source else inputs.insurance_source,
                                "confidence": "medium",
                                "note": ""},
    }
    freshness_meta = {
        "hud_fmr":      "FY2026",
        "geo":          "Census 2020 ZCTA-county",
        "wprdc":        f"tax_year={wprdc_details.tax_year}, as_of={wprdc_details.as_of_date}"
                        if wprdc_details else "n/a (non-Allegheny)",
        "allegheny":    "TAX_YEAR=2025",
        "rentcast":     rentcast_client.mode,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    # Push source strings into Inputs.* so the Inputs tab named-range cells
    # populate too (rent_source, tax_source, etc.). Non-destructive — only
    # set fields the caller didn't already set.
    if not inputs.monthly_rent_source:
        inputs.monthly_rent_source = sources_meta_for_xl["monthly_rent"]["source"]
    if not inputs.market_rent_source:
        inputs.market_rent_source = sources_meta_for_xl["market_rent"]["source"]
    if not inputs.property_tax_source:
        inputs.property_tax_source = sources_meta_for_xl["property_tax_annual"]["source"]
    if not inputs.insurance_source:
        inputs.insurance_source = sources_meta_for_xl["insurance_annual"]["source"]
    if not inputs.arv_source:
        inputs.arv_source = sources_meta_for_xl["arv"]["source"]
    if not inputs.purchase_price_source:
        inputs.purchase_price_source = sources_meta_for_xl["purchase_price"]["source"]
    if not inputs.beds_source:
        inputs.beds_source = sources_meta_for_xl["beds"]["source"]

    excel_path = populate_proforma(
        inputs=inputs, output_dir=packet_dir, property_label=label,
        sources_meta=sources_meta_for_xl,
        freshness_meta=freshness_meta,
        blockers=blockers,
    )

    # Diagnostic mode: any blocker means the verdict is not trustworthy.
    # The report must NOT display a recommendation; it must label itself
    # diagnostic-only and direct the user to fix the missing inputs.
    diagnostic_only = len(blockers) > 0

    # 6b. Save markdown report inside the packet folder
    report_path = save_report(
        result, output_dir=packet_dir, property_label=label,
        provenance=provenance, output_xlsx_path=excel_path,
        max_bid_result=max_bid_result,
        price_provided=price_provided,
        rent_variance=rent_variance,
        provenance_map=provenance_map,
        rentcast_mode=rentcast_client.mode,
        placeholder_underwriting=placeholder_underwriting,
        diagnostic_only=diagnostic_only,
        blockers=blockers,
        wprdc_lookup=wprdc_lookup,
        wprdc_details=wprdc_details,
        sensitivity_report=sensitivity_report,
        diligence_questions=diligence_questions,
    )

    # 6c. Write a sources.json audit trail with source freshness metadata.
    # Per Sean's DB review (T-105): every data source should disclose its
    # vintage so a reviewer can judge staleness.
    source_freshness = {
        "hud_fmr_fy": 2026,
        "hud_fmr_db_path": "templates/property_analysis/data/hud_fmr.db",
        "geo_db_vintage": "Census 2020 ZCTA-county relationship",
        "geo_db_path": "templates/property_analysis/data/geo.db",
        "rentcast_mode": rentcast_client.mode,
        "wprdc_tax_year": wprdc_details.tax_year if wprdc_details else None,
        "wprdc_as_of_date": wprdc_details.as_of_date if wprdc_details else None,
        "allegheny_millage_tax_year": 2025,  # see allegheny_millages.TAX_YEAR
        "report_generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    sources_audit = {
        "address": address,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "source_freshness": source_freshness,
        "rentcast_mode": rentcast_client.mode,
        "county_hint_used": county_hint,
        "county_hint_source": county_hint_source,
        "geo_lookup": ({
            "zip": geo_info.zip,
            "county": geo_info.county_name,
            "state": geo_info.state,
            "ambiguous": geo_info.ambiguous,
            "alternates": geo_info.alternates,
        } if geo_info else None),
        "price_provided": price_provided,
        "inputs_summary": {
            "purchase_price": purchase_price,
            "arv": arv,
            "rehab_cost": rehab_cost,
            "beds": int(p_beds.value),
            "monthly_rent": p_rent.value,
            "property_tax_annual": p_tax.value,
        },
        "provenance": {
            k: {
                "value": v.value,
                "source": v.source,
                "confidence": v.confidence,
                "note": v.note,
                "reconciliation": v.reconciliation,
                "attempts": [
                    {"source": a.source, "value": a.value, "succeeded": a.succeeded,
                     "note": a.note}
                    for a in v.attempts
                ],
            }
            for k, v in provenance_map.items()
        },
        "rent_variance": rent_variance,
        "blockers": blockers,
        # Intake linkage (T-204): every packet records the deal it came from
        "deal_id": deal_id,
        # Raw WPRDC payload for full audit reproducibility (when in Allegheny)
        "wprdc": {
            "lookup_found": wprdc_lookup.found,
            "parcel_id": wprdc_lookup.parcel_id,
            "municipality": wprdc_lookup.municipality,
            "lookup_error": wprdc_lookup.error,
            "details": ({
                "beds": wprdc_details.beds,
                "baths": wprdc_details.total_baths,
                "sqft": wprdc_details.finished_sqft,
                "year_built": wprdc_details.year_built,
                "property_use": wprdc_details.property_use,
                "condition": wprdc_details.condition,
                "assessed_total": wprdc_details.assessed_total,
                "fair_market_total": wprdc_details.fair_market_total,
                "municipality": wprdc_details.municipality,
                "muni_code": wprdc_details.muni_code,
                "school_district": wprdc_details.school_district,
                "last_sale_price": wprdc_details.last_sale_price,
                "last_sale_date": wprdc_details.last_sale_date,
                "prev_sale_price": wprdc_details.prev_sale_price,
                "prev_sale_date": wprdc_details.prev_sale_date,
                "tax_year": wprdc_details.tax_year,
                "as_of_date": wprdc_details.as_of_date,
            } if wprdc_details else None),
        },
    }
    # T-603: surface verdict + headline metrics so the history viewer
    # can list every run without re-running the math engine. When any
    # blocker exists, the verdict block is null — consistent with the
    # diagnostic-only contract enforced in markdown, JSON, and UI.
    if blockers:
        sources_audit["verdict"] = None
    else:
        sources_audit["verdict"] = {
            "recommendation": result.verdict.recommendation,
            "max_bid": (
                {
                    "price": max_bid_result.max_bid.price,
                    "verdict": max_bid_result.max_bid.verdict,
                }
                if (max_bid_result and max_bid_result.max_bid
                    and max_bid_result.max_bid.price is not None)
                else None
            ),
            "metrics": {
                "cap_rate": result.cap_rate,
                "cash_on_cash_y1": result.cash_on_cash_y1,
                "dscr_min": result.dscr_min_observed,
                "levered_irr": result.levered_irr,
                "equity_multiple": result.equity_multiple,
                "price_to_arv": (
                    purchase_price / arv if (purchase_price and arv) else None
                ),
            },
        }

    sources_path = packet_dir / f"{label}_sources.json"
    sources_path.write_text(json.dumps(sources_audit, indent=2, default=str),
                            encoding="utf-8")
    report_md = report_path.read_text(encoding="utf-8")

    # If this run came from an intake deal, mark it analyzed (T-204)
    if deal_id:
        try:
            from scripts.property_analysis.intake import mark_analyzed
            mark_analyzed(deal_id, run_id=run_stamp, packet_path=str(packet_dir))
        except Exception:
            pass  # best-effort; never fail the analyze run on intake bookkeeping

    return {
        "result": result,
        "max_bid": max_bid_result,
        "price_provided": price_provided,
        "rent_variance": rent_variance,
        "rentcast_record": rentcast_record,
        "rentcast_estimate": rentcast_estimate,
        "rentcast_mode": rentcast_client.mode,
        "deal_id": deal_id,
        "run_id": run_stamp,
        "packet_dir": packet_dir,
        "excel_path": excel_path,
        "report_path": report_path,
        "sources_path": sources_path,
        "report_md": report_md,
        "provenance": provenance,           # legacy string-per-field
        "provenance_map": provenance_map,   # full Provenance objects for IC memo
        "missing_inputs": [k for k, v in provenance.items() if "MISSING" in v],
        "blockers": blockers,
    }


def _run_intake_deal(deal, output_dir, rentcast_mode=None) -> dict:
    """Run analyze() against an intake.Deal, mapping its columns to analyze args."""
    overrides_dict = {}
    if deal.down_payment_pct is not None:
        overrides_dict["down_payment_pct"] = deal.down_payment_pct
    if deal.interest_rate is not None:
        overrides_dict["interest_rate"] = deal.interest_rate
    if deal.refi_year is not None:
        overrides_dict["refi_year"] = deal.refi_year
    if deal.refi_ltv is not None:
        overrides_dict["refi_ltv"] = deal.refi_ltv
    if deal.hold_period_years is not None:
        overrides_dict["hold_period_yrs"] = deal.hold_period_years
    if deal.exit_cap_rate is not None:
        overrides_dict["exit_cap_rate"] = deal.exit_cap_rate
    if deal.insurance_annual is not None:
        overrides_dict["insurance_annual"] = deal.insurance_annual
    if deal.owner_utilities_annual is not None:
        overrides_dict["utilities_annual"] = deal.owner_utilities_annual
    if deal.hoa_annual is not None:
        overrides_dict["hoa_annual"] = deal.hoa_annual

    # T-613: per-deal threshold overrides beat the global Thresholds table.
    # Each is nullable on deal_intake; when non-NULL, push it into the Inputs
    # dataclass so the verdict scorer evaluates against this deal's bar.
    if deal.override_cap_rate_min is not None:
        overrides_dict["cap_rate_min"] = deal.override_cap_rate_min
    if deal.override_cash_on_cash_min is not None:
        overrides_dict["cash_on_cash_min"] = deal.override_cash_on_cash_min
    if deal.override_dscr_min is not None:
        overrides_dict["dscr_min"] = deal.override_dscr_min
    if deal.override_irr_min is not None:
        overrides_dict["irr_min"] = deal.override_irr_min
    if deal.override_max_price_to_arv is not None:
        overrides_dict["max_price_to_arv"] = deal.override_max_price_to_arv

    return analyze(
        address=deal.address,
        purchase_price=deal.purchase_price,
        arv=deal.arv_base,
        rehab_cost=deal.rehab_budget,
        beds=deal.beds_override,
        baths=deal.baths_override or 1.0,
        output_dir=output_dir,
        overrides_dict=overrides_dict,
        rentcast_mode=rentcast_mode,
        deal_id=deal.deal_id,
    )


def main():
    # Windows cp1252 cannot encode the unicode chars our reports contain
    # (e.g., em-dashes). Switch stdout to utf-8 with replacement.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Analyze a single-family property for Joe Berlin")
    # Single-address mode
    p.add_argument("--address", default=None, help="Property address (single-deal mode)")
    p.add_argument("--price", type=float, default=None,
                   help="Purchase price (optional — omit for address-only mode with max-bid guidance)")
    p.add_argument("--arv", type=float, default=None, help="After-repair value")
    p.add_argument("--rehab", type=float, default=0, help="Rehab cost")
    p.add_argument("--beds", type=int, default=None, help="Bedroom count")
    p.add_argument("--baths", type=float, default=1.0, help="Bathroom count")
    p.add_argument("--no-refi", action="store_true", help="Disable refi scenario")
    p.add_argument("--down", type=float, default=None, help="Down payment fraction (1.0 = all cash)")
    p.add_argument("--rate", type=float, default=None, help="Interest rate (e.g., 0.085)")
    p.add_argument("--output-dir", type=Path, default=None, help="Where to save outputs")
    p.add_argument("--json", action="store_true", help="Print provenance + verdict as JSON")
    # Intake-mode flags (Phase 2)
    p.add_argument("--create-deal", action="store_true",
                   help="Interactively create a new deal in deal_intake.db (no analyze).")
    p.add_argument("--from-intake", default=None, metavar="DEAL_ID",
                   help="Load fields from deal_intake.db and analyze that deal.")
    p.add_argument("--list-deals", action="store_true",
                   help="List all deals in deal_intake.db.")
    p.add_argument("--analyze-ready", action="store_true",
                   help="Analyze every deal in 'ready' status.")
    args = p.parse_args()

    # ── Intake-only flows ──
    if args.create_deal:
        from scripts.property_analysis.intake import interactive_create
        interactive_create()
        return
    if args.list_deals:
        from scripts.property_analysis.intake import list_deals, format_deal_short
        deals = list_deals()
        print(f"\n  Deal intake ({len(deals)} total):\n")
        for d in deals:
            print(format_deal_short(d))
        print()
        return
    if args.analyze_ready:
        from scripts.property_analysis.intake import analyze_ready_deals
        deals = analyze_ready_deals()
        if not deals:
            print("No deals in 'ready' status.")
            return
        print(f"Analyzing {len(deals)} ready deal(s)...\n")
        for d in deals:
            print(f"--- {d.deal_id}  ({d.address}) ---")
            out = _run_intake_deal(d, args.output_dir)
            print(f"    packet: {out['packet_dir']}")
            print(f"    blockers: {len(out['blockers'])}")
        return
    if args.from_intake:
        from scripts.property_analysis.intake import get_deal
        deal = get_deal(args.from_intake)
        if deal is None:
            print(f"No deal found with deal_id={args.from_intake}")
            sys.exit(1)
        out = _run_intake_deal(deal, args.output_dir)
        _emit_result(out, args.json)
        return

    # ── Single-address mode (existing behavior) ──
    if not args.address:
        p.error("--address is required (or use --from-intake / --analyze-ready / --create-deal / --list-deals)")

    overrides_dict = {}
    if args.no_refi:
        overrides_dict["refi_year"] = 0
    if args.down is not None:
        overrides_dict["down_payment_pct"] = args.down
    if args.rate is not None:
        overrides_dict["interest_rate"] = args.rate

    out = analyze(
        address=args.address,
        purchase_price=args.price,
        arv=args.arv,
        rehab_cost=args.rehab,
        beds=args.beds,
        baths=args.baths,
        output_dir=args.output_dir,
        overrides_dict=overrides_dict,
    )

    _emit_result(out, args.json); return


def _emit_result(out: dict, as_json: bool) -> None:
    """Shared output formatting for single-address and from-intake flows."""
    if as_json:
        v = out["result"].verdict
        # Diagnostic-only mode: NEVER leak an actionable verdict to JSON
        # consumers (UI, batch scripts, downstream pipelines). The verdict
        # was computed against incomplete data; surfacing it would mislead
        # any machine consumer the same way it would mislead a reader.
        diagnostic = len(out.get("blockers") or []) > 0
        payload = {
            "diagnostic_only": diagnostic,
            "blockers": out["blockers"],
            "excel_path": str(out["excel_path"]),
            "report_path": str(out["report_path"]),
            "provenance": out["provenance"],
            "missing_inputs": out["missing_inputs"],
        }
        if diagnostic:
            payload["verdict"] = None
            payload["recommendation"] = "DIAGNOSTIC_ONLY"
            payload["pass_count"] = None
            payload["fail_count"] = None
            payload["checks"] = None
            payload["max_bid"] = None
            payload["note"] = (
                "Verdict and max-bid suppressed because one or more "
                "load-bearing inputs are missing. Resolve blockers and re-run."
            )
        else:
            payload["verdict"] = v.recommendation
            payload["recommendation"] = v.recommendation
            payload["pass_count"] = v.pass_count
            payload["fail_count"] = v.fail_count
            payload["checks"] = v.checks
            if out.get("max_bid"):
                mb = out["max_bid"]
                payload["max_bid"] = {
                    "max_bid": mb.max_bid.price,
                    "stretch_bid": mb.stretch_bid.price,
                    "do_not_cross": mb.do_not_cross.price,
                    "binding_at_max": mb.max_bid.binding,
                }
        print(json.dumps(payload, indent=2, default=str))
    else:
        # Surface blockers first if present — they invalidate the verdict
        if out["blockers"]:
            print("=" * 70)
            print("WARNING: missing data — verdict is not reliable without these inputs:")
            for b in out["blockers"]:
                print(f"  • {b}")
            print("=" * 70)
            print()
        print(out["report_md"])
        print()
        print(f"Deal packet: {out['packet_dir']}")
        print(f"  Excel:   {out['excel_path'].name}")
        print(f"  Report:  {out['report_path'].name}")
        print(f"  Sources: {out['sources_path'].name}")


if __name__ == "__main__":
    main()
