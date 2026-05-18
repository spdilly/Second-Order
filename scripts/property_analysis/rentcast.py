"""
RentCast adapter — three modes:

  - "live"    : real API calls (requires RENTCAST_API_KEY env var)
  - "fixture" : returns canned data from local JSON fixtures
  - "off"     : returns None for every call

Mode selection priority:
  1. Explicit `mode` argument to RentCastClient(...)
  2. RENTCAST_MODE env var ("live"|"fixture"|"off")
  3. If RENTCAST_API_KEY is set -> "live"
  4. Default -> "off"  (safe production default: no key, no comps, no surprises)

Tests and demos that need canned data must opt into fixture mode explicitly
via mode="fixture" or RENTCAST_MODE=fixture. This prevents fixture data from
ever being shown to a real client by accident.

Live API: https://developers.rentcast.io/reference
Free tier: 50 calls/month, no credit card. Get a key at developers.rentcast.io.

Endpoints we use:
  GET /properties?address=...           — property records (beds/baths/sqft/year/tax)
  GET /avm/rent/long-term?address=...   — rent estimate + comparables

Cache: every successful response (live or fixture) is mirrored to
    scripts/property_analysis/rentcast_cache/<sha256(address)>.json
so repeated lookups against the same address never burn quota.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = Path(__file__).parent / "rentcast_cache"
FIXTURES_DIR = Path(__file__).parent / "rentcast_fixtures"


# ──────────────────────────────────────────────
# RESPONSE TYPES
# ──────────────────────────────────────────────

@dataclass
class PropertyRecord:
    """Subset of RentCast property record we use."""
    address_normalized: Optional[str] = None
    beds: Optional[int] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None
    property_type: Optional[str] = None
    last_sale_price: Optional[float] = None
    last_sale_date: Optional[str] = None
    tax_assessment_annual: Optional[float] = None
    raw: dict = field(default_factory=dict)


@dataclass
class RentEstimate:
    """Subset of RentCast rent estimate we use."""
    rent: Optional[float] = None
    rent_range_low: Optional[float] = None
    rent_range_high: Optional[float] = None
    comparables: list[dict] = field(default_factory=list)
    confidence: Optional[str] = None
    raw: dict = field(default_factory=dict)


# ──────────────────────────────────────────────
# CLIENT
# ──────────────────────────────────────────────

class RentCastClient:
    BASE_URL = "https://api.rentcast.io/v1"

    def __init__(self, mode: Optional[str] = None,
                 api_key: Optional[str] = None,
                 cache_dir: Optional[Path] = None):
        self.mode = self._resolve_mode(mode)
        self.api_key = api_key or os.environ.get("RENTCAST_API_KEY")
        # Per-mode cache namespaces — fixture data CANNOT be read by live mode
        # and vice versa. This is the core trust guarantee for an audit-trail product.
        base_cache = cache_dir or CACHE_DIR
        self.cache_dir = base_cache / self.mode
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

        if self.mode == "live" and not self.api_key:
            raise RuntimeError(
                "RentCast mode='live' requires RENTCAST_API_KEY env var. "
                "Get a free key at developers.rentcast.io or set mode='fixture'."
            )

    @staticmethod
    def _resolve_mode(explicit: Optional[str]) -> str:
        if explicit:
            return explicit
        env_mode = os.environ.get("RENTCAST_MODE")
        if env_mode:
            return env_mode
        if os.environ.get("RENTCAST_API_KEY"):
            return "live"
        # Production default: off. Fixture mode must be explicitly opted into
        # so canned demo data is never shown to a real client by accident.
        return "off"

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def property_records(self, address: str) -> Optional[PropertyRecord]:
        """Fetch beds/baths/sqft/tax for an address."""
        if self.mode == "off":
            return None
        data = self._get_cached_or_fetch("property", address,
                                         path="/properties",
                                         params={"address": address, "limit": 1})
        if not data:
            return None
        return self._parse_property_record(data)

    def rent_estimate(self, address: str,
                      beds: Optional[int] = None,
                      property_type: Optional[str] = None) -> Optional[RentEstimate]:
        """Fetch market rent estimate + comparables."""
        if self.mode == "off":
            return None
        params = {"address": address}
        if beds is not None:
            params["bedrooms"] = beds
        if property_type:
            params["propertyType"] = property_type
        data = self._get_cached_or_fetch("rent", address,
                                         path="/avm/rent/long-term",
                                         params=params)
        if not data:
            return None
        return self._parse_rent_estimate(data)

    # ──────────────────────────────────────────────
    # Internal: fetch + cache
    # ──────────────────────────────────────────────

    def _get_cached_or_fetch(self, kind: str, address: str,
                             path: str, params: dict) -> Optional[dict]:
        cache_key = self._cache_key(kind, address, params)
        cache_file = self.cache_dir / f"{cache_key}.json"

        # Check cache first
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Mode-specific fetch
        if self.mode == "fixture":
            data = self._fetch_fixture(kind, address, params)
        elif self.mode == "live":
            data = self._fetch_live(path, params)
        else:
            return None

        if data is not None:
            try:
                cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass
        return data

    def _cache_key(self, kind: str, address: str, params: dict) -> str:
        # Normalize the address + params into a stable hash. Mode is encoded in
        # the cache_dir path (per-mode namespace), so it is intentionally NOT in
        # this hash — the same address+params yields the same hash, but lives in
        # a different folder per mode.
        norm = address.lower().strip()
        param_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "address")
        seed = f"{kind}|{norm}|{param_str}"
        return f"{kind}_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"

    def _fetch_live(self, path: str, params: dict) -> Optional[dict]:
        url = f"{self.BASE_URL}{path}"
        headers = {
            "Accept": "application/json",
            "X-Api-Key": self.api_key,
        }
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code == 429:
                # Rate-limited — wait and retry once
                time.sleep(2)
                r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                return None
            return r.json()
        except requests.RequestException:
            return None

    def _fetch_fixture(self, kind: str, address: str, params: dict) -> Optional[dict]:
        """Load a canned response from rentcast_fixtures/.

        Only matches when a slug-specific fixture exists. No generic fallback —
        otherwise the model can't tell when RentCast has no data for an address,
        which would suppress the "missing data" blocker.
        """
        slug = self._address_slug(address)
        specific = FIXTURES_DIR / f"{kind}_{slug}.json"
        if specific.exists():
            return json.loads(specific.read_text(encoding="utf-8"))
        return None

    @staticmethod
    def _address_slug(address: str) -> str:
        s = address.lower().strip()
        out = []
        for c in s:
            if c.isalnum():
                out.append(c)
            elif c.isspace():
                out.append("_")
        return "".join(out)[:60]

    # ──────────────────────────────────────────────
    # Internal: parse RentCast response payloads
    # ──────────────────────────────────────────────

    def _parse_property_record(self, data: Any) -> Optional[PropertyRecord]:
        # RentCast /properties returns an array; take first
        if isinstance(data, list):
            if not data:
                return None
            data = data[0]
        if not isinstance(data, dict):
            return None
        rec = PropertyRecord(raw=data)
        rec.address_normalized = data.get("formattedAddress") or data.get("addressLine1")
        rec.beds = data.get("bedrooms")
        rec.baths = data.get("bathrooms")
        rec.sqft = data.get("squareFootage")
        rec.year_built = data.get("yearBuilt")
        rec.property_type = data.get("propertyType")
        rec.last_sale_price = data.get("lastSalePrice")
        rec.last_sale_date = data.get("lastSaleDate")

        # Tax history: take most recent year's annual tax
        tax_history = data.get("taxAssessments") or data.get("propertyTaxes") or {}
        if isinstance(tax_history, dict) and tax_history:
            most_recent = max(tax_history.keys(), default=None)
            if most_recent:
                entry = tax_history[most_recent]
                if isinstance(entry, dict):
                    rec.tax_assessment_annual = entry.get("total") or entry.get("value")
                elif isinstance(entry, (int, float)):
                    rec.tax_assessment_annual = entry
        return rec

    def _parse_rent_estimate(self, data: Any) -> Optional[RentEstimate]:
        if not isinstance(data, dict):
            return None
        est = RentEstimate(raw=data)
        est.rent = data.get("rent")
        est.rent_range_low = data.get("rentRangeLow")
        est.rent_range_high = data.get("rentRangeHigh")
        est.confidence = data.get("confidence")
        comparables = data.get("comparables") or []
        if isinstance(comparables, list):
            est.comparables = comparables[:10]
        return est


# ──────────────────────────────────────────────
# CONVENIENCE
# ──────────────────────────────────────────────

_default_client: Optional[RentCastClient] = None


def get_client() -> RentCastClient:
    """Return the process-wide default client (lazy-init)."""
    global _default_client
    if _default_client is None:
        _default_client = RentCastClient()
    return _default_client


# ──────────────────────────────────────────────
# FIXTURE BUILDER (for tests/demos)
# ──────────────────────────────────────────────

def write_default_fixtures():
    """Write a couple of default fixtures so the fixture mode works out of the box."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    property_default = {
        "formattedAddress": "1417 S Canal St, Pittsburgh, PA 15215",
        "addressLine1": "1417 S Canal St",
        "bedrooms": 3,
        "bathrooms": 1.5,
        "squareFootage": 1280,
        "yearBuilt": 1925,
        "propertyType": "Single Family",
        "lastSalePrice": 85000,
        "lastSaleDate": "2018-06-15",
        "taxAssessments": {
            "2024": {"total": 2954, "land": 600, "building": 2354},
            "2023": {"total": 2820, "land": 580, "building": 2240},
        },
    }
    (FIXTURES_DIR / "property_default.json").write_text(
        json.dumps(property_default, indent=2), encoding="utf-8"
    )

    rent_default = {
        "rent": 1525,
        "rentRangeLow": 1380,
        "rentRangeHigh": 1650,
        "confidence": "medium",
        "comparables": [
            {"address": "1400 Block Canal", "rent": 1500, "beds": 3, "sqft": 1200, "distance_mi": 0.1},
            {"address": "Aspinwall Area", "rent": 1450, "beds": 3, "sqft": 1100, "distance_mi": 0.5},
            {"address": "Sharpsburg", "rent": 1625, "beds": 3, "sqft": 1350, "distance_mi": 0.7},
        ],
    }
    (FIXTURES_DIR / "rent_default.json").write_text(
        json.dumps(rent_default, indent=2), encoding="utf-8"
    )

    # Specific fixture matching the 1417 S Canal St slug
    canal_slug = RentCastClient._address_slug("1417 S Canal St, Pittsburgh, PA 15215")
    (FIXTURES_DIR / f"property_{canal_slug}.json").write_text(
        json.dumps(property_default, indent=2), encoding="utf-8"
    )
    (FIXTURES_DIR / f"rent_{canal_slug}.json").write_text(
        json.dumps(rent_default, indent=2), encoding="utf-8"
    )


