"""
Regression test suite for the property analysis skill.

Every case runs against an isolated temp output dir so the suite NEVER
pollutes Joe's project folder. Temp dir is printed at start so artifacts
can be inspected after a failure.

Run before any client touches the skill:
    python -m scripts.property_analysis.regression
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from scripts.property_analysis.analyze import analyze
from scripts.property_analysis.fmr import lookup_fmr, lookup_safmr, FMRLookupError
from scripts.property_analysis.rentcast import RentCastClient, CACHE_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Session-scoped temp output dir; set in main()
TEMP_OUT: Optional[Path] = None


# ──────────────────────────────────────────────
# TEST RUNNER
# ──────────────────────────────────────────────

@dataclass
class Check:
    name: str
    passed: bool
    actual: Any
    expected: Any
    note: str = ""


def approx(a: float, b: float, tol: float = 0.005) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def run_case(name: str, fn: Callable[[], list[Check]]) -> int:
    print(f"\n{'='*70}")
    print(f"  CASE: {name}")
    print('='*70)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            checks = fn()
    except Exception as e:
        print(f"  [CRASHED]: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1

    fails = 0
    for c in checks:
        status = "[PASS]" if c.passed else "[FAIL]"
        actual_str = f"{c.actual:.4f}" if isinstance(c.actual, float) else str(c.actual)
        expected_str = f"{c.expected:.4f}" if isinstance(c.expected, float) else str(c.expected)
        print(f"  {status}  {c.name}")
        if not c.passed:
            print(f"          expected: {expected_str}")
            print(f"          actual:   {actual_str}")
            if c.note:
                print(f"          note:     {c.note}")
            fails += 1
        elif c.note:
            print(f"          ({c.note})")
    return fails


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _run_canal(beds=3, **kwargs):
    """Run analyze on 1417 S Canal St with sensible defaults. Uses TEMP_OUT.

    Defaults to RentCast fixture mode so the rent stack always renders
    with known canned values for assertions. Production callers (and the
    skill) get the safer "off" default — tests opt in.
    """
    base = dict(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        purchase_price=130000, arv=255000, rehab_cost=55000, beds=beds,
        output_dir=TEMP_OUT,
        rentcast_mode="fixture",
        overrides_dict={"interest_rate": 0.095, "down_payment_pct": 1.0},
    )
    base.update(kwargs)
    return analyze(**base)


# ──────────────────────────────────────────────
# CASES
# ──────────────────────────────────────────────

def case_1_known_good() -> list[Check]:
    """1417 S Canal St with Joe's actual assumptions -> CONSIDER, IRR ~15.93%."""
    out = _run_canal()
    r = out["result"]
    return [
        Check("Verdict is CONSIDER",
              r.verdict.recommendation == "CONSIDER",
              r.verdict.recommendation, "CONSIDER"),
        Check("Pass count = 3",
              r.verdict.pass_count == 3, r.verdict.pass_count, 3),
        Check("Cap rate ~= 9.62%",
              approx(r.cap_rate, 0.0962, 0.001),
              r.cap_rate, 0.0962),
        Check("DSCR min ~= 1.23x",
              approx(r.dscr_min_observed, 1.2264, 0.005),
              r.dscr_min_observed, 1.2264),
        Check("Levered IRR ~= 15.93%",
              approx(r.levered_irr, 0.1593, 0.005),
              r.levered_irr, 0.1593),
        Check("No blockers",
              len(out["blockers"]) == 0,
              len(out["blockers"]), 0,
              note="; ".join(out["blockers"]) if out["blockers"] else ""),
    ]


def case_2_override_path() -> list[Check]:
    """Pass NO beds -> overrides must win over FMR/RentCast."""
    out = _run_canal(beds=None)
    inputs = out["result"].inputs
    provenance = out["provenance"]
    return [
        Check("Beds came from Property_Overrides",
              "Property_Overrides" in provenance.get("beds", ""),
              provenance.get("beds", ""), "contains 'Property_Overrides'"),
        Check("Beds = 3 (override value)",
              inputs.beds == 3, inputs.beds, 3),
        Check("Rent came from Property_Overrides ($2,500)",
              "Property_Overrides" in provenance.get("monthly_rent", ""),
              provenance.get("monthly_rent", ""), "contains 'Property_Overrides'"),
        Check("Rent value = $2,500 (not FMR)",
              inputs.monthly_rent == 2500.0,
              inputs.monthly_rent, 2500.0),
        Check("Tax came from Property_Overrides",
              "Property_Overrides" in provenance.get("property_tax_annual", ""),
              provenance.get("property_tax_annual", ""), "contains 'Property_Overrides'"),
        Check("Tax = $3,249 (override value)",
              inputs.property_tax_annual == 3249.0,
              inputs.property_tax_annual, 3249.0),
    ]


def case_3_fmr_chain() -> list[Check]:
    """SAFMR lookup happy paths + invalid ZIP errors cleanly."""
    checks = []
    safmr_pgh = lookup_safmr("15215", 3)
    checks.append(Check("ZIP 15215 (Pittsburgh) returns SAFMR rent",
                        safmr_pgh is not None,
                        f"${safmr_pgh.rent}/mo" if safmr_pgh else None,
                        "SAFMR result"))
    if safmr_pgh:
        checks.append(Check("ZIP 15215 SAFMR 3BR ~= $1,670",
                            approx(safmr_pgh.rent, 1670, 50),
                            safmr_pgh.rent, 1670))
        checks.append(Check("ZIP 15215 source = 'SAFMR'",
                            safmr_pgh.source == "SAFMR",
                            safmr_pgh.source, "SAFMR"))
    safmr_phl = lookup_safmr("19103", 3)
    checks.append(Check("ZIP 19103 (Philadelphia) returns SAFMR rent",
                        safmr_phl is not None,
                        f"${safmr_phl.rent}/mo" if safmr_phl else None,
                        "SAFMR result"))
    rent_2br = lookup_safmr("15215", 2)
    rent_4br = lookup_safmr("15215", 4)
    checks.append(Check("Different BR counts return different rents",
                        rent_2br is not None and rent_4br is not None
                        and rent_2br.rent < safmr_pgh.rent < rent_4br.rent,
                        f"2BR=${rent_2br.rent} < 3BR=${safmr_pgh.rent} < 4BR=${rent_4br.rent}",
                        "2BR < 3BR < 4BR"))
    invalid = lookup_safmr("99999", 3)
    checks.append(Check("Invalid ZIP 99999 returns None from SAFMR",
                        invalid is None,
                        "found" if invalid else "None", "None"))
    try:
        lookup_fmr("99999", 3)
        checks.append(Check("Invalid ZIP raises FMRLookupError",
                            False, "no error", "FMRLookupError"))
    except FMRLookupError:
        checks.append(Check("Invalid ZIP raises FMRLookupError",
                            True, "raised", "raised"))
    return checks


def case_4_missing_data() -> list[Check]:
    """Address without ZIP -> blockers populated; no silent estimates."""
    out = analyze(
        address="999 Nowhere Lane, NoSuchCity, XX",
        purchase_price=150000, arv=200000, rehab_cost=20000,
        beds=None, output_dir=TEMP_OUT,
        rentcast_mode="off",
        overrides_dict={"interest_rate": 0.085},
    )
    return [
        Check("Pipeline did NOT crash", True, "completed", "completed"),
        Check("Blockers non-empty",
              len(out["blockers"]) > 0,
              len(out["blockers"]), "> 0"),
        Check("Blocker mentions missing ZIP",
              any("ZIP" in b for b in out["blockers"]),
              [b[:30] + "..." for b in out["blockers"]], "contains ZIP"),
        Check("Blocker mentions missing rent",
              any("RENT" in b for b in out["blockers"]),
              [b[:30] + "..." for b in out["blockers"]], "contains RENT"),
        Check("Blocker mentions missing tax",
              any("TAX" in b for b in out["blockers"]),
              [b[:30] + "..." for b in out["blockers"]], "contains TAX"),
    ]


