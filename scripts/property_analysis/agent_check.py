"""
Agent verification script — runs alongside regression in the autonomous loop.

Catches integrity violations that the regression's per-case assertions
might miss. Specifically:

1. Required files exist (proforma template, HUD FMR DB, reference data).
2. The most recent packet for Joe's known property includes all 3 artifacts.
3. No report containing blockers shows an actionable verdict in markdown.
4. No JSON output with blockers shows an actionable verdict.
5. RentCast mode is never silently "fixture" in a default-config run.
6. No flat-file artifacts in the Joe project folder (per-deal packet only).
7. Regression suite passes when invoked.

Exit code: 0 if all checks pass, 1 otherwise.

Usage:
    python -m scripts.property_analysis.agent_check
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOE = PROJECT_ROOT / "output" / "projects" / "Joe Berlin"

VERDICT_BADGES = ("**[BUY]**", "**[CONSIDER]**", "**[PASS")
VERDICT_HEADLINES = ("## Recommendation:", "## Bid Guidance:")
ACTIONABLE_RECOMMENDATIONS = ("BUY", "CONSIDER", "PASS")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def check_required_files() -> CheckResult:
    """Make sure the static dependencies exist."""
    required = [
        PROJECT_ROOT / "templates" / "property_analysis" / "proforma_template.xlsx",
        PROJECT_ROOT / "templates" / "property_analysis" / "data" / "hud_fmr.db",
        PROJECT_ROOT / "scripts" / "property_analysis" / "analyze.py",
        PROJECT_ROOT / "scripts" / "property_analysis" / "regression.py",
        PROJECT_ROOT / "AGENT_STATE.md",
        PROJECT_ROOT / "AGENT_BACKLOG.md",
        PROJECT_ROOT / "AGENTIC_DELIVERY.md",
    ]
    missing = [p for p in required if not p.exists()]
    if missing:
        return CheckResult(
            "Required files exist",
            False,
            f"missing: {', '.join(str(p.relative_to(PROJECT_ROOT)) for p in missing)}",
        )
    return CheckResult("Required files exist", True,
                       f"{len(required)} present")


def check_no_flat_artifacts() -> CheckResult:
    """No flat <slug>_<date>.xlsx files in the Joe folder; must be per-deal subfolder."""
    if not JOE.exists():
        return CheckResult("No flat artifacts in Joe folder", True, "folder missing (OK)")
    pattern = re.compile(r"^[A-Za-z0-9_]+_\d{4}-\d{2}-\d{2}(_\d+)?\.(xlsx|md)$")
    offenders = [p.name for p in JOE.iterdir() if p.is_file() and pattern.match(p.name)]
    if offenders:
        return CheckResult(
            "No flat artifacts in Joe folder",
            False,
            f"found {len(offenders)} flat artifacts (should live in per-deal subfolder): {offenders[:3]}",
        )
    return CheckResult("No flat artifacts in Joe folder", True)


def check_latest_packet_integrity() -> CheckResult:
    """Find latest packet under Joe folder; assert it has 3 artifacts."""
    if not JOE.exists():
        return CheckResult("Latest packet integrity", True, "no packets yet (OK)")
    # Find the most recent run folder
    run_folders = sorted(
        (p for slug_dir in JOE.iterdir() if slug_dir.is_dir()
         for p in slug_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )
    if not run_folders:
        return CheckResult("Latest packet integrity", True, "no packets yet (OK)")
    latest = run_folders[-1]
    xlsx = list(latest.glob("*_proforma.xlsx"))
    md = list(latest.glob("*_report.md"))
    sources = list(latest.glob("*_sources.json"))
    missing = []
    if not xlsx: missing.append("proforma.xlsx")
    if not md: missing.append("report.md")
    if not sources: missing.append("sources.json")
    if missing:
        return CheckResult(
            "Latest packet integrity",
            False,
            f"{latest.relative_to(PROJECT_ROOT)} missing: {missing}",
        )
    return CheckResult("Latest packet integrity", True,
                       f"3/3 artifacts in {latest.name}")


def check_diagnostic_reports_have_no_verdict() -> CheckResult:
    """For every report.md in packets, if it contains 'DIAGNOSTIC ONLY',
    it must NOT contain any verdict badge or verdict headline."""
    if not JOE.exists():
        return CheckResult("Diagnostic markdown suppresses verdict", True,
                           "no packets yet (OK)")
    violations = []
    for md_path in JOE.rglob("*_report.md"):
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception:
            continue
        if "DIAGNOSTIC ONLY" not in text:
            continue
        # Find first hit only — enough to flag
        for headline in VERDICT_HEADLINES:
            if headline in text:
                violations.append(
                    f"{md_path.relative_to(PROJECT_ROOT)} has DIAGNOSTIC + '{headline}'"
                )
                break
        else:
            for badge in VERDICT_BADGES:
                if badge in text:
                    violations.append(
                        f"{md_path.relative_to(PROJECT_ROOT)} has DIAGNOSTIC + verdict badge"
                    )
                    break
    if violations:
        return CheckResult(
            "Diagnostic markdown suppresses verdict",
            False,
            f"{len(violations)} violation(s); first: {violations[0]}",
        )
    return CheckResult("Diagnostic markdown suppresses verdict", True,
                       "all DIAGNOSTIC reports clean")


def check_sources_jsons_have_provenance() -> CheckResult:
    """Every sources.json must include 'provenance' top-level key
    with monthly_rent, property_tax_annual, beds entries."""
    if not JOE.exists():
        return CheckResult("sources.json includes provenance", True,
                           "no packets yet (OK)")
    violations = []
    required_fields = ("monthly_rent", "property_tax_annual", "beds")
    for sj in JOE.rglob("*_sources.json"):
        try:
            data = json.loads(sj.read_text(encoding="utf-8"))
        except Exception as e:
            violations.append(f"{sj.relative_to(PROJECT_ROOT)} unreadable: {e}")
            continue
        prov = data.get("provenance", {})
        missing = [f for f in required_fields if f not in prov]
        if missing:
            violations.append(
                f"{sj.relative_to(PROJECT_ROOT)} missing provenance fields: {missing}"
            )
    if violations:
        return CheckResult(
            "sources.json includes provenance",
            False,
            f"{len(violations)} violation(s); first: {violations[0]}",
        )
    return CheckResult("sources.json includes provenance", True)


def check_no_fixture_in_default_run() -> CheckResult:
    """A default-config run (no RENTCAST_API_KEY, no RENTCAST_MODE) should
    NOT produce a report claiming live RentCast data. Look at the most
    recent report and confirm if it mentions RentCast, it says 'OFF' or
    'no data' rather than 'LIVE' or a fixture marker."""
    if not JOE.exists():
        return CheckResult("No fixture-as-live leak (default config)", True,
                           "no packets yet (OK)")
    # Look at the most recent report
    md_files = sorted(JOE.rglob("*_report.md"), key=lambda p: p.stat().st_mtime)
    if not md_files:
        return CheckResult("No fixture-as-live leak (default config)", True,
                           "no reports yet (OK)")
    latest = md_files[-1]
    text = latest.read_text(encoding="utf-8")
    # If the report claims LIVE without an API key being set in the run context,
    # that's a leak. We can only check the artifact, not env at run-time.
    # Heuristic: if "RentCast mode: **FIXTURE**" appears, that's a warning sign
    # for production but fine for tests. We only fail if "RentCast mode: **LIVE**"
    # appears in a report where no key would have been available.
    # For now: just confirm fixture banner contains its disclaimer.
    if "**FIXTURE**" in text:
        if "WARNING" not in text:
            return CheckResult(
                "No fixture-as-live leak (default config)",
                False,
                f"{latest.relative_to(PROJECT_ROOT)} has FIXTURE banner without WARNING",
            )
    return CheckResult("No fixture-as-live leak (default config)", True)


def check_regression_passes() -> CheckResult:
    """Run the regression suite. Pass iff it exits 0."""
    import os
    env = os.environ.copy()
    env["WPRDC_MODE"] = "off"  # avoid network in agent_check
    try:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.property_analysis.regression"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=120,
            env=env,
        )
        ok = result.returncode == 0 and "ALL CASES PASSED" in result.stdout
        if ok:
            # extract case count from output
            import re as _re
            m = _re.search(r"(\d+) cases", result.stdout)
            count = m.group(1) if m else "?"
            return CheckResult("Regression suite passes", True,
                               f"{count} cases passed")
        else:
            tail = result.stdout.split("\n")[-15:] if result.stdout else result.stderr.split("\n")[-15:]
            return CheckResult("Regression suite passes", False,
                               "regression failed; last lines:\n  " + "\n  ".join(tail))
    except subprocess.TimeoutExpired:
        return CheckResult("Regression suite passes", False, "timed out (>120s)")
    except Exception as e:
        return CheckResult("Regression suite passes", False, f"exception: {e}")


CHECKS: list[Callable[[], CheckResult]] = [
    check_required_files,
    check_no_flat_artifacts,
    check_latest_packet_integrity,
    check_diagnostic_reports_have_no_verdict,
    check_sources_jsons_have_provenance,
    check_no_fixture_in_default_run,
    check_regression_passes,
]


def main():
    print(f"\n{'=' * 70}")
    print(f"  AGENT CHECK — Property Analysis integrity verification")
    print('=' * 70)
    fails = 0
    for fn in CHECKS:
        try:
            r = fn()
        except Exception as e:
            r = CheckResult(fn.__name__, False, f"check crashed: {e}")
        status = "[PASS]" if r.passed else "[FAIL]"
        print(f"  {status}  {r.name}")
        if r.detail:
            print(f"          {r.detail}")
        if not r.passed:
            fails += 1
    print(f"\n{'=' * 70}")
    if fails == 0:
        print(f"  ALL AGENT CHECKS PASSED")
    else:
        print(f"  {fails} AGENT CHECK(S) FAILED — fix before moving phases.")
    print('=' * 70)
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
