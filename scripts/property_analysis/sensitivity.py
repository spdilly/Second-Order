"""
Deterministic sensitivity engine — Phase 3 (T-301, T-304).

Runs the financial engine against the supplied Inputs under seven canonical
perturbations and reports how each input change moves the key metrics
(IRR, DSCR, cap rate, verdict). Identifies which perturbations would
"flip" the recommendation (BUY -> CONSIDER, CONSIDER -> PASS).

No LLM, no narrative — every output comes from compute_returns() against
a perturbed Inputs dataclass. The report module renders the result into
a deterministic Markdown table.

Canonical perturbations (per spec):
  rent -10%
  rehab +20%
  tax +20%
  exit cap +100 bps
  interest rate +100 bps
  purchase price +5%, +10%, -5%, -10%
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from scripts.property_analysis.compute import Inputs, Result, compute_returns


@dataclass
class SensitivityRun:
    """One perturbation result."""
    name: str                       # e.g., "rent -10%"
    delta_label: str                # what changed, in plain text
    new_irr: float                  # NaN if not computable
    new_dscr: float                 # inf if no debt
    new_cap_rate: float
    new_verdict: str                # BUY / CONSIDER / PASS
    new_pass_count: int
    new_fail_count: int
    binding_constraints: list[str]  # which thresholds fail at this perturbation
    irr_delta_bps: float            # change vs base, in basis points
    flipped_to: Optional[str]       # set if verdict differs from base


@dataclass
class SensitivityReport:
    """Full output: base case + 7 perturbations + flippers."""
    base: SensitivityRun
    runs: list[SensitivityRun] = field(default_factory=list)

    @property
    def flippers(self) -> list[SensitivityRun]:
        """Perturbations that change the verdict vs the base."""
        return [r for r in self.runs if r.flipped_to is not None]


# ──────────────────────────────────────────────
# PERTURBATION TABLE
# ──────────────────────────────────────────────

PERTURBATIONS: list[dict] = [
    {"name": "Rent -10%",
     "delta_label": "Monthly rent reduced by 10%",
     "mutate": lambda inp: replace(inp, monthly_rent=inp.monthly_rent * 0.90)},
    {"name": "Rehab +20%",
     "delta_label": "Rehab cost increased by 20%",
     "mutate": lambda inp: replace(inp, rehab_cost=inp.rehab_cost * 1.20)},
    {"name": "Tax +20%",
     "delta_label": "Annual property tax increased by 20%",
     "mutate": lambda inp: replace(inp, property_tax_annual=inp.property_tax_annual * 1.20)},
    {"name": "Exit cap +100 bps",
     "delta_label": "Exit cap rate raised by 1.0 percentage point",
     "mutate": lambda inp: replace(inp, exit_cap_rate=inp.exit_cap_rate + 0.01)},
    {"name": "Interest rate +100 bps",
     "delta_label": "Interest rate raised by 1.0 percentage point",
     "mutate": lambda inp: replace(inp, interest_rate=inp.interest_rate + 0.01,
                                    refi_interest_rate=inp.refi_interest_rate + 0.01)},
    {"name": "Price +5%",
     "delta_label": "Purchase price up 5%",
     "mutate": lambda inp: replace(inp, purchase_price=inp.purchase_price * 1.05)},
    {"name": "Price -5%",
     "delta_label": "Purchase price down 5%",
     "mutate": lambda inp: replace(inp, purchase_price=inp.purchase_price * 0.95)},
    {"name": "Price +10%",
     "delta_label": "Purchase price up 10%",
     "mutate": lambda inp: replace(inp, purchase_price=inp.purchase_price * 1.10)},
    {"name": "Price -10%",
     "delta_label": "Purchase price down 10%",
     "mutate": lambda inp: replace(inp, purchase_price=inp.purchase_price * 0.90)},
]


def _result_to_run(name: str, delta_label: str, result: Result,
                   base_irr: Optional[float], base_verdict: Optional[str]) -> SensitivityRun:
    """Pack a Result into a SensitivityRun, marking flips against the base."""
    verdict = result.verdict.recommendation
    binding = [c["metric"] for c in result.verdict.checks if not c["passed"]]
    irr_delta_bps = (result.levered_irr - base_irr) * 10000 if base_irr is not None else 0.0
    flipped_to = verdict if base_verdict and verdict != base_verdict else None
    return SensitivityRun(
        name=name,
        delta_label=delta_label,
        new_irr=result.levered_irr,
        new_dscr=result.dscr_min_observed,
        new_cap_rate=result.cap_rate,
        new_verdict=verdict,
        new_pass_count=result.verdict.pass_count,
        new_fail_count=result.verdict.fail_count,
        binding_constraints=binding,
        irr_delta_bps=irr_delta_bps,
        flipped_to=flipped_to,
    )


def run_sensitivity(inputs: Inputs) -> SensitivityReport:
    """Run base case + all 9 perturbations. Returns a SensitivityReport."""
    base_result = compute_returns(inputs)
    base_run = _result_to_run("Base case", "Inputs as supplied",
                              base_result, None, None)
    base_run.irr_delta_bps = 0.0

    runs: list[SensitivityRun] = []
    for p in PERTURBATIONS:
        try:
            perturbed = p["mutate"](inputs)
            r = compute_returns(perturbed)
            runs.append(_result_to_run(p["name"], p["delta_label"], r,
                                       base_result.levered_irr,
                                       base_result.verdict.recommendation))
        except Exception:
            # Skip a perturbation that breaks the math (rare); leave a marker
            runs.append(SensitivityRun(
                name=p["name"], delta_label=p["delta_label"],
                new_irr=float("nan"), new_dscr=float("nan"),
                new_cap_rate=float("nan"),
                new_verdict="N/A", new_pass_count=0, new_fail_count=0,
                binding_constraints=[], irr_delta_bps=0.0, flipped_to=None,
            ))

    return SensitivityReport(base=base_run, runs=runs)


def format_sensitivity_markdown(report: SensitivityReport) -> list[str]:
    """Render the sensitivity report to Markdown lines suitable for the
    decision memo. Returns a list of lines (no trailing newline)."""
    lines: list[str] = []
    lines.append("## Sensitivity")
    lines.append("")
    lines.append(
        "Each row holds all other inputs fixed and perturbs one assumption. "
        "Bold rows are deal-flippers: the verdict changes versus the base."
    )
    lines.append("")
    lines.append("| Scenario | Change | IRR | IRR delta (bps) | DSCR | Cap | Verdict |")
    lines.append("|---|---|---:|---:|---:|---:|:---:|")

    def _pct(v: float, d: int = 1) -> str:
        if v is None or v != v:  # NaN
            return "n/a"
        return f"{v * 100:.{d}f}%"

    def _ratio(v: float) -> str:
        if v is None or v != v or v == float("inf"):
            return "n/a"
        return f"{v:.2f}x"

    # Base case row
    b = report.base
    lines.append(
        f"| Base case | {b.delta_label} | {_pct(b.new_irr)} | — | "
        f"{_ratio(b.new_dscr)} | {_pct(b.new_cap_rate)} | **{b.new_verdict}** |"
    )
    # Perturbation rows
    for r in report.runs:
        bold = "**" if r.flipped_to else ""
        delta = f"{r.irr_delta_bps:+.0f}" if r.new_irr == r.new_irr else "n/a"
        lines.append(
            f"| {bold}{r.name}{bold} | {r.delta_label} | "
            f"{_pct(r.new_irr)} | {delta} | "
            f"{_ratio(r.new_dscr)} | {_pct(r.new_cap_rate)} | "
            f"**{r.new_verdict}** |"
        )
    lines.append("")

    # What-would-change block (T-304)
    flippers = report.flippers
    if flippers:
        lines.append("### What would change the recommendation")
        lines.append("")
        for f in flippers:
            binding = (", ".join(f.binding_constraints)
                       if f.binding_constraints else "no thresholds")
            lines.append(
                f"- **{f.name}** → verdict shifts from "
                f"**{report.base.new_verdict}** to **{f.new_verdict}**. "
                f"Failing at that point: {binding}."
            )
        lines.append("")
    else:
        lines.append(
            "> No single perturbation in the canonical 9 changes the verdict. "
            "The recommendation is robust across the sensitivity band."
        )
        lines.append("")

    return lines