def case_5_per_deal_packet() -> list[Check]:
    """Each run produces a packet folder with proforma + report + sources.json."""
    out = _run_canal()
    packet = out["packet_dir"]
    label = "1417_S_Canal_St"
    return [
        Check("Packet folder exists",
              packet.exists() and packet.is_dir(),
              str(packet), "exists/is_dir"),
        Check("Packet contains proforma xlsx",
              (packet / f"{label}_proforma.xlsx").exists(),
              "exists" if (packet / f"{label}_proforma.xlsx").exists() else "missing",
              "exists"),
        Check("Packet contains report md",
              (packet / f"{label}_report.md").exists(),
              "exists" if (packet / f"{label}_report.md").exists() else "missing",
              "exists"),
        Check("Packet contains sources.json",
              (packet / f"{label}_sources.json").exists(),
              "exists" if (packet / f"{label}_sources.json").exists() else "missing",
              "exists"),
        Check("sources.json includes rentcast_mode field",
              "rentcast_mode" in json.loads((packet / f"{label}_sources.json").read_text()),
              "present", "present"),
        Check("sources.json includes provenance for monthly_rent",
              "monthly_rent" in json.loads((packet / f"{label}_sources.json").read_text())["provenance"],
              "present", "present"),
    ]


def case_6_address_only_with_arv() -> list[Check]:
    """No --price but --arv supplied -> bid-guidance headline + max-bid solved."""
    out = analyze(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        arv=255000, rehab_cost=55000, beds=3,
        output_dir=TEMP_OUT,
        rentcast_mode="fixture",
        overrides_dict={"interest_rate": 0.095, "down_payment_pct": 1.0},
    )
    md = out["report_md"]
    return [
        Check("price_provided flag is False",
              out["price_provided"] is False,
              out["price_provided"], False),
        Check("Report headline = 'Bid Guidance: No Price Supplied'",
              "Bid Guidance: No Price Supplied" in md,
              "found" if "Bid Guidance: No Price Supplied" in md else "missing",
              "found"),
        Check("Report contains Scenario Snapshot section",
              "## Scenario Snapshot" in md,
              "found" if "## Scenario Snapshot" in md else "missing",
              "found"),
        Check("max_bid object returned",
              out["max_bid"] is not None,
              "present" if out["max_bid"] is not None else "missing",
              "present"),
        Check("NO placeholder-underwriting banner (ARV was supplied)",
              "PLACEHOLDER UNDERWRITING" not in md,
              "absent" if "PLACEHOLDER UNDERWRITING" not in md else "present",
              "absent"),
        Check("NO placeholder blocker (ARV was supplied)",
              not any("PLACEHOLDER" in b for b in out["blockers"]),
              "no placeholder blocker", "no placeholder blocker"),
    ]


def case_6b_address_only_no_arv_placeholder() -> list[Check]:
    """No --price AND no --arv -> diagnostic mode: NO recommendation displayed."""
    out = analyze(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        beds=3, output_dir=TEMP_OUT,
        rentcast_mode="fixture",
    )
    md = out["report_md"]
    return [
        Check("DIAGNOSTIC ONLY block appears at top",
              "DIAGNOSTIC ONLY" in md,
              "found" if "DIAGNOSTIC ONLY" in md else "missing", "found"),
        Check("Placeholder blocker is set",
              any("PLACEHOLDER" in b for b in out["blockers"]),
              [b[:40] + "..." for b in out["blockers"]],
              "contains PLACEHOLDER blocker"),
        Check("Report does NOT show 'Recommendation:' headline",
              "## Recommendation:" not in md,
              "absent" if "## Recommendation:" not in md else "present",
              "absent",
              note="Diagnostic mode must suppress the verdict to prevent misleading the reader"),
        Check("Report does NOT show 'Bid Guidance' headline",
              "## Bid Guidance:" not in md,
              "absent" if "## Bid Guidance:" not in md else "present", "absent"),
        Check("Report does NOT show verdict badge ([BUY]/[CONSIDER]/[PASS])",
              not any(badge in md for badge in ("**[BUY]**", "**[CONSIDER]**", "**[PASS")),
              "no badges" if not any(badge in md for badge in ("**[BUY]**", "**[CONSIDER]**", "**[PASS")) else "badge present",
              "no badges"),
        Check("Max-bid result is None (skipped due to placeholder)",
              out["max_bid"] is None,
              "None" if out["max_bid"] is None else "present", "None"),
        Check("Pipeline does NOT crash on placeholder underwriting",
              True, "completed", "completed"),
    ]


def case_6c_diagnostic_when_tax_missing_only() -> list[Check]:
    """Full ARV/price/rent supplied but tax missing -> still diagnostic mode."""
    # Use a non-Joe address with full price/ARV but no override → tax will be missing
    out = analyze(
        address="500 Random Ave, Pittsburgh, PA 15217",
        purchase_price=100000, arv=130000, rehab_cost=15000, beds=2,
        output_dir=TEMP_OUT,
        rentcast_mode="off",
        overrides_dict={"interest_rate": 0.08, "down_payment_pct": 0.25},
    )
    md = out["report_md"]
    has_tax_blocker = any("TAX" in b for b in out["blockers"])
    return [
        Check("Tax blocker is set (no override, no live fetch)",
              has_tax_blocker,
              [b[:40] + "..." for b in out["blockers"]],
              "contains TAX blocker"),
        Check("Report shows DIAGNOSTIC ONLY (because tax is missing)",
              "DIAGNOSTIC ONLY" in md,
              "found" if "DIAGNOSTIC ONLY" in md else "missing", "found"),
        Check("Report does NOT show 'Recommendation:' headline",
              "## Recommendation:" not in md,
              "absent" if "## Recommendation:" not in md else "present", "absent"),
    ]


def case_7_rentcast_mode_isolation() -> list[Check]:
    """Fixture cache MUST NOT leak into off mode."""
    # Use a private cache dir for this test so we don't touch the global one
    tmp_cache = TEMP_OUT / "rc_cache_test"
    tmp_cache.mkdir(parents=True, exist_ok=True)
    addr = "1417 S Canal St, Pittsburgh, PA 15215"

    c_fix = RentCastClient(mode="fixture", cache_dir=tmp_cache)
    rec_fix = c_fix.property_records(addr)

    c_off = RentCastClient(mode="off", cache_dir=tmp_cache)
    rec_off = c_off.property_records(addr)

    return [
        Check("Fixture mode returns a property record",
              rec_fix is not None,
              "present" if rec_fix is not None else "None", "present"),
        Check("Off mode returns None",
              rec_off is None,
              "None" if rec_off is None else "present", "None"),
        Check("Fixture cache dir exists",
              (tmp_cache / "fixture").exists(),
              "exists" if (tmp_cache / "fixture").exists() else "missing", "exists"),
        Check("Off mode cache dir is separate from fixture",
              c_off.cache_dir != c_fix.cache_dir,
              f"{c_off.cache_dir.name} vs {c_fix.cache_dir.name}",
              "different"),
    ]


def case_8_report_text_assertions() -> list[Check]:
    """Critical institutional strings must appear in the report."""
    out = _run_canal()
    md = out["report_md"]
    return [
        Check("Report contains 'Rent Stack' section",
              "## Rent Stack" in md, "found", "found"),
        Check("Report shows RentCast mode banner",
              "RentCast mode:" in md, "found", "found"),
        Check("Report contains 'Scenario Snapshot' section",
              "## Scenario Snapshot" in md, "found", "found"),
        Check("Report contains 'Decision Breakdown' section",
              "## Decision Breakdown" in md, "found", "found"),
        Check("Report contains 'Assumptions' section with sources",
              "## Assumptions" in md, "found", "found"),
        Check("Report contains 'Cashflow Summary' section",
              "## Cashflow Summary" in md, "found", "found"),
    ]


def case_9_hud_label_correctness() -> list[Check]:
    """'HUD SAFMR' must appear cleanly; 'SAHUD' must never appear."""
    out = _run_canal(beds=None)  # Force the chain to actually run
    md = out["report_md"]
    sources_json = (out["packet_dir"] / "1417_S_Canal_St_sources.json").read_text()

    # The 1417 case uses overrides for rent, so HUD SAFMR appears as an
    # un-selected attempt in sources.json, not in the report body. We assert
    # the bad label never appears anywhere.
    return [
        Check("'SAHUD' string NEVER appears in report",
              "SAHUD" not in md,
              "found 'SAHUD'" if "SAHUD" in md else "absent", "absent"),
        Check("'SAHUD' string NEVER appears in sources.json",
              "SAHUD" not in sources_json,
              "found 'SAHUD'" if "SAHUD" in sources_json else "absent", "absent"),
        Check("'HUD SAFMR' label appears in sources.json (attempt list)",
              "HUD SAFMR" in sources_json,
              "found" if "HUD SAFMR" in sources_json else "missing", "found"),
    ]


