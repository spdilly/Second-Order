"""
Markdown report generator.

Renders the property analysis decision summary from a compute.Result and the
lookup provenance dict. The report goes back to Joe in chat and is also saved
to disk alongside the populated Excel.

Format:
  - One-page markdown
  - Headline verdict at top
  - Key metrics table
  - Assumptions table with source citations
  - Per-threshold pass/fail breakdown
  - Year-by-year cashflow summary
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from scripts.property_analysis.compute import Result, Inputs


def _pct(v: float, decimals: int = 1) -> str:
    if v is None or v != v:  # NaN
        return "n/a"
    return f"{v * 100:.{decimals}f}%"


def _money(v: float) -> str:
    if v is None or v != v:
        return "n/a"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}"


def _ratio(v: float) -> str:
    if v is None or v != v or v == float("inf"):
        return "n/a"
    return f"{v:.2f}x"


def render_report(result: Result, provenance: Optional[dict] = None,
                  output_xlsx_path: Optional[Path] = None,
                  max_bid_result=None, price_provided: bool = True,
                  rent_variance: Optional[dict] = None,
                  provenance_map: Optional[dict] = None,
                  rentcast_mode: Optional[str] = None,
                  placeholder_underwriting: bool = False,
                  diagnostic_only: bool = False,
                  blockers: Optional[list[str]] = None,
                  wprdc_lookup=None, wprdc_details=None,
                  sensitivity_report=None,
                  diligence_questions: Optional[list] = None) -> str:
    """
    Render the markdown report.

    Args:
        result: compute.Result
        provenance: dict of {input_name: (value, source, note)} from lookup chain.
        output_xlsx_path: optional path to the populated Excel for the report footer.
        max_bid_result: optional MaxBidResult from max_bid.solve_max_bid()
        price_provided: True if user supplied a price; False = address-only mode.
    """
    provenance = provenance or {}
    inp = result.inputs
    v = result.verdict

    badge = {
        "BUY":      "**[BUY]**",
        "CONSIDER": "**[CONSIDER]**",
        "PASS":     "**[PASS — DO NOT BUY]**",
    }[v.recommendation]

    lines = []
    lines.append(f"# Property Analysis — {inp.address or 'Property'}")
    lines.append("")
    lines.append(f"_{datetime.now().strftime('%B %d, %Y')} · Section 8 SFH proforma_")
    lines.append("")

    # ── Diagnostic-only mode (any blocker present) ──
    # When ANY blocker is active, the report MUST NOT display a recommendation.
    # The verdict is computed against incomplete data; showing it would imply
    # confidence the model does not have. Instead, show a hard "Diagnostic Only"
    # block with the specific blockers and the fix needed for each.
    if diagnostic_only:
        lines.append("## DIAGNOSTIC ONLY — NO RECOMMENDATION PRODUCED")
        lines.append("")
        lines.append(
            "> This report cannot produce a buy/no-buy decision because one or more "
            "load-bearing inputs are missing. The numbers below are shown for "
            "diagnostic value only and should NOT be acted on. Resolve the blockers "
            "below and re-run."
        )
        lines.append("")
        lines.append("**Blockers preventing a real recommendation:**")
        lines.append("")
        for i, b in enumerate(blockers or [], start=1):
            lines.append(f"{i}. {b}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # ── Headline: bid-guidance framing when no price supplied ──
    # Skip the verdict/bid headline entirely in diagnostic mode.
    if diagnostic_only:
        pass  # No headline; the diagnostic block above replaces it
    elif not price_provided:
        lines.append("## Bid Guidance: No Price Supplied")
        lines.append("")
        if max_bid_result is not None and max_bid_result.max_bid.price:
            lines.append(f"- **Recommended Max Bid: ${max_bid_result.max_bid.price:,.0f}** (full BUY threshold)")
        else:
            lines.append("- **Max Bid (full BUY): not achievable** at current assumptions")
        if max_bid_result is not None and max_bid_result.stretch_bid.price:
            lines.append(f"- Stretch Bid: ${max_bid_result.stretch_bid.price:,.0f} (CONSIDER ceiling)")
        if max_bid_result is not None and max_bid_result.do_not_cross.price:
            lines.append(f"- Do Not Cross: ${max_bid_result.do_not_cross.price:,.0f}")
        lines.append("")
        if max_bid_result is not None and not max_bid_result.max_bid.price:
            binding = ", ".join(max_bid_result.stretch_bid.binding) if max_bid_result.stretch_bid.binding else "multiple"
            lines.append(
                f"> No price clears all BUY thresholds under current assumptions. "
                f"Best actionable ceiling is the stretch bid at "
                f"${max_bid_result.stretch_bid.price:,.0f}. "
                f"Binding constraints at that price: **{binding}**."
            )
            lines.append("")
            lines.append(
                "> _This is not a rejection of the deal. The model applies institutional "
                "defaults — CapEx reserve, escalating expenses, full cost of sale — that "
                "tend to be more conservative than back-of-the-envelope underwriting. "
                "Read 'binding constraints' as 'where the deal would need to bend to clear,' "
                "not as 'the deal is bad.'_"
            )
            lines.append("")
    else:
        # Price supplied → show verdict at that price + bid context
        lines.append(f"## Recommendation: {badge} at ${inp.purchase_price:,.0f}")
        lines.append("")
        if max_bid_result is not None:
            if max_bid_result.max_bid.price:
                lines.append(f"- Recommended Max Bid: **${max_bid_result.max_bid.price:,.0f}** (full BUY threshold)")
            else:
                lines.append("- Recommended Max Bid: **not achievable** at current assumptions")
            if max_bid_result.stretch_bid.price:
                lines.append(f"- Stretch Ceiling: ${max_bid_result.stretch_bid.price:,.0f} (CONSIDER ceiling)")
            if max_bid_result.do_not_cross.price:
                lines.append(f"- Do Not Cross: ${max_bid_result.do_not_cross.price:,.0f}")
        lines.append("")
        lines.append(f"**{v.pass_count} of {v.pass_count + v.fail_count} thresholds passed at the offered price.**")
        lines.append("")
        if max_bid_result is not None and max_bid_result.max_bid.price:
            offered = inp.purchase_price
            max_bid_price = max_bid_result.max_bid.price
            if offered <= max_bid_price:
                headroom = max_bid_price - offered
                lines.append(f"> Offered price is **${headroom:,.0f} below max bid** — room to negotiate up if needed.")
            else:
                overage = offered - max_bid_price
                lines.append(f"> Offered price is **${overage:,.0f} above max bid** — at this price, at least one threshold fails.")
            lines.append("")
        elif max_bid_result is not None and not max_bid_result.max_bid.price:
            lines.append(
                "> _Max bid 'not achievable' means no price clears all BUY thresholds at the current assumption stack. "
                "The deal can still clear at CONSIDER level. The model applies institutional defaults "
                "(CapEx reserve, expense escalation, full cost of sale) that are more conservative than "
                "a back-of-the-envelope underwrite — read this as 'where the deal needs to bend,' not 'the deal is bad.'_"
            )
            lines.append("")

    # ── Scenario snapshot table (Option 3) ──
    if max_bid_result is not None:
        # Build scenarios if not already attached
        scenarios = max_bid_result.scenarios
        if not scenarios:
            from scripts.property_analysis.max_bid import build_scenarios
            scenarios = build_scenarios(inp, max_bid_result)
            max_bid_result.scenarios = scenarios

        lines.append("## Scenario Snapshot")
        lines.append("")
        lines.append("| Scenario | Price | Verdict | IRR | DSCR | Cash-on-Cash | Cap Rate | Binding |")
        lines.append("|---|---:|:---:|---:|---:|---:|---:|---|")
        for s in scenarios:
            if s["price"] is None:
                lines.append(f"| {s['display']} | n/a | — | — | — | — | — | {', '.join(s['binding']) if s['binding'] else 'none'} |")
                continue
            irr_s   = _pct(s["irr"], 1)
            dscr_s  = _ratio(s["dscr"])
            coc_s   = _pct(s["coc"], 1)
            cap_s   = _pct(s["cap_rate"], 1)
            binding = ", ".join(s["binding"]) if s["binding"] else "—"
            lines.append(
                f"| {s['display']} | {_money(s['price'])} | **{s['verdict']}** | "
                f"{irr_s} | {dscr_s} | {coc_s} | {cap_s} | {binding} |"
            )
        lines.append("")

    # ── Rent Stack (HUD vs RentCast variance) ──
    # Mode banner — always shown when there is any RentCast involvement.
    # This is the trust layer: report consumer must know if data is live, demo, or absent.
    mode_label = (rentcast_mode or "unknown").lower()
    mode_banner = {
        "live":    "RentCast mode: **LIVE** (real-time API)",
        "fixture": "RentCast mode: **FIXTURE** (canned demo data — not a real market estimate)",
        "off":     "RentCast mode: **OFF** (market variance check skipped)",
        "unknown": "RentCast mode: unknown",
    }.get(mode_label, f"RentCast mode: {mode_label}")

    if rent_variance is not None:
        flag_label = {
            "aligned":  "ALIGNED",
            "moderate": "MODERATE",
            "high":     "HIGH",
        }.get(rent_variance["flag"], "—")
        lines.append("## Rent Stack")
        lines.append("")
        lines.append(mode_banner)
        lines.append("")
        if mode_label == "fixture":
            lines.append("> **WARNING**: market rent below is from a local fixture file, not the live RentCast API. Variance numbers are illustrative only. Set `RENTCAST_API_KEY` and `RENTCAST_MODE=live` for real comparables.")
            lines.append("")
        lines.append("| Source | Monthly Rent | Use |")
        lines.append("|---|---:|---|")
        lines.append(f"| HUD SAFMR / FMR (underwriting) | {_money(rent_variance['underwriting_rent'])} | Section 8 paid rent |")
        lines.append(f"| RentCast (market estimate) | {_money(rent_variance['market_rent'])} | Open-market sanity check |")
        lines.append(f"| Variance | {rent_variance['pct']:+.1%} | **{flag_label}** |")
        lines.append("")
        lines.append(f"> {rent_variance['message']}")
        lines.append("")
    elif provenance_map and "market_rent" in provenance_map:
        # Show rent provenance even without variance (e.g. when RentCast off)
        mr = provenance_map["market_rent"]
        lines.append("## Rent Stack")
        lines.append("")
        lines.append(mode_banner)
        lines.append("")
        lines.append("| Source | Monthly Rent | Use |")
        lines.append("|---|---:|---|")
        underwriting = provenance_map.get("monthly_rent")
        if underwriting and underwriting.value:
            lines.append(f"| HUD SAFMR / FMR (underwriting) | {_money(underwriting.value)} | Section 8 paid rent |")
        lines.append(f"| RentCast (market estimate) | n/a | adapter {mode_label} or returned no data |")
        lines.append("")
        if mode_label == "off":
            lines.append("> Market rent variance check skipped (RentCast adapter is OFF).")
        else:
            lines.append(f"> Market rent variance check skipped (RentCast {mode_label} returned no estimate for this address).")
        lines.append("")

    # ── Property Facts (WPRDC / Allegheny) ──
    # Show this when the WPRDC adapter returned real parcel data. For non-Allegheny
    # addresses or lookup failures, omit the section entirely; the source-audit
    # table still captures the failed attempt.
    if wprdc_details and wprdc_lookup and wprdc_lookup.found:
        lines.append("## Property Facts (Allegheny County / WPRDC)")
        lines.append("")
        lines.append(f"Parcel ID: `{wprdc_lookup.parcel_id}` · "
                     f"Municipality: {wprdc_details.municipality or wprdc_lookup.municipality} · "
                     f"School: {wprdc_details.school_district or '—'}")
        lines.append("")
        lines.append("| Fact | Value | Source |")
        lines.append("|---|---:|---|")
        if wprdc_details.beds is not None:
            lines.append(f"| Bedrooms | {wprdc_details.beds} | Allegheny assessment record |")
        if wprdc_details.total_baths is not None:
            lines.append(f"| Baths (full + half) | {wprdc_details.total_baths} | Allegheny assessment record |")
        if wprdc_details.finished_sqft:
            lines.append(f"| Finished sqft | {wprdc_details.finished_sqft:,} | Allegheny assessment record |")
        if wprdc_details.year_built:
            lines.append(f"| Year built | {wprdc_details.year_built} | Allegheny assessment record |")
        if wprdc_details.property_use:
            lines.append(f"| Use / style | {wprdc_details.property_use} / {wprdc_details.style or '—'} | Allegheny assessment record |")
        if wprdc_details.condition:
            cond = wprdc_details.condition
            warning = " **(FLAG)**" if cond.upper() in ("POOR", "UNSOUND", "VERY POOR") else ""
            lines.append(f"| Condition | {cond}{warning} | Allegheny assessment record |")
        if wprdc_details.assessed_total:
            lines.append(f"| Assessed value (county) | {_money(wprdc_details.assessed_total)} | Allegheny assessment record |")
        if wprdc_details.last_sale_price is not None and wprdc_details.last_sale_date:
            lines.append(f"| Last sale | {_money(wprdc_details.last_sale_price)} on {wprdc_details.last_sale_date} | Allegheny sales record |")
        if wprdc_details.prev_sale_price is not None and wprdc_details.prev_sale_date:
            lines.append(f"| Prior sale | {_money(wprdc_details.prev_sale_price)} on {wprdc_details.prev_sale_date} | Allegheny sales record |")
        if wprdc_details.tax_year:
            lines.append(f"| Tax year on record | {wprdc_details.tax_year} | Allegheny assessment record |")
        lines.append("")

        # Highlight notable sales-history signals
        if wprdc_details.last_sale_price is not None and wprdc_details.last_sale_price <= 5000:
            lines.append(
                "> **Sales history flag**: last recorded sale was at a nominal price "
                f"({_money(wprdc_details.last_sale_price)} on {wprdc_details.last_sale_date}). "
                "This is typically a family transfer, quitclaim, or distress sale, not a market comp."
            )
            lines.append("")
        if wprdc_details.condition and wprdc_details.condition.upper() in ("UNSOUND", "VERY POOR"):
            lines.append(
                f"> **Condition flag**: county records the building as **{wprdc_details.condition}**. "
                "Verify rehab budget covers structural / habitability work before bidding."
            )
            lines.append("")

    # ── Key metrics table ──
    lines.append("## Key Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Total Cash Invested | {_money(result.total_cash_invested)} |")
    lines.append(f"| Year 1 NOI | {_money(result.annuals[1].noi)} |")
    lines.append(f"| Cap Rate (Y1 / Cash) | {_pct(result.cap_rate, 2)} |")
    lines.append(f"| Cap Rate (Y1 / Purchase) | {_pct(result.cap_rate_on_purchase, 2)} |")
    lines.append(f"| Cash-on-Cash (Y1) | {_pct(result.cash_on_cash_y1, 2)} |")
    lines.append(f"| Cash-on-Cash (Y2 stabilized) | {_pct(result.cash_on_cash_stabilized, 2)} |")
    lines.append(f"| DSCR (Year 1) | {_ratio(result.dscr_y1)} |")
    lines.append(f"| DSCR (min observed) | {_ratio(result.dscr_min_observed)} |")
    lines.append(f"| Levered IRR ({inp.hold_period_yrs}-yr) | {_pct(result.levered_irr, 2)} |")
    lines.append(f"| Unlevered IRR | {_pct(result.unlevered_irr, 2)} |")
    lines.append(f"| Equity Multiple | {_ratio(result.equity_multiple)} |")
    lines.append(f"| Year {inp.hold_period_yrs} Exit Value | {_money(result.exit_value)} |")
    lines.append(f"| Net Sale Proceeds | {_money(result.net_sale_proceeds)} |")
    lines.append(f"| Price / ARV | {_pct(result.price_to_arv, 1)} |")
    lines.append("")

    # ── Buy/no-buy detail ──
    lines.append("## Decision Breakdown")
    lines.append("")
    lines.append("| Metric | Value | Threshold | Status | Magnitude |")
    lines.append("|---|---:|---:|:---:|---|")
    for c in v.checks:
        if c["format"] == "pct":
            val_str = _pct(c["value"], 2)
            th_str = _pct(c["threshold"], 1)
        else:
            val_str = _ratio(c["value"])
            th_str = _ratio(c["threshold"])
        status = "PASS" if c["passed"] else "FAIL"
        op = "<=" if c.get("inverted") else ">="
        # T-302: binding-constraint magnitude — say by how much the threshold misses
        if c["passed"]:
            mag = "—"
        else:
            if c["format"] == "pct":
                diff_pp = abs(c["value"] - c["threshold"]) * 100
                mag = f"{diff_pp:.1f} pp {'over' if c.get('inverted') else 'short'}"
            else:
                diff = abs(c["value"] - c["threshold"])
                mag = f"{diff:.2f}x {'over' if c.get('inverted') else 'short'}"
        lines.append(f"| {c['metric']} | {val_str} | {op} {th_str} | **{status}** | {mag} |")
    lines.append("")

    # ── Sensitivity (Phase 3, T-301 + T-304) ──
    if sensitivity_report is not None and not diagnostic_only:
        try:
            from scripts.property_analysis.sensitivity import format_sensitivity_markdown
            lines.extend(format_sensitivity_markdown(sensitivity_report))
        except Exception:
            pass

    # ── Diligence questions (Phase 3, T-303) ──
    if diligence_questions:
        try:
            from scripts.property_analysis.diligence import format_diligence_markdown
            lines.extend(format_diligence_markdown(diligence_questions))
        except Exception:
            pass

    # ── Assumptions with sources ──
    lines.append("## Assumptions")
    lines.append("")
    lines.append("| Input | Value | Source |")
    lines.append("|---|---:|---|")
    assumption_rows = [
        ("Address",             inp.address,                                 provenance.get("address", "user input")),
        ("ZIP",                 inp.zip,                                     provenance.get("zip", "user input")),
        ("Bedrooms",            inp.beds,                                    provenance.get("beds", "default")),
        ("Purchase Price",      _money(inp.purchase_price),                  provenance.get("purchase_price", "user input")),
        ("ARV",                 _money(inp.arv),                             provenance.get("arv", "user input")),
        ("Rehab",               _money(inp.rehab_cost),                      provenance.get("rehab_cost", "user input")),
        ("Monthly Rent",        _money(inp.monthly_rent),                    provenance.get("monthly_rent", "user input")),
        ("Property Tax (annual)", _money(inp.property_tax_annual),           provenance.get("property_tax_annual", "user input")),
        ("Insurance (annual)",  _money(inp.insurance_annual),                provenance.get("insurance_annual", "default")),
        ("Vacancy",             _pct(inp.vacancy_rate),                      provenance.get("vacancy_rate", "default")),
        ("Down Payment",        _pct(inp.down_payment_pct),                  provenance.get("down_payment_pct", "user input")),
        ("Interest Rate",       _pct(inp.interest_rate, 2),                  provenance.get("interest_rate", "user input")),
        ("Refi Year",           inp.refi_year if inp.refi_year else "No refi", provenance.get("refi_year", "user input")),
        ("Refi LTV (post-refi)", _pct(inp.refi_ltv) if inp.refi_year else "-", provenance.get("refi_ltv", "user input")),
        ("Hold Period",         f"{inp.hold_period_yrs} years",              provenance.get("hold_period_yrs", "default")),
        ("Exit Cap Rate",       _pct(inp.exit_cap_rate, 2),                  provenance.get("exit_cap_rate", "default")),
    ]
    for label, val, src in assumption_rows:
        lines.append(f"| {label} | {val} | {src} |")
    lines.append("")

    # ── Cash flow summary ──
    lines.append(f"## Cashflow Summary (10-Year)")
    lines.append("")
    lines.append("| Year | Gross Rent | NOI | Debt Service | DSCR | Net Cash Flow |")
    lines.append("|:---:|---:|---:|---:|:---:|---:|")
    for a in result.annuals[1:]:
        dscr_str = _ratio(a.dscr) if a.debt_service < 0 else "n/a"
        lines.append(
            f"| {a.year} | {_money(a.gross_rent)} | {_money(a.noi)} | "
            f"{_money(a.debt_service)} | {dscr_str} | {_money(a.net_cash_flow)} |"
        )
    lines.append("")

    # ── BRRRR note if applicable ──
    if inp.refi_year > 0 and inp.refi_year <= inp.hold_period_yrs:
        refi_a = result.annuals[inp.refi_year]
        lines.append(f"### Refinance — Year {inp.refi_year}")
        lines.append("")
        lines.append(
            f"At year {inp.refi_year}, the property refinances at "
            f"{_pct(inp.refi_ltv)} LTV against the ARV of {_money(inp.arv)}, "
            f"producing {_money(inp.arv * inp.refi_ltv)} in loan proceeds. "
            f"After paying off the original loan and refi closing costs, "
            f"**{_money(refi_a.refi_cash_out)}** returns to the investor."
        )
        lines.append("")
        if result.equity_post_refi > 0:
            lines.append(
                f"Equity remaining in the deal after refi: "
                f"**{_money(result.equity_post_refi)}** (was {_money(result.initial_equity)} at acquisition)."
            )
        else:
            lines.append(
                f"All initial equity is returned at refi. The deal becomes infinite-return on remaining equity."
            )
        lines.append("")

    # ── Footer ──
    lines.append("---")
    lines.append("")
    if output_xlsx_path:
        lines.append(f"**Excel model**: `{output_xlsx_path}`")
        lines.append("")
    lines.append(
        "Built by Sean Dillard | [seandillard.com](https://www.seandillard.com) | "
        "Property analysis skill v1"
    )

    return "\n".join(lines)


def save_report(result: Result, output_dir: Path | str,
                property_label: str, provenance: Optional[dict] = None,
                output_xlsx_path: Optional[Path] = None,
                max_bid_result=None, price_provided: bool = True,
                rent_variance: Optional[dict] = None,
                provenance_map: Optional[dict] = None,
                rentcast_mode: Optional[str] = None,
                placeholder_underwriting: bool = False,
                diagnostic_only: bool = False,
                blockers: Optional[list[str]] = None,
                wprdc_lookup=None, wprdc_details=None,
                sensitivity_report=None,
                diligence_questions: Optional[list] = None) -> Path:
    """Render and save the markdown report inside the per-deal packet folder."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{property_label}_report.md"
    md = render_report(result, provenance, output_xlsx_path,
                       max_bid_result=max_bid_result,
                       price_provided=price_provided,
                       rent_variance=rent_variance,
                       provenance_map=provenance_map,
                       rentcast_mode=rentcast_mode,
                       placeholder_underwriting=placeholder_underwriting,
                       diagnostic_only=diagnostic_only,
                       blockers=blockers,
                       wprdc_lookup=wprdc_lookup,
                       wprdc_details=wprdc_details,
                       sensitivity_report=sensitivity_report,
                       diligence_questions=diligence_questions)
    path.write_text(md, encoding="utf-8")
    return path


if __name__ == "__main__":
    from scripts.property_analysis.compute import compute_returns

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
        hoa_annual=0, utilities_annual=0, expense_growth=0.03,
        hold_period_yrs=10, exit_cap_rate=0.08, cost_of_sale_pct=0.01,
    )
    result = compute_returns(inputs)
    provenance = {
        "monthly_rent": "Property_Overrides",
        "property_tax_annual": "Property_Overrides",
        "beds": "Property_Overrides",
    }
    md = render_report(result, provenance)
    print(md)
