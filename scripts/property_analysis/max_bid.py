"""
Max-bid solver.

Given Inputs without a purchase_price (or with a placeholder), binary-search
across price to find:
  - max_bid      — highest price where all thresholds pass (verdict = BUY)
  - stretch_bid  — highest price where 1-2 thresholds fail (verdict = CONSIDER)
  - do_not_cross — highest price below the boundary where 3+ thresholds fail

At each tier, identify the binding constraint(s) — the threshold(s) that fail
just above that price level.

This is the feature that turns the tool from "analyze this price" into
"tell me what to offer."
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from scripts.property_analysis.compute import Inputs, Result, compute_returns


@dataclass
class BidLevel:
    label: str                       # "max_bid", "stretch_bid", "do_not_cross"
    price: Optional[float]
    verdict: Optional[str]           # verdict AT this price
    binding: list[str] = field(default_factory=list)
    note: str = ""

    def fmt(self) -> str:
        if self.price is None:
            return f"{self.label}: not found ({self.note})"
        return f"{self.label}: ${self.price:,.0f}"


@dataclass
class MaxBidResult:
    levels: dict[str, BidLevel]
    bounds_searched: tuple[float, float]
    iterations: int
    scenarios: list[dict] = field(default_factory=list)  # populated by build_scenarios()

    @property
    def max_bid(self) -> BidLevel:
        return self.levels["max_bid"]

    @property
    def stretch_bid(self) -> BidLevel:
        return self.levels["stretch_bid"]

    @property
    def do_not_cross(self) -> BidLevel:
        return self.levels["do_not_cross"]


def build_scenarios(base_inputs: Inputs, max_bid_result: MaxBidResult) -> list[dict]:
    """For each bid level, run the engine and return key metrics for a comparison table.

    Returns a list of dicts with: label, price, verdict, irr, dscr, coc, cap_rate.
    """
    scenarios = []
    for label, lvl in max_bid_result.levels.items():
        if lvl.price is None:
            scenarios.append({
                "label": label,
                "display": label.replace("_", " ").title(),
                "price": None,
                "verdict": None,
                "irr": None, "dscr": None, "coc": None, "cap_rate": None,
                "note": lvl.note,
                "binding": lvl.binding,
            })
            continue
        test_inputs = replace(base_inputs, purchase_price=lvl.price)
        r = compute_returns(test_inputs)
        scenarios.append({
            "label": label,
            "display": label.replace("_", " ").title(),
            "price": lvl.price,
            "verdict": r.verdict.recommendation,
            "irr": r.levered_irr,
            "dscr": r.dscr_min_observed,
            "coc": r.cash_on_cash_y1,
            "cap_rate": r.cap_rate,
            "note": lvl.note,
            "binding": lvl.binding,
        })
    return scenarios


def _passes_at(price: float, base_inputs: Inputs, tier: str) -> tuple[bool, Result]:
    """Return (passes_at_tier, result) for a given test price.

    tier='max': verdict == BUY (0 fails)
    tier='stretch': verdict in {BUY, CONSIDER} (≤2 fails)
    tier='dnc': verdict != PASS (≤2 fails; same as stretch — used to anchor)
    """
    test = replace(base_inputs, purchase_price=price)
    r = compute_returns(test)
    fails = r.verdict.fail_count
    if tier == "max":
        return (fails == 0), r
    if tier == "stretch":
        return (fails <= 2), r
    if tier == "dnc":
        # do_not_cross is the highest price where verdict is still CONSIDER
        # (above this, verdict goes PASS = clearly do not bid)
        return (fails <= 2), r
    raise ValueError(f"Unknown tier: {tier}")


def _binary_search(base_inputs: Inputs, tier: str,
                   low: float, high: float, max_iter: int = 30,
                   tolerance: float = 100.0) -> tuple[Optional[float], Optional[Result]]:
    """Find the highest price where the tier predicate is true."""
    # Check that low passes; if not, no price works
    passes_low, _ = _passes_at(low, base_inputs, tier)
    if not passes_low:
        return None, None
    # Check that high fails; if not, expand
    passes_high, _ = _passes_at(high, base_inputs, tier)
    if passes_high:
        # Even highest tested price passes — return high
        return high, _
    # Standard binary search
    last_passing = low
    last_result = None
    for _ in range(max_iter):
        if (high - low) <= tolerance:
            break
        mid = (low + high) / 2
        passes, r = _passes_at(mid, base_inputs, tier)
        if passes:
            last_passing = mid
            last_result = r
            low = mid
        else:
            high = mid
    return last_passing, last_result


def _binding_constraints(price: float, base_inputs: Inputs) -> list[str]:
    """At price, which threshold checks fail? Returns metric names of failing checks."""
    test = replace(base_inputs, purchase_price=price)
    r = compute_returns(test)
    return [c["metric"] for c in r.verdict.checks if not c["passed"]]


def solve_max_bid(base_inputs: Inputs,
                  low: Optional[float] = None,
                  high: Optional[float] = None) -> MaxBidResult:
    """
    Solve for max bid, stretch bid, and do-not-cross prices.

    Args:
        base_inputs: Inputs with all fields populated except purchase_price.
                     The purchase_price field is overwritten during search.
        low: Search floor. Defaults to 10% of ARV (or rehab cost).
        high: Search ceiling. Defaults to 150% of ARV.

    Returns:
        MaxBidResult with three BidLevel objects.
    """
    arv = base_inputs.arv if base_inputs.arv > 0 else max(base_inputs.purchase_price, 100000)
    low = low if low is not None else max(10000.0, arv * 0.10)
    high = high if high is not None else arv * 1.50

    iterations = 0
    levels: dict[str, BidLevel] = {}

    # ── max_bid: highest price where verdict = BUY ──
    max_price, max_result = _binary_search(base_inputs, "max", low, high)
    iterations += 30
    if max_price is None:
        levels["max_bid"] = BidLevel(
            label="max_bid", price=None, verdict=None,
            note=("No price meets all thresholds at current assumptions. "
                  "Either lower thresholds, raise rent, reduce rehab, or pass on the deal."),
        )
    else:
        # Identify the binding constraint just above max_price
        binding = _binding_constraints(max_price * 1.005 + 100, base_inputs)
        levels["max_bid"] = BidLevel(
            label="max_bid", price=max_price,
            verdict="BUY", binding=binding,
            note=f"Above ${max_price:,.0f}, fails: {', '.join(binding) or 'multiple'}",
        )

    # ── stretch_bid: highest price where verdict in {BUY, CONSIDER} ──
    stretch_price, _ = _binary_search(base_inputs, "stretch", low, high)
    iterations += 30
    if stretch_price is None:
        levels["stretch_bid"] = BidLevel(
            label="stretch_bid", price=None, verdict=None,
            note="No price produces a CONSIDER or better.",
        )
    else:
        binding = _binding_constraints(stretch_price * 1.005 + 100, base_inputs)
        # Verdict at stretch price
        test = replace(base_inputs, purchase_price=stretch_price)
        r = compute_returns(test)
        levels["stretch_bid"] = BidLevel(
            label="stretch_bid", price=stretch_price,
            verdict=r.verdict.recommendation, binding=binding,
            note=f"At ${stretch_price:,.0f}: {r.verdict.fail_count} of {r.verdict.fail_count + r.verdict.pass_count} thresholds fail",
        )

    # ── do_not_cross: same as stretch upper bound — above this is clearly PASS ──
    # We define do_not_cross as: the price at which verdict flips to PASS (3+ fails)
    # which is essentially stretch_bid + epsilon.
    if stretch_price is not None:
        # Walk up from stretch_price until verdict becomes PASS
        dnc_price = stretch_price
        for step_pct in (0.005, 0.01, 0.02):
            candidate = stretch_price * (1 + step_pct)
            test = replace(base_inputs, purchase_price=candidate)
            r = compute_returns(test)
            if r.verdict.fail_count >= 3:
                dnc_price = candidate
                break
            dnc_price = candidate
        levels["do_not_cross"] = BidLevel(
            label="do_not_cross", price=dnc_price,
            verdict="PASS (above this point)",
            binding=_binding_constraints(dnc_price, base_inputs),
            note=f"Above ${dnc_price:,.0f}, verdict turns PASS — do not bid.",
        )
    else:
        levels["do_not_cross"] = BidLevel(
            label="do_not_cross", price=None,
            verdict=None,
            note="Any price triggers PASS verdict at current assumptions.",
        )

    return MaxBidResult(levels=levels, bounds_searched=(low, high),
                        iterations=iterations)


def format_max_bid(result: MaxBidResult) -> str:
    """Pretty-print the max bid result for the report."""
    lines = []
    lines.append("Max Bid Analysis")
    lines.append("")
    for label in ("max_bid", "stretch_bid", "do_not_cross"):
        lvl = result.levels[label]
        if lvl.price is None:
            lines.append(f"  {label.replace('_', ' ').title():<14} : not found — {lvl.note}")
            continue
        lines.append(f"  {label.replace('_', ' ').title():<14} : ${lvl.price:>10,.0f}")
        if lvl.binding:
            lines.append(f"  {'binding':<14} : {', '.join(lvl.binding)}")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# SMOKE TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Joe's 1417 S Canal St with his actual assumptions
    inputs = Inputs(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        zip="15215", beds=3,
        purchase_price=130000, arv=255000, rehab_cost=55000,
        closing_cost_pct=0.07, holding_months=3,
        down_payment_pct=1.0, interest_rate=0.095, loan_term_yrs=30,
        refi_year=3, refi_ltv=0.75, refi_interest_rate=0.075,
        refi_term_yrs=30, refi_closing_cost_pct=0.04,
        monthly_rent=2500, rent_growth=0.04, vacancy_rate=0.04,
        management_pct=0.10, maintenance_pct=0.04, capex_reserve_pct=0.0,
        property_tax_annual=3249, insurance_annual=1020,
        hoa_annual=0, utilities_annual=0, expense_growth=0.0,
        hold_period_yrs=10, exit_cap_rate=0.08, cost_of_sale_pct=0.01,
    )
    print(format_max_bid(solve_max_bid(inputs)))
    print()

    # Same inputs but at FMR rent ($1,670) to see how max bid drops
    inputs_fmr = replace(inputs, monthly_rent=1670, capex_reserve_pct=0.05,
                         maintenance_pct=0.05, expense_growth=0.03,
                         cost_of_sale_pct=0.06)
    print("At HUD FMR rent ($1,670) with conservative defaults:")
    print(format_max_bid(solve_max_bid(inputs_fmr)))