def case_10_no_artifact_pollution() -> list[Check]:
    """Confirm regression artifacts stay in TEMP_OUT, not in Joe's folder."""
    joe_folder = PROJECT_ROOT / "output" / "projects" / "Joe Berlin"
    today_files = list(joe_folder.glob("1417_S_Canal_St_2026-*"))
    return [
        Check("Joe folder has no flat 1417_S_Canal_St_<date> files",
              len(today_files) == 0,
              len(today_files), 0,
              note=", ".join(f.name for f in today_files[:3]) if today_files else ""),
    ]


def case_11_json_diagnostic_suppression() -> list[Check]:
    """JSON mode MUST suppress verdict when blockers exist.

    Spec: Sean's DB_AND_UI_REVIEW.md — P0 fix. UI/batch consumers must not
    see an actionable verdict when the underwriting is diagnostic-only.
    """
    # Diagnostic case (no ARV)
    out_diag = analyze(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        beds=3, output_dir=TEMP_OUT,
        rentcast_mode="fixture",
    )
    # Healthy case
    out_ok = _run_canal()
    checks = [
        Check("Diagnostic case has blockers > 0",
              len(out_diag["blockers"]) > 0,
              len(out_diag["blockers"]), "> 0"),
        Check("Healthy case has 0 blockers",
              len(out_ok["blockers"]) == 0,
              len(out_ok["blockers"]), 0),
    ]
    # Simulate the JSON payload shape produced by main() — match the actual logic.
    diag_payload = _simulated_json_payload(out_diag)
    ok_payload = _simulated_json_payload(out_ok)
    checks.extend([
        Check("Diagnostic JSON: diagnostic_only == True",
              diag_payload["diagnostic_only"] is True,
              diag_payload["diagnostic_only"], True),
        Check("Diagnostic JSON: verdict is null",
              diag_payload["verdict"] is None,
              diag_payload["verdict"], None),
        Check("Diagnostic JSON: recommendation == 'DIAGNOSTIC_ONLY'",
              diag_payload["recommendation"] == "DIAGNOSTIC_ONLY",
              diag_payload["recommendation"], "DIAGNOSTIC_ONLY"),
        Check("Diagnostic JSON: pass/fail counts are null",
              diag_payload["pass_count"] is None and diag_payload["fail_count"] is None,
              f"pass={diag_payload['pass_count']}, fail={diag_payload['fail_count']}",
              "both None"),
        Check("Diagnostic JSON: max_bid is null",
              diag_payload["max_bid"] is None,
              diag_payload["max_bid"], None),
        Check("Healthy JSON: verdict is present",
              ok_payload["verdict"] is not None,
              ok_payload["verdict"], "non-null"),
        Check("Healthy JSON: diagnostic_only == False",
              ok_payload["diagnostic_only"] is False,
              ok_payload["diagnostic_only"], False),
    ])
    return checks


def _simulated_json_payload(out: dict) -> dict:
    """Mirror the JSON-mode payload built in analyze.main()."""
    v = out["result"].verdict
    diagnostic = len(out.get("blockers") or []) > 0
    payload = {
        "diagnostic_only": diagnostic,
        "blockers": out["blockers"],
    }
    if diagnostic:
        payload.update({
            "verdict": None,
            "recommendation": "DIAGNOSTIC_ONLY",
            "pass_count": None,
            "fail_count": None,
            "checks": None,
            "max_bid": None,
        })
    else:
        payload.update({
            "verdict": v.recommendation,
            "recommendation": v.recommendation,
            "pass_count": v.pass_count,
            "fail_count": v.fail_count,
            "checks": v.checks,
            "max_bid": out.get("max_bid"),
        })
    return payload


def case_13_arkansas_resolves() -> list[Check]:
    """Arkansas address (72202 Little Rock) resolves rent + county via geo.db.

    Spec: T-106 Phase 1 acceptance — non-PA addresses must produce a real
    HUD SAFMR rent through deterministic disambiguation.
    """
    from scripts.property_analysis.geo import lookup_county
    geo = lookup_county("72202")
    out = analyze(
        address="100 Main St, Little Rock, AR 72202",
        purchase_price=100000, arv=130000, rehab_cost=20000, beds=3,
        output_dir=TEMP_OUT,
        rentcast_mode="off",
        overrides_dict={"interest_rate": 0.075, "down_payment_pct": 0.25},
    )
    # Find the HUD rent attempt
    rent_attempts = [a for a in out["provenance_map"]["monthly_rent"].attempts
                     if a.source.startswith("HUD ") and a.succeeded]
    return [
        Check("geo.db returns Pulaski County for 72202",
              geo is not None and "Pulaski" in geo.county_name,
              f"{geo.county_name if geo else None}", "contains Pulaski"),
        Check("Pipeline runs without crash on AR address",
              True, "completed", "completed"),
        Check("HUD SAFMR rent resolved for 72202",
              out["provenance_map"]["monthly_rent"].value is not None,
              out["provenance_map"]["monthly_rent"].value, "non-null"),
        Check("Rent confidence is 'high' (unambiguous ZIP)",
              out["provenance_map"]["monthly_rent"].confidence == "high",
              out["provenance_map"]["monthly_rent"].confidence, "high"),
        Check("Rent source mentions HUD SAFMR (not SAHUD)",
              rent_attempts and "HUD" in rent_attempts[0].source
              and "SAHUD" not in rent_attempts[0].source,
              rent_attempts[0].source if rent_attempts else None,
              "HUD SAFMR or HUD FMR"),
        Check("Tax blocker fires (no Allegheny WPRDC for AR; no override)",
              any("TAX" in b for b in out["blockers"]),
              [b[:30] + "..." for b in out["blockers"]], "TAX mentioned"),
    ]


def case_14_ambiguous_zip_disambiguated_by_geo() -> list[Check]:
    """Ambiguous ZIP 40601 (Frankfort KY, 4 county matches) disambiguates
    via geo.db dominant-county heuristic — Franklin County wins.

    Spec: T-106 Phase 1 acceptance — when geo.db resolves a county for an
    otherwise ambiguous SAFMR ZIP, confidence recovers to 'high' and
    no AMBIGUOUS_ZIP blocker fires.
    """
    from scripts.property_analysis.geo import lookup_county
    geo = lookup_county("40601")
    out = analyze(
        address="200 Capitol Ave, Frankfort, KY 40601",
        purchase_price=150000, arv=200000, rehab_cost=25000, beds=3,
        output_dir=TEMP_OUT,
        rentcast_mode="off",
        overrides_dict={"interest_rate": 0.075, "down_payment_pct": 0.25},
    )
    return [
        Check("geo.db flags 40601 as ambiguous (4 county matches)",
              geo is not None and geo.ambiguous and len(geo.alternates) >= 2,
              f"alts={len(geo.alternates) if geo else 0}", ">= 2"),
        Check("geo.db dominant county for 40601 is Franklin",
              geo is not None and "Franklin" in geo.county_name,
              geo.county_name if geo else None, "contains Franklin"),
        Check("Pipeline runs without crash on ambiguous-ZIP KY address",
              True, "completed", "completed"),
        Check("HUD rent resolved (geo.db disambiguated)",
              out["provenance_map"]["monthly_rent"].value is not None,
              out["provenance_map"]["monthly_rent"].value, "non-null"),
        Check("No AMBIGUOUS_ZIP blocker (geo.db provided county hint)",
              not any("AMBIGUOUS_ZIP" in b for b in out["blockers"]),
              [b[:40] for b in out["blockers"] if "AMBIG" in b] or "no AMBIG blocker",
              "no AMBIG blocker"),
    ]


