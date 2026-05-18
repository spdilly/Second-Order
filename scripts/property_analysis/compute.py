"""
Section 8 SFH proforma financial engine.

Pure Python — no Excel dependency. Defines the math contract for both the
Excel template and the markdown report. Given a fully-specified Inputs dict,
produces year-by-year cashflows, returns metrics, and a buy/no-buy verdict.

Design principles:
- One source of truth for the financial math (this file).
- Excel template mirrors these formulas, but the report renders from
  Python computations so we don't depend on openpyxl recalculating.
- BRRRR-aware: supports an optional refinance at year N.
- Section 8 oriented: rent grows by HUD's annual FMR reset assumption,
  vacancy assumption is lower than market (Joe can override).

Usage:
    from scripts.property_analysis.compute import Inputs, compute_returns

    inputs = Inputs(
        purchase_price=130000,
        arv=255000,
        rehab_cost=55000,
        ...
    )
    result = compute_returns(inputs)
    print(result.cap_rate, result.cash_on_cash_y1, result.levered_irr)
    print(result.verdict.recommendation)  # "BUY", "CONSIDER", or "PASS"
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# ──────────────────────────────────────────────
# INPUTS
# ──────────────────────────────────────────────

@dataclass
class Inputs:
    """All proforma inputs. Defaults are sensible for Section 8 SFH."""

    # Property identification
    address: str = ""
    zip: str = ""
    beds: int = 3
    baths: float = 1.0

    # Deal particulars
    purchase_price: float = 0.0
    arv: float = 0.0                       # After-repair value
    rehab_cost: float = 0.0
    closing_cost_pct: float = 0.06         # % of purchase price
    holding_months: float = 3.0            # Months of carrying cost during rehab

    # Financing — initial purchase
    down_payment_pct: float = 1.0          # 1.0 = all cash; 0.20 = 20% down
    interest_rate: float = 0.085           # Annual
    loan_term_yrs: int = 30

    # Refinance (BRRRR) — set refi_year=0 to disable
    refi_year: int = 3                     # Year refi happens (1-indexed); 0 = no refi
    refi_ltv: float = 0.75                 # % of ARV
    refi_interest_rate: float = 0.075
    refi_term_yrs: int = 30
    refi_closing_cost_pct: float = 0.04    # % of refi loan amount

    # Operating — rent
    monthly_rent: float = 0.0              # FMR or override
    rent_growth: float = 0.03              # Annual; Section 8 follows HUD reset (~3% historical)
    vacancy_rate: float = 0.04             # Section 8 default; lower than market

    # Operating — expenses (% of gross rent)
    management_pct: float = 0.10
    maintenance_pct: float = 0.05
    capex_reserve_pct: float = 0.05        # CapEx reserve

    # Operating — non-operating expenses (annual $)
    property_tax_annual: float = 0.0
    insurance_annual: float = 1000.0
    utilities_annual: float = 0.0          # If owner-paid (Section 8 often tenant-paid)
    hoa_annual: float = 0.0
    expense_growth: float = 0.03           # Tax / insurance growth

    # Exit
    hold_period_yrs: int = 10
    exit_cap_rate: float = 0.08
    cost_of_sale_pct: float = 0.06         # Realtor + closing

    # Decision thresholds (Joe's overrides plug in here)
    cap_rate_min: float = 0.08
    cash_on_cash_min: float = 0.10
    dscr_min: float = 1.25
    irr_min: float = 0.15
    max_price_to_arv: float = 0.75         # Don't pay more than X% of ARV

    # ── V2 additions: Joe-shape inputs (defaults keep V1 callers working) ──

    # Market context (informational only — drives no math, surfaces on Summary)
    market_rent: float = 0.0               # RentCast / open-market comparable

    # Appreciation drives property-value tracking year-by-year on Summary
    appreciation_rate: float = 0.05        # Joe's historical 5%

    # Acquisition line items Joe tracks explicitly
    wholesaler_fee: float = 0.0
    inspection_fee: float = 350.0
    appraisal_fee: float = 750.0
    holding_utilities_monthly: float = 0.0  # Tax/ins/util carry during rehab

    # GP/LP split — LP gets X% after capital returned, GP gets 1-X%
    gp_lp_split_lp: float = 0.50

    # Provenance / source strings (populated by analyze.py, shown on Inputs +
    # Sources tabs). Pure metadata — does NOT affect math.
    monthly_rent_source: str = ""
    market_rent_source: str = ""
    property_tax_source: str = ""
    insurance_source: str = ""
    arv_source: str = ""
    purchase_price_source: str = ""
    beds_source: str = ""


# ──────────────────────────────────────────────
# OUTPUTS
# ──────────────────────────────────────────────

@dataclass
class AnnualCashflow:
    year: int                              # 0 = acquisition, 1..N = operating years
    gross_rent: float = 0.0
    vacancy_loss: float = 0.0
    egi: float = 0.0                       # Effective gross income
    management: float = 0.0
    maintenance: float = 0.0
    capex_reserve: float = 0.0
    property_tax: float = 0.0
    insurance: float = 0.0
    utilities: float = 0.0
    hoa: float = 0.0
    total_opex: float = 0.0
    noi: float = 0.0
    debt_service: float = 0.0
    interest_paid: float = 0.0
    principal_paid: float = 0.0
    cash_flow_before_tax: float = 0.0      # NOI - debt service
    dscr: float = 0.0
    loan_balance_eop: float = 0.0          # End of period
    # Capital flows
    acquisition_cost: float = 0.0
    refi_proceeds: float = 0.0
    refi_cash_out: float = 0.0             # Net cash returned to investor at refi
    exit_proceeds: float = 0.0
    net_cash_flow: float = 0.0             # Operating + capital flows


@dataclass
class Verdict:
    recommendation: str                    # "BUY", "CONSIDER", "PASS"
    pass_count: int
    fail_count: int
    checks: list[dict] = field(default_factory=list)  # Per-threshold breakdown


@dataclass
class Result:
    inputs: Inputs
    annuals: list[AnnualCashflow]
    cap_rate: float                        # Year 1 NOI / total acquisition cost
    cap_rate_on_purchase: float            # Year 1 NOI / purchase price
    cash_on_cash_y1: float                 # Year 1 levered cash flow / initial equity
    cash_on_cash_stabilized: float         # Year 2 cash-on-cash (avoids partial-year effects)
    dscr_y1: float
    dscr_min_observed: float
    levered_irr: float
    unlevered_irr: float
    equity_multiple: float
    total_cash_invested: float
    initial_equity: float                  # Cash in at acquisition (pre-refi)
    equity_post_refi: float                # Cash remaining after refi cash-out
    exit_value: float
    net_sale_proceeds: float
    price_to_arv: float
    verdict: Verdict


# ──────────────────────────────────────────────
# FINANCIAL PRIMITIVES
# ──────────────────────────────────────────────

def monthly_payment(principal: float, annual_rate: float, term_yrs: int) -> float:
    """Standard PMT: monthly payment on a fully amortizing loan."""
    if principal <= 0 or term_yrs <= 0:
        return 0.0
    r = annual_rate / 12
    n = term_yrs * 12
    if r == 0:
        return principal / n
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def loan_balance(principal: float, annual_rate: float, term_yrs: int,
                 months_paid: int) -> float:
    """Remaining principal after months_paid monthly payments."""
    if principal <= 0 or months_paid <= 0:
        return principal
    r = annual_rate / 12
    n = term_yrs * 12
    if months_paid >= n:
        return 0.0
    if r == 0:
        return principal * (1 - months_paid / n)
    pmt = monthly_payment(principal, annual_rate, term_yrs)
    balance = principal
    for _ in range(months_paid):
        interest = balance * r
        principal_pmt = pmt - interest
        balance -= principal_pmt
    return max(0.0, balance)


def annual_interest_principal(principal: float, annual_rate: float, term_yrs: int,
                              year_index: int) -> tuple[float, float]:
    """Sum of interest and principal paid in a given operating year (1-indexed)."""
    if principal <= 0 or year_index <= 0:
        return 0.0, 0.0
    r = annual_rate / 12
    pmt = monthly_payment(principal, annual_rate, term_yrs)
    start_month = (year_index - 1) * 12
    balance = loan_balance(principal, annual_rate, term_yrs, start_month)
    total_interest = 0.0
    total_principal = 0.0
    for _ in range(12):
        if balance <= 0:
            break
        interest = balance * r
        principal_pmt = min(pmt - interest, balance)
        balance -= principal_pmt
        total_interest += interest
        total_principal += principal_pmt
    return total_interest, total_principal


def irr(cash_flows: list[float], guess: float = 0.1, max_iter: int = 200) -> float:
    """Newton-Raphson IRR. Returns NaN if no solution found."""
    if not cash_flows or all(cf >= 0 for cf in cash_flows) or all(cf <= 0 for cf in cash_flows):
        return float("nan")

    def npv(rate: float) -> float:
        return sum(cf / (1 + rate) ** i for i, cf in enumerate(cash_flows))

    def dnpv(rate: float) -> float:
        return sum(-i * cf / (1 + rate) ** (i + 1) for i, cf in enumerate(cash_flows))

    rate = guess
    for _ in range(max_iter):
        val = npv(rate)
        if abs(val) < 1e-7:
            return rate
        d = dnpv(rate)
        if abs(d) < 1e-12:
            break
        new_rate = rate - val / d
        if new_rate <= -0.99:
            new_rate = (rate - 0.99) / 2
        if abs(new_rate - rate) < 1e-9:
            return new_rate
        rate = new_rate
    return float("nan")


# ──────────────────────────────────────────────
# CORE COMPUTATION
# ──────────────────────────────────────────────

def compute_returns(inputs: Inputs) -> Result:
    """Run the full proforma and return a Result with all metrics."""
    inp = inputs

    # ── Acquisition ──
    closing_cost = inp.purchase_price * inp.closing_cost_pct
    down_payment_dollars = inp.purchase_price * inp.down_payment_pct
    initial_loan = inp.purchase_price - down_payment_dollars
    initial_pmt = monthly_payment(initial_loan, inp.interest_rate, inp.loan_term_yrs)
    # Holding-period carry: mortgage interest + pro-rated fixed expenses
    # + owner-paid holding utilities. Real cash out the LP funds during rehab.
    holding_cost = (
        initial_pmt * inp.holding_months
        + (inp.property_tax_annual / 12) * inp.holding_months
        + (inp.insurance_annual / 12) * inp.holding_months
        + (inp.utilities_annual / 12) * inp.holding_months
        + inp.holding_utilities_monthly * inp.holding_months
    )

    # LP Initial Investment / total cash invested includes every line item
    # the LP funds at close: down payment + closing + rehab + holding carry
    # + acquisition fees. Matches the Excel template's "LP Initial Investment"
    # subtotal on the Summary tab.
    total_cash_invested = (
        down_payment_dollars
        + closing_cost
        + inp.rehab_cost
        + holding_cost
        + inp.wholesaler_fee
        + inp.inspection_fee
        + inp.appraisal_fee
    )
    initial_equity = total_cash_invested

    # ── Refi setup ──
    has_refi = inp.refi_year > 0 and inp.refi_ltv > 0 and inp.arv > 0
    refi_loan = inp.arv * inp.refi_ltv if has_refi else 0.0
    refi_closing = refi_loan * inp.refi_closing_cost_pct if has_refi else 0.0

    # ── Build annual cashflow ──
    annuals: list[AnnualCashflow] = []

    # Year 0 — acquisition
    y0 = AnnualCashflow(year=0)
    y0.acquisition_cost = -total_cash_invested
    y0.net_cash_flow = -total_cash_invested
    annuals.append(y0)

    current_loan_principal = initial_loan
    current_loan_rate = inp.interest_rate
    current_loan_term = inp.loan_term_yrs
    current_loan_start_year = 1

    for yr in range(1, inp.hold_period_yrs + 1):
        a = AnnualCashflow(year=yr)

        # ── Revenue ──
        a.gross_rent = inp.monthly_rent * 12 * (1 + inp.rent_growth) ** (yr - 1)
        a.vacancy_loss = -a.gross_rent * inp.vacancy_rate
        a.egi = a.gross_rent + a.vacancy_loss

        # ── Operating expenses (variable, % of gross rent) ──
        a.management = -a.gross_rent * inp.management_pct
        a.maintenance = -a.gross_rent * inp.maintenance_pct
        a.capex_reserve = -a.gross_rent * inp.capex_reserve_pct

        # ── Non-operating (fixed, with growth) ──
        growth_factor = (1 + inp.expense_growth) ** (yr - 1)
        a.property_tax = -inp.property_tax_annual * growth_factor
        a.insurance = -inp.insurance_annual * growth_factor
        a.utilities = -inp.utilities_annual * growth_factor
        a.hoa = -inp.hoa_annual * growth_factor

        a.total_opex = (
            a.management + a.maintenance + a.capex_reserve
            + a.property_tax + a.insurance + a.utilities + a.hoa
        )
        a.noi = a.egi + a.total_opex   # total_opex is already negative

        # ── Refi event at start of refi_year ──
        if has_refi and yr == inp.refi_year:
            # Pay off existing loan
            months_paid = (yr - current_loan_start_year) * 12
            payoff = loan_balance(
                current_loan_principal, current_loan_rate,
                current_loan_term, months_paid,
            )
            a.refi_proceeds = refi_loan
            cash_to_owner = refi_loan - payoff - refi_closing
            a.refi_cash_out = cash_to_owner

            # Switch to refi loan for remainder of hold
            current_loan_principal = refi_loan
            current_loan_rate = inp.refi_interest_rate
            current_loan_term = inp.refi_term_yrs
            current_loan_start_year = yr

        # ── Debt service for the year ──
        if current_loan_principal > 0:
            year_in_current_loan = yr - current_loan_start_year + 1
            pmt = monthly_payment(
                current_loan_principal, current_loan_rate, current_loan_term,
            )
            a.debt_service = -pmt * 12
            interest, principal = annual_interest_principal(
                current_loan_principal, current_loan_rate, current_loan_term,
                year_in_current_loan,
            )
            a.interest_paid = -interest
            a.principal_paid = -principal
            months_paid_eop = year_in_current_loan * 12
            a.loan_balance_eop = loan_balance(
                current_loan_principal, current_loan_rate, current_loan_term,
                months_paid_eop,
            )
        else:
            a.loan_balance_eop = 0.0

        a.cash_flow_before_tax = a.noi + a.debt_service
        a.dscr = a.noi / -a.debt_service if a.debt_service < 0 else float("inf")

        # ── Exit at end of final year ──
        if yr == inp.hold_period_yrs:
            # Year (N+1) NOI grosses the exit cap rate
            forward_rent = inp.monthly_rent * 12 * (1 + inp.rent_growth) ** yr
            forward_vac = -forward_rent * inp.vacancy_rate
            forward_egi = forward_rent + forward_vac
            forward_opex_var = (
                -forward_rent * (inp.management_pct + inp.maintenance_pct + inp.capex_reserve_pct)
            )
            forward_growth = (1 + inp.expense_growth) ** yr
            forward_opex_fixed = -(
                inp.property_tax_annual + inp.insurance_annual
                + inp.utilities_annual + inp.hoa_annual
            ) * forward_growth
            forward_noi = forward_egi + forward_opex_var + forward_opex_fixed
            exit_value = forward_noi / inp.exit_cap_rate if inp.exit_cap_rate > 0 else 0.0
            cost_of_sale = exit_value * inp.cost_of_sale_pct
            net_sale_proceeds = exit_value - cost_of_sale - a.loan_balance_eop
            a.exit_proceeds = net_sale_proceeds

        a.net_cash_flow = a.cash_flow_before_tax + a.refi_cash_out + a.exit_proceeds
        annuals.append(a)

    # ── Metrics ──
    y1 = annuals[1]
    cap_rate = y1.noi / total_cash_invested if total_cash_invested > 0 else 0.0
    cap_rate_on_purchase = y1.noi / inp.purchase_price if inp.purchase_price > 0 else 0.0

    # Equity post-refi: cash returned reduces the in-deal equity
    refi_cash_returned = sum(a.refi_cash_out for a in annuals)
    equity_post_refi = max(0.0, initial_equity - refi_cash_returned)

    cash_on_cash_y1 = y1.cash_flow_before_tax / initial_equity if initial_equity > 0 else 0.0
    cash_on_cash_stabilized = 0.0
    if len(annuals) >= 3:
        y2 = annuals[2]
        equity_for_y2 = equity_post_refi if has_refi and inp.refi_year <= 2 else initial_equity
        cash_on_cash_stabilized = (
            y2.cash_flow_before_tax / equity_for_y2 if equity_for_y2 > 0 else 0.0
        )

    # DSCR — minimum across operating years where there is debt service
    dscr_with_debt = [a.dscr for a in annuals[1:] if a.debt_service < 0]
    dscr_min_observed = min(dscr_with_debt) if dscr_with_debt else float("inf")

    # IRR — levered uses net cash flow including capital flows
    levered_cfs = [a.net_cash_flow for a in annuals]
    levered_irr = irr(levered_cfs)

    # Unlevered IRR: -(purchase + closing + rehab + holding) + NOI annual + exit (no debt)
    total_property_cost = (
        inp.purchase_price + closing_cost + inp.rehab_cost + holding_cost
    )
    unlevered_cfs = [-total_property_cost]
    for yr in range(1, inp.hold_period_yrs + 1):
        a = annuals[yr]
        # Unlevered: NOI + exit proceeds gross of debt
        ul = a.noi
        if yr == inp.hold_period_yrs:
            # Recompute exit without subtracting loan balance
            forward_rent = inp.monthly_rent * 12 * (1 + inp.rent_growth) ** yr
            forward_vac = -forward_rent * inp.vacancy_rate
            forward_egi = forward_rent + forward_vac
            forward_opex_var = -forward_rent * (
                inp.management_pct + inp.maintenance_pct + inp.capex_reserve_pct
            )
            forward_growth = (1 + inp.expense_growth) ** yr
            forward_opex_fixed = -(
                inp.property_tax_annual + inp.insurance_annual
                + inp.utilities_annual + inp.hoa_annual
            ) * forward_growth
            forward_noi = forward_egi + forward_opex_var + forward_opex_fixed
            exit_value = forward_noi / inp.exit_cap_rate if inp.exit_cap_rate > 0 else 0.0
            cost_of_sale = exit_value * inp.cost_of_sale_pct
            ul += exit_value - cost_of_sale
        unlevered_cfs.append(ul)
    unlevered_irr = irr(unlevered_cfs)

    # Equity multiple — sum of positive cash flows / sum of negative
    inflows = sum(cf for cf in levered_cfs if cf > 0)
    outflows = -sum(cf for cf in levered_cfs if cf < 0)
    equity_multiple = inflows / outflows if outflows > 0 else 0.0

    exit_value = annuals[-1].exit_proceeds + annuals[-1].loan_balance_eop
    net_sale_proceeds = annuals[-1].exit_proceeds
    price_to_arv = inp.purchase_price / inp.arv if inp.arv > 0 else 0.0

    verdict = build_verdict(
        cap_rate=cap_rate,
        cash_on_cash=cash_on_cash_y1,
        dscr=dscr_min_observed,
        irr_=levered_irr,
        price_to_arv=price_to_arv,
        inputs=inp,
    )

    return Result(
        inputs=inp,
        annuals=annuals,
        cap_rate=cap_rate,
        cap_rate_on_purchase=cap_rate_on_purchase,
        cash_on_cash_y1=cash_on_cash_y1,
        cash_on_cash_stabilized=cash_on_cash_stabilized,
        dscr_y1=y1.dscr,
        dscr_min_observed=dscr_min_observed,
        levered_irr=levered_irr,
        unlevered_irr=unlevered_irr,
        equity_multiple=equity_multiple,
        total_cash_invested=total_cash_invested,
        initial_equity=initial_equity,
        equity_post_refi=equity_post_refi,
        exit_value=exit_value,
        net_sale_proceeds=net_sale_proceeds,
        price_to_arv=price_to_arv,
        verdict=verdict,
    )


def build_verdict(
    cap_rate: float, cash_on_cash: float, dscr: float, irr_: float,
    price_to_arv: float, inputs: Inputs,
) -> Verdict:
    """Compare metrics against thresholds. Build pass/fail breakdown and recommendation."""
    checks = [
        {
            "metric": "Cap Rate (Y1)",
            "value": cap_rate,
            "threshold": inputs.cap_rate_min,
            "passed": cap_rate >= inputs.cap_rate_min,
            "format": "pct",
        },
        {
            "metric": "Cash-on-Cash (Y1)",
            "value": cash_on_cash,
            "threshold": inputs.cash_on_cash_min,
            "passed": cash_on_cash >= inputs.cash_on_cash_min,
            "format": "pct",
        },
        {
            "metric": "DSCR (min observed)",
            "value": dscr,
            "threshold": inputs.dscr_min,
            "passed": dscr >= inputs.dscr_min,
            "format": "ratio",
        },
        {
            "metric": "Levered IRR",
            "value": irr_,
            "threshold": inputs.irr_min,
            "passed": (not (irr_ != irr_)) and irr_ >= inputs.irr_min,  # NaN check
            "format": "pct",
        },
        {
            "metric": "Price-to-ARV",
            "value": price_to_arv,
            "threshold": inputs.max_price_to_arv,
            "passed": price_to_arv <= inputs.max_price_to_arv,
            "format": "pct",
            "inverted": True,  # Lower is better
        },
    ]
    fail_count = sum(1 for c in checks if not c["passed"])
    pass_count = len(checks) - fail_count
    if fail_count == 0:
        rec = "BUY"
    elif fail_count <= 2:
        rec = "CONSIDER"
    else:
        rec = "PASS"
    return Verdict(
        recommendation=rec, pass_count=pass_count,
        fail_count=fail_count, checks=checks,
    )


# ──────────────────────────────────────────────
# SMOKE TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Reproduce Joe's 1417 S Canal St assumptions to validate
    inputs = Inputs(
        address="1417 S Canal St, Pittsburgh, PA 15215",
        zip="15215",
        beds=3,
        purchase_price=130000,
        arv=255000,
        rehab_cost=55000,
        closing_cost_pct=0.07,
        holding_months=3,
        down_payment_pct=1.0,            # All cash per Joe's model
        interest_rate=0.095,
        loan_term_yrs=30,
        refi_year=3,
        refi_ltv=0.75,
        refi_interest_rate=0.075,
        refi_term_yrs=30,
        refi_closing_cost_pct=0.04,
        monthly_rent=2500,               # Joe's plug (FMR would say $1,670)
        rent_growth=0.04,
        vacancy_rate=0.04,
        management_pct=0.10,
        maintenance_pct=0.04,
        capex_reserve_pct=0.0,           # Joe doesn't have CapEx reserve in current model
        property_tax_annual=3249,
        insurance_annual=85 * 12,
        hoa_annual=0,
        utilities_annual=0,
        expense_growth=0.0,              # Joe's model doesn't escalate
        hold_period_yrs=10,
        exit_cap_rate=0.08,
        cost_of_sale_pct=0.01,
    )
    r = compute_returns(inputs)
    print(f"Total cash invested: ${r.total_cash_invested:,.0f}")
    print(f"Cap Rate (Y1 / purchase price): {r.cap_rate_on_purchase:.2%}")
    print(f"Cap Rate (Y1 / cash invested):  {r.cap_rate:.2%}")
    print(f"Y1 Cash-on-Cash:                {r.cash_on_cash_y1:.2%}")
    print(f"DSCR (min):                     {r.dscr_min_observed:.2f}x")
    print(f"Levered IRR:                    {r.levered_irr:.2%}")
    print(f"Unlevered IRR:                  {r.unlevered_irr:.2%}")
    print(f"Equity Multiple:                {r.equity_multiple:.2f}x")
    print(f"Exit Value:                     ${r.exit_value:,.0f}")
    print(f"Net Sale Proceeds:              ${r.net_sale_proceeds:,.0f}")
    print(f"Price/ARV:                      {r.price_to_arv:.1%}")
    print(f"\nVerdict: {r.verdict.recommendation} ({r.verdict.pass_count}/{r.verdict.pass_count + r.verdict.fail_count} thresholds passed)")
    for c in r.verdict.checks:
        status = "PASS" if c["passed"] else "FAIL"
        val_str = f"{c['value']:.2%}" if c["format"] == "pct" else f"{c['value']:.2f}x"
        thresh_str = f"{c['threshold']:.2%}" if c["format"] == "pct" else f"{c['threshold']:.2f}x"
        op = "<=" if c.get("inverted") else ">="
        print(f"  [{status}] {c['metric']}: {val_str} {op} {thresh_str}")
