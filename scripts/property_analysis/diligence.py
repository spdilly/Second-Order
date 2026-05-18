"""
Deterministic diligence-question generator — Phase 3 (T-303).

Every question is emitted by a rule. Rules fire against the actual model
state (Provenance + Result + rent variance + WPRDC condition flags +
sales history + sensitivity). Zero LLM input.

Output is a list of (category, question, why_it_fires) tuples. The report
renders them as a checklist.

Rule taxonomy:
  - rent: rent variance > 15%, no override, Section 8 status ambiguous
  - tax: tax confidence is low or comes from estimate/RentCast (not WPRDC override)
  - rehab: rehab/price ratio high, condition UNSOUND/POOR
  - financing: high LTV, low DSCR, refi feasibility
  - sales: nominal-price last sale, no recent arms-length comp
  - threshold: any verdict check fails or sits within ~10% of threshold
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class DiligenceQuestion:
    category: str           # rent, tax, rehab, financing, sales, threshold
    question: str           # the question text Joe asks the seller/inspector/etc.
    why: str                # one-sentence why this fires (audit trail)
    severity: str = "info"  # info, watch, must-ask


def generate(*,
             inputs,                             # compute.Inputs
             result,                             # compute.Result
             provenance_map: dict,               # {name: Provenance}
             rent_variance: Optional[dict] = None,
             wprdc_details=None,                 # wprdc.ParcelDetails or None
             sensitivity_flippers: list = None,  # list[SensitivityRun]
             ) -> list[DiligenceQuestion]:
    """Run every rule and return the diligence-question list.

    Categories are emitted in a deterministic order (rent, tax, rehab,
    financing, sales, threshold) so the report layout is stable across runs.
    """
    out: list[DiligenceQuestion] = []
    sensitivity_flippers = sensitivity_flippers or []

    # ── RENT rules ──
    if rent_variance:
        pct = abs(rent_variance.get("pct") or 0)
        if pct > 0.15:
            out.append(DiligenceQuestion(
                "rent",
                "Confirm Section 8 voucher status and current Payment Standard "
                "for the property's ZIP and bedroom count.",
                f"HUD underwriting rent is {pct:.0%} {'above' if rent_variance.get('delta',0)>0 else 'below'} the RentCast market estimate. "
                "Section 8 status is load-bearing for the underwriting.",
                severity="must-ask",
            ))
            out.append(DiligenceQuestion(
                "rent",
                "Confirm whether the tenant has an active voucher and the housing "
                "authority that issued it.",
                "High rent dependency on Section 8 program continuity.",
                severity="must-ask",
            ))
        elif pct > 0.05:
            out.append(DiligenceQuestion(
                "rent",
                "Confirm the Section 8 occupancy status (current voucher, "
                "expiration, and any payment-standard adjustments).",
                f"Moderate ({pct:.0%}) rent variance between HUD and market estimate.",
                severity="watch",
            ))

    # Always ask current lease terms if rent is the underwriting rent
    mr = provenance_map.get("monthly_rent")
    if mr and mr.value is not None:
        out.append(DiligenceQuestion(
            "rent",
            "What is the current lease rent, lease expiration, and whether the "
            "tenant pays utilities (or owner)?",
            "Lease terms drive year-1 cash flow before any rent reset.",
            severity="must-ask",
        ))

    # ── TAX rules ──
    tax = provenance_map.get("property_tax_annual")
    if tax:
        if tax.confidence == "low" or tax.source in ("default", "manual_required"):
            out.append(DiligenceQuestion(
                "tax",
                "Pull the most recent tax bill from the county assessor and confirm "
                "annual tax dollars, millage components, and any pending appeals.",
                "Tax is not source-anchored (no override, no county-parcel match).",
                severity="must-ask",
            ))
        # If sold this calendar year, reassessment risk
        if wprdc_details and wprdc_details.last_sale_date:
            try:
                year = int(wprdc_details.last_sale_date[-4:])
                if year < 2020 and inputs.purchase_price and wprdc_details.assessed_total:
                    if inputs.purchase_price > wprdc_details.assessed_total * 1.5:
                        out.append(DiligenceQuestion(
                            "tax",
                            "Confirm whether the new purchase will trigger a reassessment "
                            "(purchase price materially exceeds current assessed value).",
                            f"Purchase ${inputs.purchase_price:,.0f} vs assessed "
                            f"${wprdc_details.assessed_total:,.0f}; reassessment is likely.",
                            severity="must-ask",
                        ))
            except ValueError:
                pass

    # ── REHAB rules ──
    if inputs.purchase_price and inputs.rehab_cost is not None and inputs.purchase_price > 0:
        ratio = inputs.rehab_cost / inputs.purchase_price
        if ratio > 0.40:
            out.append(DiligenceQuestion(
                "rehab",
                "Obtain a written contractor scope and 2-3 line-item bids "
                "(structural, mechanical, finish). Add 10-15% contingency.",
                f"Rehab ${inputs.rehab_cost:,.0f} is {ratio:.0%} of purchase — heavy.",
                severity="must-ask",
            ))
        elif ratio > 0.20:
            out.append(DiligenceQuestion(
                "rehab",
                "Confirm rehab scope and contingency. Request a contractor walkthrough "
                "before close.",
                f"Rehab is {ratio:.0%} of purchase — meaningful scope.",
                severity="watch",
            ))

    # WPRDC condition flag
    if wprdc_details and wprdc_details.condition:
        cond = wprdc_details.condition.upper()
        if cond in ("POOR", "UNSOUND", "VERY POOR"):
            out.append(DiligenceQuestion(
                "rehab",
                "Order a structural inspection. Confirm rehab budget accounts for "
                "habitability work (foundation, roof, mechanical).",
                f"County records condition as {wprdc_details.condition}.",
                severity="must-ask",
            ))

    # ── FINANCING rules ──
    dscr_min = result.dscr_min_observed
    if dscr_min < float("inf") and dscr_min < 1.30:
        out.append(DiligenceQuestion(
            "financing",
            "Confirm lender DSCR threshold and whether the deal still qualifies "
            "if rent or expenses come in less favorably.",
            f"Minimum observed DSCR is {dscr_min:.2f}x — close to typical 1.25x lender floor.",
            severity="must-ask",
        ))
    if inputs.refi_year > 0:
        out.append(DiligenceQuestion(
            "financing",
            "Confirm refi feasibility at year {0}: ARV support, debt service ratio "
            "at the refi LTV, and lender appetite at that time.".format(inputs.refi_year),
            f"Refi at Y{inputs.refi_year} at {inputs.refi_ltv:.0%} LTV is load-bearing for the returns.",
            severity="watch",
        ))

    # ── SALES rules ──
    if wprdc_details:
        if wprdc_details.last_sale_price is not None and wprdc_details.last_sale_price <= 5000:
            out.append(DiligenceQuestion(
                "sales",
                "Investigate the most recent recorded sale. Was it an arms-length "
                "market transaction or a family/quitclaim transfer?",
                f"Last sale recorded at ${wprdc_details.last_sale_price:,.0f} "
                f"({wprdc_details.last_sale_date}) is nominal — likely not a market comp.",
                severity="must-ask",
            ))

    # ── THRESHOLD rules ──
    for c in result.verdict.checks:
        if not c["passed"]:
            metric = c["metric"]
            out.append(DiligenceQuestion(
                "threshold",
                f"What inputs would have to change to clear the {metric} threshold? "
                f"Test the assumption you would need to revisit.",
                f"{metric} fails at the offered price.",
                severity="must-ask",
            ))

    # Deal-flippers from sensitivity (T-304 linkage)
    for f in sensitivity_flippers:
        out.append(DiligenceQuestion(
            "threshold",
            f"How confident are we in the assumption underlying '{f.name}'? "
            f"This perturbation alone flips the verdict from "
            f"{f.flipped_to and 'BUY/CONSIDER' or 'the base'} to {f.new_verdict}.",
            f"{f.delta_label} causes verdict to change.",
            severity="watch",
        ))

    return out


def format_diligence_markdown(questions: list[DiligenceQuestion]) -> list[str]:
    """Render the diligence questions to Markdown lines."""
    lines: list[str] = []
    if not questions:
        lines.append("## Diligence Questions")
        lines.append("")
        lines.append("> No automatic diligence questions fired. Run a contractor "
                     "walkthrough and confirm the lease terms regardless.")
        lines.append("")
        return lines

    # Group by category in deterministic order
    order = ["rent", "tax", "rehab", "financing", "sales", "threshold"]
    by_cat: dict[str, list[DiligenceQuestion]] = {k: [] for k in order}
    for q in questions:
        by_cat.setdefault(q.category, []).append(q)

    lines.append("## Diligence Questions")
    lines.append("")
    lines.append("Generated from rules tied to the model state. Each item shows "
                 "the trigger so it can be challenged.")
    lines.append("")

    for cat in order:
        items = by_cat.get(cat, [])
        if not items:
            continue
        lines.append(f"### {cat.title()}")
        lines.append("")
        for q in items:
            sev_tag = {"must-ask": "**[MUST ASK]**", "watch": "_[Watch]_",
                       "info": ""}.get(q.severity, "")
            tag = f" {sev_tag}" if sev_tag else ""
            lines.append(f"- {q.question}{tag}")
            lines.append(f"  _Trigger:_ {q.why}")
        lines.append("")

    return lines