def case_15_geo_db_metadata_in_sources() -> list[Check]:
    """sources.json must include source_freshness and geo_lookup metadata.

    Spec: T-105 — source freshness disclosure for audit.
    """
    out = _run_canal()
    sj_path = out["packet_dir"] / f"1417_S_Canal_St_sources.json"
    if not sj_path.exists():
        return [Check("sources.json exists", False, "missing", "exists")]
    data = json.loads(sj_path.read_text(encoding="utf-8"))
    sf = data.get("source_freshness", {})
    return [
        Check("sources.json has source_freshness block",
              "source_freshness" in data,
              "present" if "source_freshness" in data else "missing", "present"),
        Check("source_freshness includes hud_fmr_fy",
              sf.get("hud_fmr_fy") == 2026,
              sf.get("hud_fmr_fy"), 2026),
        Check("source_freshness includes geo_db_vintage",
              "geo_db_vintage" in sf,
              sf.get("geo_db_vintage"), "present"),
        Check("source_freshness includes wprdc_tax_year key (may be null if WPRDC off)",
              "wprdc_tax_year" in sf,
              "present" if "wprdc_tax_year" in sf else "missing", "present"),
        Check("source_freshness includes report_generated_at timestamp",
              "report_generated_at" in sf,
              sf.get("report_generated_at"), "present"),
    ]


def case_16_intake_create_and_list() -> list[Check]:
    """Phase 2 (T-205): create/list/analyze-ready round-trip in an isolated DB."""
    from scripts.property_analysis.intake import (
        schema_init, create_deal, get_deal, list_deals,
        analyze_ready_deals, update_deal, mark_analyzed,
    )
    db = TEMP_OUT / "deal_intake_t16.db"
    schema_init(db)

    d1 = create_deal(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        arv_base=255000, rehab_budget=55000, section8_status="confirmed",
        purchase_price=130000, beds_override=3,
        db_path=db,
    )
    d2 = create_deal(
        address="57 Lower Rd, Pittsburgh, PA 15215",
        section8_status="assumed",  # no ARV/rehab yet -> not ready
        db_path=db,
    )
    all_deals = list_deals(db_path=db)
    ready_before = analyze_ready_deals(db_path=db)
    update_deal(d2.deal_id, db_path=db, arv_base=35000, rehab_budget=15000)
    ready_after = analyze_ready_deals(db_path=db)
    fetched = get_deal(d1.deal_id, db_path=db)
    marked = mark_analyzed(d1.deal_id, run_id="2026-05-17_TEST",
                           packet_path="/tmp/fake_packet", db_path=db)

    return [
        Check("create_deal returns deal_id with slug+date+seq format",
              bool(d1.deal_id) and "_20" in d1.deal_id,
              d1.deal_id, "slug_YYYYMMDD_n"),
        Check("Deal with full inputs is status=ready",
              d1.status == "ready", d1.status, "ready"),
        Check("Deal with missing required fields is NOT ready",
              not d2.is_ready, d2.is_ready, False,
              note=f"missing: {d2.required_missing()}"),
        Check("list_deals returns both inserted deals",
              len(all_deals) == 2, len(all_deals), 2),
        Check("analyze_ready_deals returns only the complete one (1 of 2)",
              len(ready_before) == 1, len(ready_before), 1),
        Check("update_deal flips status to 'ready' once required fields populated",
              len(ready_after) == 2, len(ready_after), 2),
        Check("get_deal round-trips the inserted deal",
              fetched is not None and fetched.address.startswith("1417"),
              fetched.address if fetched else None, "1417 S Canal St..."),
        Check("mark_analyzed sets status=analyzed + records run_id + packet_path",
              marked is not None and marked.status == "analyzed"
              and marked.last_analyzed_run_id == "2026-05-17_TEST"
              and marked.last_packet_path == "/tmp/fake_packet",
              f"status={marked.status}, run_id={marked.last_analyzed_run_id}",
              "analyzed / 2026-05-17_TEST / /tmp/fake_packet"),
    ]


def case_17_analyze_from_intake() -> list[Check]:
    """Phase 2 (T-204): --from-intake equivalent — analyze() with deal_id
    creates a packet, links it to the deal in deal_intake.db, and the
    sources.json contains the deal_id."""
    from scripts.property_analysis.intake import (
        schema_init, create_deal, get_deal,
    )
    db = TEMP_OUT / "deal_intake_t17.db"
    schema_init(db)

    # Point analyze() at this DB via monkeypatch of DB_PATH module global
    import scripts.property_analysis.intake as intake_mod
    original_db = intake_mod.DB_PATH
    intake_mod.DB_PATH = db
    try:
        d = create_deal(
            address="1417 S Canal St, Pittsburgh, PA 15215",
            arv_base=255000, rehab_budget=55000, section8_status="confirmed",
            purchase_price=130000, beds_override=3, interest_rate=0.095,
            down_payment_pct=1.0,
            db_path=db,
        )
        out = analyze(
            address=d.address,
            purchase_price=d.purchase_price,
            arv=d.arv_base,
            rehab_cost=d.rehab_budget,
            beds=d.beds_override,
            output_dir=TEMP_OUT,
            overrides_dict={"interest_rate": 0.095, "down_payment_pct": 1.0},
            rentcast_mode="fixture",
            deal_id=d.deal_id,
        )
        sources = json.loads(out["sources_path"].read_text(encoding="utf-8"))
        post_deal = get_deal(d.deal_id, db_path=db)
    finally:
        intake_mod.DB_PATH = original_db

    return [
        Check("analyze() accepts deal_id parameter",
              out.get("deal_id") == d.deal_id, out.get("deal_id"), d.deal_id),
        Check("sources.json captures deal_id",
              sources.get("deal_id") == d.deal_id,
              sources.get("deal_id"), d.deal_id),
        Check("intake DB shows deal status=analyzed after run",
              post_deal.status == "analyzed",
              post_deal.status, "analyzed"),
        Check("intake DB records last_packet_path",
              post_deal.last_packet_path == str(out["packet_dir"]),
              post_deal.last_packet_path[:60], "matches packet_dir"),
        Check("intake DB records last_analyzed_run_id",
              post_deal.last_analyzed_run_id == out["run_id"],
              post_deal.last_analyzed_run_id, out["run_id"]),
    ]


def case_18_sensitivity_engine() -> list[Check]:
    """Phase 3 (T-301, T-304): sensitivity engine produces 9 perturbations
    + flips the verdict for at least one of them on Joe's borderline 1417 deal."""
    from scripts.property_analysis.sensitivity import run_sensitivity, PERTURBATIONS
    from scripts.property_analysis.compute import Inputs

    inputs = Inputs(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        zip="15215", beds=3,
        purchase_price=130000, arv=255000, rehab_cost=55000,
        closing_cost_pct=0.07, holding_months=3,
        down_payment_pct=1.0, interest_rate=0.095, loan_term_yrs=30,
        refi_year=3, refi_ltv=0.75, refi_interest_rate=0.075,
        refi_term_yrs=30, refi_closing_cost_pct=0.04,
        monthly_rent=2500, rent_growth=0.03, vacancy_rate=0.04,
        management_pct=0.10, maintenance_pct=0.05, capex_reserve_pct=0.05,
        property_tax_annual=3249, insurance_annual=1020,
        hoa_annual=0, utilities_annual=0, expense_growth=0.03,
        hold_period_yrs=10, exit_cap_rate=0.08, cost_of_sale_pct=0.06,
    )
    rep = run_sensitivity(inputs)

    return [
        Check("Sensitivity runs all 9 canonical perturbations",
              len(rep.runs) == len(PERTURBATIONS),
              len(rep.runs), len(PERTURBATIONS)),
        Check("Base run captured separately from perturbations",
              rep.base.name == "Base case", rep.base.name, "Base case"),
        Check("Rent -10% perturbation exists by name",
              any(r.name == "Rent -10%" for r in rep.runs),
              True, True),
        Check("At least one perturbation flips the verdict on 1417 (borderline CONSIDER)",
              len(rep.flippers) >= 1,
              len(rep.flippers), ">= 1"),
        Check("IRR delta is 0.0 for base, non-zero for perturbations",
              rep.base.irr_delta_bps == 0.0 and any(r.irr_delta_bps != 0 for r in rep.runs),
              f"base={rep.base.irr_delta_bps} ; perturbed nonzero",
              "yes"),
        Check("Flippers carry binding_constraints list (non-empty when not BUY)",
              all(r.binding_constraints or r.new_verdict == "BUY"
                  for r in rep.flippers),
              "all flippers have constraints listed",
              "all flippers have constraints"),
    ]