# ──────────────────────────────────────────────
# SMOKE TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Write fixtures so fixture mode works
    write_default_fixtures()
    print(f"Fixtures written to {FIXTURES_DIR}")

    # Test each mode
    for mode in ("fixture", "off"):
        print(f"\n=== Mode: {mode} ===")
        c = RentCastClient(mode=mode)
        rec = c.property_records("1417 S Canal St, Pittsburgh, PA 15215")
        est = c.rent_estimate("1417 S Canal St, Pittsburgh, PA 15215", beds=3)
        if rec:
            print(f"  Property: {rec.beds}BR/{rec.baths}BA, {rec.sqft}sqft, built {rec.year_built}, tax ${rec.tax_assessment_annual}")
        else:
            print(f"  Property: None (mode={mode})")
        if est:
            print(f"  Rent: ${est.rent}/mo (range ${est.rent_range_low}-${est.rent_range_high})")
            print(f"  Comparables: {len(est.comparables)}")
        else:
            print(f"  Rent: None (mode={mode})")

    # Live mode would require RENTCAST_API_KEY
    if os.environ.get("RENTCAST_API_KEY"):
        print(f"\n=== Mode: live ===")
        c = RentCastClient(mode="live")
        rec = c.property_records("1417 S Canal St, Pittsburgh, PA 15215")
        print(f"  Property: {rec}")
    else:
        print("\n(Skip live mode — RENTCAST_API_KEY not set)")
