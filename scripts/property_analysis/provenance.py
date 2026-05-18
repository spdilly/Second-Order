"""
Source-of-truth audit trail for every input the model uses.

Every value the model consumes carries:
  - the resolved value
  - the source that won
  - a confidence level
  - the full list of attempts (sources tried, what each said)
  - an optional reconciliation note when multiple sources agreed/disagreed

This is what turns the tool from a calculator into an institutional underwriter:
the IC memo and source-audit report read directly from Provenance objects.

Confidence levels:
  - "high"   — authoritative source matched (Property_Overrides, HUD SAFMR, exact
                multi-source agreement)
  - "medium" — defensible single source (county FMR fallback, prior-year × 1.10,
                RentCast estimate, multi-source agreement with one outlier)
  - "low"    — fallback default or single weak source
  - "manual" — value unavailable; user must provide
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ──────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────

CONFIDENCE_LEVELS = ("high", "medium", "low", "manual")

# Source priority ranking — earlier sources win when reconciling.
# Used when multiple sources return values for the same field.
#
# Note: HUD SAFMR/FMR is ranked higher than RentCast for `monthly_rent` because
# Joe underwrites Section 8 — HUD is what he's actually paid, RentCast market
# rent is a separate signal (`market_rent`) used for the variance flag.
SOURCE_PRIORITY: dict[str, int] = {
    "prompt":              100,   # explicit user input from CLI/skill prompt
    "Property_Overrides":   90,   # Joe's curated overrides
    "WPRDC (Allegheny)":    80,   # county parcel data (Phase 2)
    "HUD SAFMR":            75,   # ZIP-level Section 8 rent (canonical for Joe)
    "HUD FMR":              70,   # county-level Section 8 rent (fallback)
    "Realtor.com":          65,   # listing data
    "RentCast":             60,   # property records + market rent estimate
    "Realtor.com prior_year+10%": 50,
    "millage_estimate":     30,   # tax estimated from purchase price × millage
    "HUD FMR rule_of_thumb":25,
    "default":              10,   # template default value
    "manual_required":       0,   # placeholder — must be supplied
}


# ──────────────────────────────────────────────
# DATACLASSES
# ──────────────────────────────────────────────

@dataclass
class Attempt:
    """A single attempt to resolve a value from one source."""
    source: str
    value: Optional[Any]   # None = source returned nothing (not in DB, API failed)
    succeeded: bool
    note: str = ""

    def label(self) -> str:
        """Short label for tables."""
        if self.succeeded and self.value is not None:
            return f"{self.source}: {self._fmt(self.value)}"
        if self.value is not None:
            return f"{self.source}: {self._fmt(self.value)} (not used)"
        return f"{self.source}: no result"

    def _fmt(self, v: Any) -> str:
        if isinstance(v, float):
            if abs(v) >= 100:
                return f"${v:,.0f}"
            return f"{v:.2f}"
        return str(v)


@dataclass
class Provenance:
    """How a single input value was determined."""
    name: str                              # field name e.g. "monthly_rent"
    value: Any                             # the value the model will use
    source: str                            # the winning source label
    confidence: str                        # one of CONFIDENCE_LEVELS
    attempts: list[Attempt] = field(default_factory=list)
    note: str = ""
    reconciliation: Optional[str] = None   # set when multiple sources agreed/conflicted

    @property
    def is_resolved(self) -> bool:
        return self.value is not None and self.confidence != "manual"

    def to_audit_row(self) -> dict:
        """One row of the source-audit table in the IC memo."""
        return {
            "input": self.name,
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "note": self.note or self.reconciliation or "",
            "attempts": [a.label() for a in self.attempts],
        }

    def fmt_value(self) -> str:
        """Format value for the report."""
        v = self.value
        if v is None:
            return "missing"
        if isinstance(v, float):
            if 0 < abs(v) < 1:
                return f"{v * 100:.1f}%"
            if abs(v) >= 100:
                return f"${v:,.0f}"
            return f"{v:.2f}"
        return str(v)


# ──────────────────────────────────────────────
# BUILDERS
# ──────────────────────────────────────────────

def resolve(name: str, attempts: list[Attempt],
            default: Optional[Any] = None,
            default_confidence: str = "low") -> Provenance:
    """
    Pick a winning value from a list of Attempts.

    Logic:
      1. Of the attempts that returned a value, pick the highest-priority source.
      2. If multiple high-priority sources agree, mark confidence as "high".
      3. If they disagree, prefer the highest-priority and note the conflict.
      4. If nothing returned, fall through to default.
    """
    # Mark which attempts succeeded based on whether they have a value
    succeeded_attempts = [a for a in attempts if a.value is not None]

    if not succeeded_attempts:
        # Fall through to default
        if default is not None:
            return Provenance(
                name=name, value=default, source="default",
                confidence=default_confidence,
                attempts=attempts,
                note="No source returned a value; using template default.",
            )
        # No default either — manual required
        return Provenance(
            name=name, value=None, source="manual_required",
            confidence="manual",
            attempts=attempts,
            note="Could not resolve from any source. User must provide.",
        )

    # Sort by source priority (highest first)
    def priority(a: Attempt) -> int:
        return SOURCE_PRIORITY.get(a.source, 0)

    succeeded_attempts.sort(key=priority, reverse=True)
    winner = succeeded_attempts[0]

    # Mark the winner's `succeeded` flag in the original list
    for a in attempts:
        a.succeeded = (a is winner)

    confidence = _confidence_for(winner.source, succeeded_attempts)
    reconciliation = _reconcile(succeeded_attempts)

    return Provenance(
        name=name,
        value=winner.value,
        source=winner.source,
        confidence=confidence,
        attempts=attempts,
        note=winner.note,
        reconciliation=reconciliation,
    )


def _confidence_for(winning_source: str, succeeded: list[Attempt]) -> str:
    """Confidence depends on source authority and corroboration."""
    high_authority = ("prompt", "Property_Overrides", "HUD SAFMR", "WPRDC (Allegheny)")
    medium_authority = ("HUD FMR", "Realtor.com", "RentCast",
                        "Realtor.com prior_year+10%")

    base = "low"
    if winning_source in high_authority:
        base = "high"
    elif winning_source in medium_authority:
        base = "medium"

    # Bump up one level if multiple sources agree on the same value
    if len(succeeded) >= 2:
        winner_val = succeeded[0].value
        agreed = sum(1 for a in succeeded if _values_match(a.value, winner_val))
        if agreed >= 2 and base == "medium":
            base = "high"

    return base


def _values_match(a: Any, b: Any, tol: float = 0.10) -> bool:
    """Two values 'agree' if exactly equal or within tolerance (for numerics)."""
    if a == b:
        return True
    try:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if a == 0 or b == 0:
                return abs(a - b) < 1
            return abs(a - b) / max(abs(a), abs(b)) <= tol
    except Exception:
        pass
    return False


def _reconcile(succeeded: list[Attempt]) -> Optional[str]:
    """Build a human-readable reconciliation note if multiple sources returned values."""
    if len(succeeded) < 2:
        return None
    winner = succeeded[0]
    others = succeeded[1:]

    agreed = [a for a in others if _values_match(a.value, winner.value)]
    disagreed = [a for a in others if not _values_match(a.value, winner.value)]

    parts = []
    if agreed:
        sources = ", ".join(a.source for a in agreed)
        parts.append(f"{sources} agrees")
    if disagreed:
        for a in disagreed:
            parts.append(f"{a.source} says {a._fmt(a.value)}")
    if not parts:
        return None
    return f"{winner.source}: {winner._fmt(winner.value)}; " + "; ".join(parts)


# ──────────────────────────────────────────────
# CONVENIENCE: build Provenance from a single value
# ──────────────────────────────────────────────

def single(name: str, value: Any, source: str, confidence: str = "high",
           note: str = "") -> Provenance:
    """Build a Provenance from a single known value (user input, default, etc.)."""
    attempt = Attempt(source=source, value=value, succeeded=True, note=note)
    return Provenance(
        name=name, value=value, source=source,
        confidence=confidence, attempts=[attempt], note=note,
    )


def manual_required(name: str, attempts: Optional[list[Attempt]] = None,
                    note: str = "") -> Provenance:
    """Build a Provenance marking that the value could not be resolved."""
    return Provenance(
        name=name, value=None, source="manual_required",
        confidence="manual", attempts=attempts or [], note=note,
    )


# ──────────────────────────────────────────────
# SMOKE TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Example: beds resolved with two sources agreeing
    p = resolve(
        "beds",
        [
            Attempt("Property_Overrides", 3, succeeded=False),
            Attempt("RentCast", 3, succeeded=False),
            Attempt("Realtor.com", None, succeeded=False),
        ],
        default=3,
    )
    print(f"Beds: value={p.value}, source={p.source}, conf={p.confidence}")
    print(f"  Reconciliation: {p.reconciliation}")
    print(f"  Attempts: {[a.label() for a in p.attempts]}")

    # Example: rent with HUD primary + RentCast secondary disagreeing
    p2 = resolve(
        "monthly_rent",
        [
            Attempt("Property_Overrides", None, succeeded=False),
            Attempt("HUD SAFMR", 1670, succeeded=False),
            Attempt("RentCast", 1525, succeeded=False, note="market rent estimate"),
        ],
    )
    print(f"\nRent: value={p2.value}, source={p2.source}, conf={p2.confidence}")
    print(f"  Reconciliation: {p2.reconciliation}")

    # Example: nothing resolved
    p3 = resolve(
        "property_tax_annual",
        [
            Attempt("Property_Overrides", None, succeeded=False),
            Attempt("Realtor.com", None, succeeded=False),
        ],
    )
    print(f"\nTax: value={p3.value}, source={p3.source}, conf={p3.confidence}")
    print(f"  Note: {p3.note}")