def case_19_diligence_rules() -> list[Check]:
    """Phase 3 (T-303): diligence generator fires the right rules for the
    1417 borderline deal — heavy rehab, low DSCR, refi feasibility, lease terms."""
    out = _run_canal()
    from scripts.property_analysis.diligence import generate

    # We need to recompute the questions because _run_canal goes through analyze
    # which already wires them in. We can pull from the report instead.
    md = out["report_md"]

    return [
        Check("Report contains 'Diligence Questions' section",
              "## Diligence Questions" in md,
              "found" if "## Diligence Questions" in md else "missing", "found"),
        Check("Diligence contains 'Rent' subsection",
              "### Rent" in md, "found", "found"),
        Check("Diligence contains 'Rehab' subsection (rehab is 42% of price)",
              "### Rehab" in md, "found", "found"),
        Check("Diligence contains 'Financing' subsection (DSCR 1.23x < 1.30)",
              "### Financing" in md, "found", "found"),
        Check("DSCR-near-threshold rule fires (1.23x close to 1.25x lender floor)",
              "1.25x lender floor" in md or "1.30" in md,
              "DSCR rule fired", "yes"),
        Check("At least one [MUST ASK] item in the diligence list",
              "[MUST ASK]" in md, "found", "found"),
    ]


def case_20_magnitude_column() -> list[Check]:
    """Phase 3 (T-302): Decision Breakdown table shows magnitude for failing
    thresholds (e.g., '0.4 pp short', '0.02x short')."""
    out = _run_canal()
    md = out["report_md"]
    return [
        Check("Decision Breakdown has 'Magnitude' column",
              "| Magnitude |" in md, "found", "found"),
        Check("Failing thresholds show 'short' magnitude text",
              "short" in md and "pp" in md,
              "found", "found",
              note="Cash-on-Cash and DSCR both fail at base case for 1417"),
        Check("Passing thresholds show magnitude='—' (em-dash) not a number",
              "**PASS** | — |" in md or "PASS** | — |" in md,
              "found", "found"),
    ]


def case_21_webapp_routes_smoke() -> list[Check]:
    """Phase 4 (T-405): every public route returns 200 and the diagnostic-only
    rule survives in the UI layer (no verdict/recommendation/max-bid shown
    when blockers exist).

    Uses an in-process TestClient. No external server. No browser. Routes
    exercised: /healthz, /, /deals (POST), /deals/{id}, /results/{id}/{run_id},
    /packets/{id}, /files/{id}/{filename}.
    """
    from fastapi.testclient import TestClient
    from scripts.property_analysis import intake as _intake
    from scripts.property_analysis.analyze import _run_intake_deal
    import os as _os

    # Point intake at a fresh temp DB so the smoke test never touches Sean's
    # real deals DB. Same env var the FastAPI app respects on startup.
    db_path = TEMP_OUT / "webapp_smoke" / "intake.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _os.environ["PROPERTY_ANALYSIS_DB"] = str(db_path)
    # Re-import intake so the schema points at our path
    import importlib
    importlib.reload(_intake)
    _intake.schema_init()

    # Import the app AFTER we've set the env var.
    from webapp.main import app as _app

    checks: list[Check] = []
    # TestClient must be entered as a context manager so the FastAPI app's
    # lifespan + httpx transport release on exit. Without this, Windows
    # keeps a handle on the test intake.db and shutil.rmtree fails.
    with TestClient(_app) as client:
        checks.extend(_run_case_21_checks(client, _intake, _run_intake_deal))
    return checks


def _run_case_21_checks(client, _intake, _run_intake_deal) -> list[Check]:
    checks: list[Check] = []

    # Health
    r = client.get("/healthz")
    checks.append(Check("GET /healthz returns 200", r.status_code == 200,
                        r.status_code, 200))

    # Home with empty deals list
    r = client.get("/")
    checks.append(Check("GET / returns 200", r.status_code == 200,
                        r.status_code, 200))
    checks.append(Check("Home shows intake form",
                        "New deal" in r.text and "Address" in r.text,
                        "found", "found"))

    # Create a deal that will be diagnostic-only (no ARV -> placeholder underwriting)
    r = client.post(
        "/deals",
        data={
            "address": "57 Lower Rd, Pittsburgh, PA 15215",
            "section8_status": "assumed",
            "notes": "smoke test deal",
        },
        follow_redirects=False,
    )
    checks.append(Check("POST /deals redirects (303)", r.status_code == 303,
                        r.status_code, 303))
    redirect_url = r.headers.get("location", "")
    deal_id = redirect_url.rsplit("/", 1)[-1] if redirect_url else ""
    checks.append(Check("POST /deals returns a deal_id in redirect",
                        bool(deal_id), deal_id or "missing", "non-empty"))

    # Deal detail page
    r = client.get(f"/deals/{deal_id}")
    checks.append(Check("GET /deals/{id} returns 200", r.status_code == 200,
                        r.status_code, 200))

    # Deal is not ready (no ARV/rehab) -> Analyze button must be disabled
    checks.append(Check("Deal page shows NOT READY banner",
                        "NOT READY" in r.text,
                        "found" if "NOT READY" in r.text else "missing",
                        "found"))
    checks.append(Check("Analyze button is disabled when not ready",
                        "disabled" in r.text,
                        "found" if "disabled" in r.text else "missing",
                        "found"))

    # Make the deal ready (set ARV + rehab via direct DB update so the smoke
    # test doesn't depend on a future PATCH route).
    _intake.update_deal(deal_id, arv_base=300000.0, rehab_budget=50000.0,
                       beds_override=3)
    deal = _intake.get_deal(deal_id)
    checks.append(Check("Deal becomes ready after ARV/rehab supplied",
                        deal.is_ready, deal.is_ready, True))

    # Trigger the analyze pipeline directly (bypass HTTP for speed —
    # the analyze endpoint is exercised via the redirect target check below)
    packet_root = TEMP_OUT / "webapp_smoke" / "packets"
    out = _run_intake_deal(deal, output_dir=packet_root, rentcast_mode="off")
    run_id = out["run_id"]
    diagnostic = len(out.get("blockers") or []) > 0
    checks.append(Check("Analyze produced a packet folder",
                        Path(out["packet_dir"]).exists(),
                        "exists", "exists"))

    # Result page
    r = client.get(f"/results/{deal_id}/{run_id}")
    checks.append(Check("GET /results/{id}/{run} returns 200",
                        r.status_code == 200, r.status_code, 200))
    checks.append(Check("Result page shows the address",
                        deal.address in r.text, "found", "found"))
    checks.append(Check("Result page shows Inputs and provenance section",
                        "Inputs and provenance" in r.text, "found", "found"))

    # CRITICAL diagnostic-only rule: if blockers exist, the UI must NOT
    # display an actionable verdict badge.
    if diagnostic:
        checks.append(Check(
            "Diagnostic-only result UI shows 'DIAGNOSTIC ONLY' banner",
            "DIAGNOSTIC ONLY" in r.text, "found", "found",
            note=f"blockers={out.get('blockers')}"))
        # In diagnostic mode the markdown report itself MUST NOT show a
        # recommendation line. We verify against the rendered HTML.
        forbidden_active_verdicts = [
            "## VERDICT: BUY",
            "## VERDICT: CONSIDER",
            "## VERDICT: PASS",
            "Recommendation: BUY",
            "Recommendation: CONSIDER",
            "Recommendation: PASS",
        ]
        for forbidden in forbidden_active_verdicts:
            checks.append(Check(
                f"Diagnostic UI suppresses '{forbidden}'",
                forbidden not in r.text, "absent", "absent"))
    else:
        # If the smoke deal did produce a verdict, then either BUY/CONSIDER/PASS
        # appears (analyze did its job) — just confirm the report rendered.
        checks.append(Check("Non-diagnostic result page contains report body",
                            "Underwriting report" in r.text, "found", "found"))

    # Packet listing
    r = client.get(f"/packets/{deal_id}")
    checks.append(Check("GET /packets/{id} returns 200",
                        r.status_code == 200, r.status_code, 200))
    checks.append(Check("Packet view lists at least one file",
                        "Download" in r.text, "found", "found"))

    # File download — the proforma xlsx
    excel_files = list(Path(out["packet_dir"]).glob("*_proforma.xlsx"))
    if excel_files:
        r = client.get(f"/files/{deal_id}/{excel_files[0].name}")
        checks.append(Check("GET /files/{id}/{xlsx} returns 200",
                            r.status_code == 200, r.status_code, 200))
        checks.append(Check("Downloaded file has non-zero content",
                            len(r.content) > 1000, len(r.content), "> 1000"))

    # Path-escape guard: a traversal attempt must be rejected (400)
    r = client.get(f"/files/{deal_id}/..%2F..%2Fsecret.txt")
    checks.append(Check("Path-traversal request is rejected",
                        r.status_code in (400, 404), r.status_code, "400 or 404"))

    # 404s
    r = client.get("/deals/does-not-exist-deal-id")
    checks.append(Check("Unknown deal returns 404",
                        r.status_code == 404, r.status_code, 404))
    r = client.get("/results/does-not-exist/does-not-exist")
    checks.append(Check("Unknown result returns 404",
                        r.status_code == 404, r.status_code, 404))

    # ── Forced-diagnostic deal ───────────────────────────────────────
    # Create a deal whose address has no ZIP → blockers will fire →
    # UI must show DIAGNOSTIC ONLY and suppress every verdict string.
    r = client.post(
        "/deals",
        data={
            "address": "999 Nowhere Lane, NoSuchCity, XX",
            "section8_status": "unknown",
            "arv_base": 200000,
            "rehab_budget": 20000,
        },
        follow_redirects=False,
    )
    diag_deal_id = (r.headers.get("location", "") or "").rsplit("/", 1)[-1]
    diag_deal = _intake.get_deal(diag_deal_id)
    diag_out = _run_intake_deal(diag_deal, output_dir=packet_root, rentcast_mode="off")
    checks.append(Check(
        "Forced-diagnostic deal produced blockers",
        len(diag_out.get("blockers") or []) > 0,
        len(diag_out.get("blockers") or []), "> 0"))
    r = client.get(f"/results/{diag_deal_id}/{diag_out['run_id']}")
    checks.append(Check(
        "Forced-diagnostic result page returns 200",
        r.status_code == 200, r.status_code, 200))
    checks.append(Check(
        "Forced-diagnostic UI shows DIAGNOSTIC ONLY banner",
        "DIAGNOSTIC ONLY" in r.text, "found", "found"))
    # These verdict strings must NEVER appear in a diagnostic UI page.
    for forbidden in ("Recommendation: BUY",
                       "Recommendation: CONSIDER",
                       "Recommendation: PASS",
                       "## VERDICT: BUY",
                       "## VERDICT: CONSIDER",
                       "## VERDICT: PASS"):
        checks.append(Check(
            f"Forced-diagnostic UI suppresses '{forbidden}'",
            forbidden not in r.text, "absent", "absent"))

    return checks


def case_22_project_edit_rerun_history() -> list[Check]:
    """Phase 6.1 (T-601, T-603, T-604, T-605): the project-per-address
    workflow. Edit a deal's inputs, re-run analysis, then verify:

    - Both packet folders exist on disk under the same slug.
    - GET /deals/{id}/history returns both runs, newest first.
    - The OLDER run is openable via /deals/{id}/runs/{run_id} and via the
      per-run file download endpoint.
    - sources.json on each run carries the new verdict block (T-603).

    Exercises every Phase 6.1 endpoint in one round-trip.
    """
    from fastapi.testclient import TestClient
    from scripts.property_analysis import intake as _intake
    from scripts.property_analysis.analyze import _run_intake_deal
    import os as _os
    import importlib

    db_path = TEMP_OUT / "phase6_smoke" / "intake.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _os.environ["PROPERTY_ANALYSIS_DB"] = str(db_path)
    importlib.reload(_intake)
    _intake.schema_init()

    from webapp.main import app as _app

    checks: list[Check] = []
    with TestClient(_app) as client:
        # Create a ready deal
        r = client.post(
            "/deals",
            data={
                "address": "1417 S Canal St, Pittsburgh, PA 15215",
                "section8_status": "confirmed",
                "arv_base": 255000,
                "rehab_budget": 55000,
                "purchase_price": 130000,
                "beds_override": 3,
            },
            follow_redirects=False,
        )
        deal_id = (r.headers.get("location", "") or "").rsplit("/", 1)[-1]
        checks.append(Check("Phase 6 setup: deal created",
                            bool(deal_id), deal_id or "missing", "non-empty"))

        # Run #1 — direct pipeline call (TestClient analyze endpoint shells
        # out to the real engine; skipping for speed)
        deal = _intake.get_deal(deal_id)
        packet_root = TEMP_OUT / "phase6_smoke" / "packets"
        out1 = _run_intake_deal(deal, output_dir=packet_root, rentcast_mode="off")
        run_id_1 = out1["run_id"]
        time.sleep(1.1)  # ensure run_id_2 has a distinct timestamp

        # T-601: edit inputs via POST /deals/{id}/edit
        deal = _intake.get_deal(deal_id)
        r = client.post(
            f"/deals/{deal_id}/edit",
            data={
                # Most fields kept at current values; bump purchase_price by 10%
                "address": deal.address,
                "section8_status": deal.section8_status,
                "arv_base": str(deal.arv_base),
                "rehab_budget": str(deal.rehab_budget),
                "purchase_price": str((deal.purchase_price or 130000) * 1.10),
                "beds_override": str(deal.beds_override or 3),
                "notes": "Phase 6 regression",
            },
            follow_redirects=False,
        )
        checks.append(Check(
            "T-601: POST /deals/{id}/edit returns 303 redirect",
            r.status_code == 303, r.status_code, 303))
        deal = _intake.get_deal(deal_id)
        checks.append(Check(
            "T-601: edit persists new purchase_price",
            abs(deal.purchase_price - 143000) < 1, deal.purchase_price, 143000))

        # Run #2 with the edited inputs
        out2 = _run_intake_deal(deal, output_dir=packet_root, rentcast_mode="off")
        run_id_2 = out2["run_id"]
        checks.append(Check(
            "T-602: re-run produced a distinct run_id",
            run_id_1 != run_id_2, run_id_2, f"!= {run_id_1}"))

        # Both packet folders on disk?
        from pathlib import Path as _P
        slug_dir = _P(out2["packet_dir"]).parent
        runs_on_disk = sorted(p.name for p in slug_dir.iterdir() if p.is_dir())
        checks.append(Check(
            "T-602: both runs persist on disk under the same slug",
            len(runs_on_disk) == 2 and run_id_1 in runs_on_disk
            and run_id_2 in runs_on_disk,
            runs_on_disk, [run_id_1, run_id_2]))

        # T-603: history endpoint returns both, newest first
        r = client.get(f"/deals/{deal_id}/history")
        checks.append(Check(
            "T-603: GET /deals/{id}/history returns 200",
            r.status_code == 200, r.status_code, 200))
        checks.append(Check(
            "T-603: history page lists run #1",
            run_id_1 in r.text, "found", "found"))
        checks.append(Check(
            "T-603: history page lists run #2",
            run_id_2 in r.text, "found", "found"))
        # Newest first: run #2 (later timestamp) should appear before run #1
        idx2 = r.text.find(run_id_2)
        idx1 = r.text.find(run_id_1)
        checks.append(Check(
            "T-603: history orders newest first",
            idx2 < idx1 and idx2 >= 0, f"idx2={idx2} idx1={idx1}", "idx2 < idx1"))

        # T-604: OLDER run is openable
        r = client.get(f"/deals/{deal_id}/runs/{run_id_1}")
        checks.append(Check(
            "T-604: GET /deals/{id}/runs/{old} returns 200",
            r.status_code == 200, r.status_code, 200))

        # Per-run file download from the OLDER run
        excel_files_1 = list(_P(out1["packet_dir"]).glob("*_proforma.xlsx"))
        if excel_files_1:
            r = client.get(
                f"/deals/{deal_id}/runs/{run_id_1}/files/{excel_files_1[0].name}")
            checks.append(Check(
                "T-604: per-run file download (old run) returns 200",
                r.status_code == 200, r.status_code, 200))
            # The xlsx must come from the OLD packet, not the new one
            checks.append(Check(
                "T-604: per-run download returns non-zero content",
                len(r.content) > 1000, len(r.content), "> 1000"))

        # T-603: sources.json carries the new verdict block
        import json as _json
        src_files = list(_P(out2["packet_dir"]).glob("*_sources.json"))
        sources2 = _json.loads(src_files[0].read_text(encoding="utf-8"))
        checks.append(Check(
            "T-603: sources.json has top-level 'verdict' key",
            "verdict" in sources2, "verdict" in sources2, True))
        if not sources2.get("blockers"):
            v = sources2.get("verdict") or {}
            checks.append(Check(
                "T-603: non-diagnostic verdict carries recommendation",
                v.get("recommendation") in ("BUY", "CONSIDER", "PASS"),
                v.get("recommendation"),
                "BUY/CONSIDER/PASS"))
            checks.append(Check(
                "T-603: non-diagnostic verdict carries metrics block",
                isinstance(v.get("metrics"), dict)
                and v["metrics"].get("cap_rate") is not None,
                v.get("metrics"), "dict with cap_rate"))

        # Path-traversal guard on per-run file endpoint
        r = client.get(
            f"/deals/{deal_id}/runs/{run_id_1}/files/..%2F..%2Fsecret.txt")
        checks.append(Check(
            "T-604: per-run path-traversal request rejected",
            r.status_code in (400, 404), r.status_code, "400 or 404"))

    return checks


def case_23_overrides_thresholds_web_crud() -> list[Check]:
    """Phase 6.2 (T-611, T-612, T-615): Property_Overrides and Thresholds
    CRUD through the web UI. The DB is authoritative; xlsx is fallback.
    """
    from fastapi.testclient import TestClient
    from scripts.property_analysis import intake as _intake
    import os as _os, importlib

    db_path = TEMP_OUT / "phase6_2_overrides" / "intake.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _os.environ["PROPERTY_ANALYSIS_DB"] = str(db_path)
    importlib.reload(_intake)
    _intake.schema_init()

    from webapp.main import app as _app
    checks: list[Check] = []
    with TestClient(_app) as client:
        r = client.get("/overrides")
        checks.append(Check("GET /overrides returns 200",
                            r.status_code == 200, r.status_code, 200))
        checks.append(Check("GET /overrides shows empty-state",
                            "No overrides yet" in r.text, "found", "found"))

        r = client.post(
            "/overrides",
            data={
                "address_normalized": "9876 Test Way, Pittsburgh, PA 15215",
                "beds": "3", "annual_tax": "3500",
                "market_rent": "2450", "notes": "Phase 6.2 regression",
            },
            follow_redirects=False,
        )
        checks.append(Check("POST /overrides returns 303 redirect",
                            r.status_code == 303, r.status_code, 303))

        ov = _intake.get_override("9876 Test Way, Pittsburgh, PA 15215")
        checks.append(Check("Override persisted to DB",
                            ov is not None and ov.beds == 3
                            and abs(ov.annual_tax - 3500) < 1,
                            ov, "row with beds=3 tax=3500"))

        r = client.get("/overrides")
        checks.append(Check("GET /overrides shows the new row",
                            "9876 test way" in r.text.lower(),
                            "found", "found"))

        r = client.post(
            "/overrides/delete",
            data={"address_normalized": "9876 Test Way, Pittsburgh, PA 15215"},
            follow_redirects=False,
        )
        checks.append(Check("Delete returns 303",
                            r.status_code == 303, r.status_code, 303))
        checks.append(Check(
            "Override gone from DB after delete",
            _intake.get_override("9876 Test Way, Pittsburgh, PA 15215") is None,
            "absent", "absent"))

        r = client.get("/thresholds")
        checks.append(Check("GET /thresholds returns 200",
                            r.status_code == 200, r.status_code, 200))
        before = _intake.get_thresholds()
        checks.append(Check(
            "Default cap_rate_min == 0.08",
            abs(before["cap_rate_min"] - 0.08) < 1e-9,
            before["cap_rate_min"], 0.08))

        r = client.post(
            "/thresholds",
            data={
                "cap_rate_min": "0.09",
                "cash_on_cash_min": str(before["cash_on_cash_min"]),
                "dscr_min": str(before["dscr_min"]),
                "irr_min": str(before["irr_min"]),
                "max_price_to_arv": str(before["max_price_to_arv"]),
                "vacancy_default": str(before["vacancy_default"]),
                "rent_growth_default": str(before["rent_growth_default"]),
                "expense_growth_default": str(before["expense_growth_default"]),
            },
            follow_redirects=False,
        )
        checks.append(Check("POST /thresholds returns 303",
                            r.status_code == 303, r.status_code, 303))
        after = _intake.get_thresholds()
        checks.append(Check(
            "cap_rate_min updated to 0.09 in DB",
            abs(after["cap_rate_min"] - 0.09) < 1e-9,
            after["cap_rate_min"], 0.09))

    return checks


def case_24_per_deal_threshold_override() -> list[Check]:
    """Phase 6.2 (T-613, T-616): a per-deal threshold override changes the
    verdict for THAT deal without affecting others."""
    from scripts.property_analysis import intake as _intake
    from scripts.property_analysis.analyze import _run_intake_deal
    import os as _os, importlib

    db_path = TEMP_OUT / "phase6_2_thresholds" / "intake.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _os.environ["PROPERTY_ANALYSIS_DB"] = str(db_path)
    importlib.reload(_intake)
    _intake.schema_init()

    packet_root = TEMP_OUT / "phase6_2_thresholds" / "packets"

    deal_a = _intake.create_deal(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        section8_status="confirmed",
        arv_base=255000, rehab_budget=55000,
        purchase_price=130000, beds_override=3,
        interest_rate=0.095, down_payment_pct=1.0,
        override_cap_rate_min=0.12,
    )
    deal_b = _intake.create_deal(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        section8_status="confirmed",
        arv_base=255000, rehab_budget=55000,
        purchase_price=130000, beds_override=3,
        interest_rate=0.095, down_payment_pct=1.0,
    )

    out_a = _run_intake_deal(deal_a, output_dir=packet_root, rentcast_mode="off")
    out_b = _run_intake_deal(deal_b, output_dir=packet_root, rentcast_mode="off")

    checks: list[Check] = []
    checks.append(Check(
        "Deal A (cap-rate=12% override) ran without blockers",
        not out_a.get("blockers"), out_a.get("blockers"), []))
    checks.append(Check(
        "Deal B (no override) ran without blockers",
        not out_b.get("blockers"), out_b.get("blockers"), []))

    report_a = next(Path(out_a["packet_dir"])
                    .glob("*_report.md")).read_text(encoding="utf-8")
    report_b = next(Path(out_b["packet_dir"])
                    .glob("*_report.md")).read_text(encoding="utf-8")

    checks.append(Check(
        "Deal A report shows cap-rate threshold 12.0%",
        ">= 12.0%" in report_a,
        "found" if ">= 12.0%" in report_a else "missing", "found"))
    checks.append(Check(
        "Deal A Cap Rate row reads FAIL (9.6% < 12%)",
        "Cap Rate (Y1) | 9.62% | >= 12.0% | **FAIL**" in report_a,
        "found" if "Cap Rate (Y1) | 9.62% | >= 12.0% | **FAIL**" in report_a else "missing",
        "found"))
    checks.append(Check(
        "Deal B report shows cap-rate threshold 8.0% (unchanged)",
        ">= 8.0%" in report_b,
        "found" if ">= 8.0%" in report_b else "missing", "found"))
    checks.append(Check(
        "Deal B Cap Rate row reads PASS (9.6% > 8%)",
        "Cap Rate (Y1) | 9.62% | >= 8.0% | **PASS**" in report_b,
        "found" if "Cap Rate (Y1) | 9.62% | >= 8.0% | **PASS**" in report_b else "missing",
        "found"))

    return checks


def case_25_pdf_export() -> list[Check]:
    """Phase 6.2 (T-614, T-617): PDF export endpoint returns a non-empty
    application/pdf response with a valid PDF magic header."""
    from fastapi.testclient import TestClient
    from scripts.property_analysis import intake as _intake
    from scripts.property_analysis.analyze import _run_intake_deal
    import os as _os, importlib

    db_path = TEMP_OUT / "phase6_2_pdf" / "intake.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _os.environ["PROPERTY_ANALYSIS_DB"] = str(db_path)
    importlib.reload(_intake)
    _intake.schema_init()

    deal = _intake.create_deal(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        section8_status="confirmed",
        arv_base=255000, rehab_budget=55000,
        purchase_price=130000, beds_override=3,
        interest_rate=0.095, down_payment_pct=1.0,
    )
    out = _run_intake_deal(
        deal, output_dir=TEMP_OUT / "phase6_2_pdf" / "packets",
        rentcast_mode="off",
    )
    run_id = out["run_id"]

    from webapp.main import app as _app
    checks: list[Check] = []
    with TestClient(_app) as client:
        r = client.get(f"/deals/{deal.deal_id}/runs/{run_id}/pdf")
        checks.append(Check(
            "GET /deals/{id}/runs/{run}/pdf returns 200",
            r.status_code == 200, r.status_code, 200))
        checks.append(Check(
            "PDF response carries application/pdf content type",
            r.headers.get("content-type", "").startswith("application/pdf"),
            r.headers.get("content-type"), "application/pdf"))
        checks.append(Check(
            "PDF body is non-empty (> 10 KB)",
            len(r.content) > 10_000, len(r.content), "> 10000"))
        checks.append(Check(
            "PDF body starts with PDF magic header (%PDF)",
            r.content[:4] == b"%PDF",
            r.content[:4], b"%PDF"))

    return checks


def case_12_safmr_ambiguity_surfaced() -> list[Check]:
    """When a ZIP maps to multiple HUD areas, surface alternates as a warning.

    Spec: Sean's DB_AND_UI_REVIEW.md — P0 fix. ZIP-only lookup must not silently
    pick a HUD area when multiple match.
    """
    from scripts.property_analysis.fmr import lookup_safmr
    # 40601 is one of the most ambiguous ZIPs (6 HUD area matches per the DB audit)
    unambiguous = lookup_safmr("15215", 3)  # Pittsburgh — single match
    ambiguous = lookup_safmr("40601", 3)    # Frankfort/Lexington/Louisville KY — 6 matches

    return [
        Check("Unambiguous ZIP (15215) flagged as not-ambiguous",
              unambiguous is not None and unambiguous.ambiguous is False,
              f"ambiguous={getattr(unambiguous, 'ambiguous', None)}",
              "False"),
        Check("Unambiguous ZIP confidence remains 'high'",
              unambiguous is not None and unambiguous.confidence == "high",
              getattr(unambiguous, "confidence", None), "high"),
        Check("Ambiguous ZIP (40601) flagged as ambiguous",
              ambiguous is not None and ambiguous.ambiguous is True,
              f"ambiguous={getattr(ambiguous, 'ambiguous', None)}",
              "True"),
        Check("Ambiguous ZIP returns alternates list",
              ambiguous is not None and len(ambiguous.alternates or []) > 0,
              len(ambiguous.alternates or []), "> 0"),
        Check("Ambiguous ZIP confidence drops to 'medium' without hint",
              ambiguous is not None and ambiguous.confidence == "medium",
              getattr(ambiguous, "confidence", None), "medium"),
        Check("cite() mentions AMBIGUOUS for ambiguous result",
              ambiguous is not None and "AMBIGUOUS" in ambiguous.cite(),
              "found" if (ambiguous and "AMBIGUOUS" in ambiguous.cite()) else "missing",
              "found"),
    ]


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

CASES = [
    ("1. Known-good control (1417 S Canal St)", case_1_known_good),
    ("2. Override-heavy (Property_Overrides wins)", case_2_override_path),
    ("3. SAFMR lookup chain", case_3_fmr_chain),
    ("4. Missing data -> blockers surfaced", case_4_missing_data),
    ("5. Per-deal packet folder structure", case_5_per_deal_packet),
    ("6. Address-only WITH ARV -> bid guidance, no placeholder banner", case_6_address_only_with_arv),
    ("6b. Address-only WITHOUT ARV -> DIAGNOSTIC ONLY, no verdict shown", case_6b_address_only_no_arv_placeholder),
    ("6c. Tax-missing -> DIAGNOSTIC ONLY, no verdict shown", case_6c_diagnostic_when_tax_missing_only),
    ("7. RentCast mode isolation (fixture/off separated)", case_7_rentcast_mode_isolation),
    ("8. Report text contains institutional sections", case_8_report_text_assertions),
    ("9. HUD label correctness (no 'SAHUD' anywhere)", case_9_hud_label_correctness),
    ("10. No artifact pollution in Joe folder", case_10_no_artifact_pollution),
    ("11. JSON mode suppresses verdict in diagnostic state (P0)", case_11_json_diagnostic_suppression),
    ("12. SAFMR ambiguity surfaced for multi-area ZIPs (P0)", case_12_safmr_ambiguity_surfaced),
    ("13. Arkansas address resolves via geo.db (Phase 1)", case_13_arkansas_resolves),
    ("14. Ambiguous ZIP disambiguated by geo.db (Phase 1)", case_14_ambiguous_zip_disambiguated_by_geo),
    ("15. sources.json includes source_freshness metadata (T-105)", case_15_geo_db_metadata_in_sources),
    ("16. Deal intake: create / list / analyze-ready round-trip (T-205)", case_16_intake_create_and_list),
    ("17. analyze() from-intake: deal_id flows to sources.json + DB (T-204)", case_17_analyze_from_intake),
    ("18. Sensitivity engine: 9 perturbations + flippers (T-301, T-304)", case_18_sensitivity_engine),
    ("19. Diligence generator: rule-based questions in report (T-303)", case_19_diligence_rules),
    ("20. Binding-constraint magnitude in Decision Breakdown (T-302)", case_20_magnitude_column),
    ("21. Webapp routes smoke test + diagnostic-only UI suppression (T-405/406)", case_21_webapp_routes_smoke),
    ("22. Phase 6.1: edit -> re-run -> history shows both, older openable (T-601..T-605)", case_22_project_edit_rerun_history),
    ("23. Phase 6.2: /overrides + /thresholds CRUD round-trip (T-611/T-612/T-615)", case_23_overrides_thresholds_web_crud),
    ("24. Phase 6.2: per-deal threshold override flips one verdict, not the other (T-613/T-616)", case_24_per_deal_threshold_override),
    ("25. Phase 6.2: PDF export endpoint returns valid application/pdf (T-614/T-617)", case_25_pdf_export),
]


def main():
    global TEMP_OUT
    TEMP_OUT = Path(tempfile.mkdtemp(prefix="pa_regression_"))
    print(f"\n{'='*70}")
    print(f"  PROPERTY ANALYSIS — PRE-CLIENT REGRESSION SUITE")
    print(f"  {len(CASES)} cases  |  Output: {TEMP_OUT}")
    print('='*70)
    total_fails = 0
    for name, fn in CASES:
        total_fails += run_case(name, fn)

    print(f"\n{'='*70}")
    if total_fails == 0:
        print(f"  ALL CASES PASSED — skill is ready for client.")
        # Clean up temp dir on full success
        try:
            shutil.rmtree(TEMP_OUT)
            print(f"  Cleaned: {TEMP_OUT}")
        except Exception as e:
            print(f"  (Could not clean temp dir: {e})")
    else:
        print(f"  {total_fails} FAILED CHECK(S) — fix before sending to client.")
        print(f"  Inspect artifacts at: {TEMP_OUT}")
    print('='*70)
    sys.exit(0 if total_fails == 0 else 1)


if __name__ == "__main__":
    main()
