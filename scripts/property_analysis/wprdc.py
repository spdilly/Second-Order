"""
WPRDC + Allegheny ArcGIS adapter — factual baseline for Pittsburgh-area properties.

Provides the deterministic plumbing layer:
  - Address -> ArcGIS parcel ID lookup (Allegheny County only)
  - Parcel ID -> WPRDC property-api full record (assessment + characteristics + sales)
  - Allegheny scope check; non-PA / non-Allegheny addresses return None

Sources:
  - Allegheny ArcGIS AddressPoints layer:
      https://gisdata.alleghenycounty.us/arcgis/rest/services/Addressing/Addressing_AddressPoints/MapServer/0
  - WPRDC property API:
      https://tools.wprdc.org/property-api/v0/parcels/{parcel_id}

Both are public, no auth required. We cache responses on disk and save the raw
JSON into per-deal sources.json for full audit reproducibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

CACHE_DIR = Path(__file__).parent / "wprdc_cache"
ARCGIS_BASE = (
    "https://gisdata.alleghenycounty.us/arcgis/rest/services/"
    "Addressing/Addressing_AddressPoints/MapServer/0/query"
)
WPRDC_BASE = "https://tools.wprdc.org/property-api/v0/parcels"

UA = "Mozilla/5.0 (compatible; PropertyAnalysisSkill/1.0; +https://seandillard.com)"


# ──────────────────────────────────────────────
# RESPONSE TYPES
# ──────────────────────────────────────────────

@dataclass
class ParcelLookup:
    """Result of address -> parcel ID lookup."""
    parcel_id: Optional[str]
    full_address: Optional[str] = None
    zip_code: Optional[str] = None
    municipality: Optional[str] = None
    found: bool = False
    error: Optional[str] = None


@dataclass
class ParcelDetails:
    """Subset of WPRDC parcel data we use for underwriting."""
    parcel_id: str
    # Property characteristics
    beds: Optional[int] = None
    full_baths: Optional[int] = None
    half_baths: Optional[int] = None
    finished_sqft: Optional[int] = None
    lot_sqft: Optional[float] = None
    year_built: Optional[int] = None
    property_use: Optional[str] = None         # e.g., "TWO FAMILY"
    style: Optional[str] = None                 # e.g., "MULTI-FAMILY"
    condition: Optional[str] = None             # e.g., "FAIR"
    # Assessment & jurisdiction
    assessed_total: Optional[float] = None      # COUNTYTOTAL
    assessed_land: Optional[float] = None
    assessed_building: Optional[float] = None
    fair_market_total: Optional[float] = None
    municipality: Optional[str] = None
    muni_code: Optional[str] = None
    school_district: Optional[str] = None
    # Sales history (up to 3 from the assessment record)
    last_sale_price: Optional[float] = None
    last_sale_date: Optional[str] = None
    prev_sale_price: Optional[float] = None
    prev_sale_date: Optional[str] = None
    # Address from WPRDC's record
    address_from_wprdc: Optional[str] = None
    zip_code: Optional[str] = None
    # Metadata
    tax_year: Optional[int] = None
    as_of_date: Optional[str] = None
    # Raw payload for audit (sources.json)
    raw: dict = field(default_factory=dict)

    @property
    def total_baths(self) -> Optional[float]:
        if self.full_baths is None and self.half_baths is None:
            return None
        return (self.full_baths or 0) + 0.5 * (self.half_baths or 0)


# ──────────────────────────────────────────────
# CLIENT
# ──────────────────────────────────────────────

class WPRDCClient:
    """Thin client over Allegheny ArcGIS + WPRDC property-api.

    Allegheny-only. Non-PA / non-Allegheny addresses return ParcelLookup(found=False).

    Modes:
      - "live" (default): make real API calls, cache responses on disk
      - "off": no network; every lookup returns found=False. Use in tests to
               avoid hitting the network and to assert non-Allegheny behavior.

    Mode resolution: explicit arg -> WPRDC_MODE env var -> "live".
    """

    def __init__(self, cache_dir: Optional[Path] = None,
                 cache_ttl_days: int = 30,
                 request_timeout: int = 20,
                 mode: Optional[str] = None):
        self.mode = mode or os.environ.get("WPRDC_MODE", "live")
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = cache_ttl_days * 86400
        self.timeout = request_timeout

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def lookup_parcel_id(self, parsed_address) -> ParcelLookup:
        """Take a normalize.ParsedAddress, return Allegheny parcel ID if found.

        Allegheny scope check: state must be PA. If not, returns found=False
        with a clear error message.
        """
        if self.mode == "off":
            return ParcelLookup(
                parcel_id=None,
                error="WPRDC adapter is OFF (no network calls)",
            )
        if not parsed_address:
            return ParcelLookup(parcel_id=None, error="No address provided")
        if parsed_address.state and parsed_address.state.upper() != "PA":
            return ParcelLookup(
                parcel_id=None,
                error=f"State is {parsed_address.state}; WPRDC supports Allegheny County (PA) only.",
            )

        addr_num = parsed_address.street_number
        st_name = parsed_address.street_name
        if not addr_num or not st_name:
            return ParcelLookup(
                parcel_id=None,
                error="Could not parse street number + street name from address.",
            )

        # ArcGIS expects parts in UPPERCASE, no street type/suffix in ST_NAME.
        st_name_clean = st_name.upper().strip()
        zip_code = parsed_address.zip

        cache_key = self._cache_key("arcgis", f"{addr_num}_{st_name_clean}_{zip_code or ''}")
        cached = self._read_cache(cache_key)
        if cached:
            return self._parse_arcgis(cached, fallback_parcel_id=None)

        # Build where clause
        where_parts = [f"ADDR_NUM='{addr_num}'", f"ST_NAME='{st_name_clean}'"]
        if zip_code:
            where_parts.append(f"ZIP_CODE='{zip_code}'")
        params = {
            "where": " AND ".join(where_parts),
            "outFields": "PARCELID,FULL_ADDRESS,ZIP_CODE,MUNICIPALITY,ST_TYPE",
            "f": "json",
            "returnGeometry": "false",
        }
        try:
            r = requests.get(ARCGIS_BASE, params=params,
                             headers={"User-Agent": UA, "Accept": "application/json"},
                             timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            return ParcelLookup(parcel_id=None, error=f"ArcGIS request failed: {e}")
        except ValueError as e:
            return ParcelLookup(parcel_id=None, error=f"ArcGIS returned non-JSON: {e}")

        self._write_cache(cache_key, data)
        return self._parse_arcgis(data)

    def get_parcel_details(self, parcel_id: str) -> Optional[ParcelDetails]:
        """Fetch full WPRDC parcel record. Returns None if not found / on error."""
        if not parcel_id:
            return None
        cache_key = self._cache_key("wprdc", parcel_id)
        cached = self._read_cache(cache_key)
        if cached:
            return self._parse_wprdc(cached, parcel_id)

        url = f"{WPRDC_BASE}/{parcel_id}"
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"},
                             timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            return None

        self._write_cache(cache_key, data)
        return self._parse_wprdc(data, parcel_id)

    def fetch_for_address(self, parsed_address) -> tuple[ParcelLookup, Optional[ParcelDetails]]:
        """Convenience: do both calls in sequence. Returns (lookup, details_or_None)."""
        lookup = self.lookup_parcel_id(parsed_address)
        if not lookup.found or not lookup.parcel_id:
            return lookup, None
        details = self.get_parcel_details(lookup.parcel_id)
        return lookup, details

    # ──────────────────────────────────────────────
    # Internal: caching
    # ──────────────────────────────────────────────

    def _cache_key(self, kind: str, seed: str) -> str:
        return f"{kind}_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"

    def _read_cache(self, key: str) -> Optional[dict]:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.cache_ttl_seconds:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_cache(self, key: str, data: dict) -> None:
        path = self.cache_dir / f"{key}.json"
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # Internal: parsers
    # ──────────────────────────────────────────────

    @staticmethod
    def _parse_arcgis(data: dict, fallback_parcel_id: Optional[str] = None) -> ParcelLookup:
        if not isinstance(data, dict):
            return ParcelLookup(parcel_id=None, error="Unexpected ArcGIS response shape")
        features = data.get("features") or []
        if not features:
            return ParcelLookup(parcel_id=None, error="No parcel found at that address")
        attrs = features[0].get("attributes") or {}
        pid = attrs.get("PARCELID") or fallback_parcel_id
        if not pid:
            return ParcelLookup(parcel_id=None, error="ArcGIS returned no PARCELID")
        return ParcelLookup(
            parcel_id=str(pid).strip(),
            full_address=attrs.get("FULL_ADDRESS"),
            zip_code=str(attrs.get("ZIP_CODE")) if attrs.get("ZIP_CODE") else None,
            municipality=attrs.get("MUNICIPALITY"),
            found=True,
        )

    @staticmethod
    def _parse_wprdc(data: Any, parcel_id: str) -> Optional[ParcelDetails]:
        if not isinstance(data, dict):
            return None
        results = data.get("results") or []
        if not results:
            return None
        res = results[0]
        inner = (res.get("data") or {}).get("assessments") or []
        if not inner:
            # No assessment record; return shell with raw payload
            return ParcelDetails(parcel_id=parcel_id, raw=data)
        a = inner[0]

        def _int(v):
            try: return int(v) if v is not None else None
            except (ValueError, TypeError): return None

        def _float(v):
            try: return float(v) if v is not None else None
            except (ValueError, TypeError): return None

        addr_parts = [a.get("PROPERTYHOUSENUM"), a.get("PROPERTYFRACTION"),
                      a.get("PROPERTYADDRESS")]
        address = " ".join(str(p) for p in addr_parts if p).strip() or None

        return ParcelDetails(
            parcel_id=parcel_id,
            beds=_int(a.get("BEDROOMS")),
            full_baths=_int(a.get("FULLBATHS")),
            half_baths=_int(a.get("HALFBATHS")),
            finished_sqft=_int(a.get("FINISHEDLIVINGAREA")),
            lot_sqft=_float(a.get("LOTAREA")),
            year_built=_int(a.get("YEARBLT")),
            property_use=a.get("USEDESC"),
            style=a.get("STYLEDESC"),
            condition=a.get("CONDITIONDESC"),
            assessed_total=_float(a.get("COUNTYTOTAL")),
            assessed_land=_float(a.get("COUNTYLAND")),
            assessed_building=_float(a.get("COUNTYBUILDING")),
            fair_market_total=_float(a.get("FAIRMARKETTOTAL")),
            municipality=a.get("MUNIDESC"),
            muni_code=a.get("MUNICODE"),
            school_district=a.get("SCHOOLDESC"),
            last_sale_price=_float(a.get("SALEPRICE")),
            last_sale_date=a.get("SALEDATE"),
            prev_sale_price=_float(a.get("PREVSALEPRICE")),
            prev_sale_date=a.get("PREVSALEDATE"),
            address_from_wprdc=address,
            zip_code=str(a.get("PROPERTYZIP")) if a.get("PROPERTYZIP") else None,
            tax_year=_int(a.get("TAXYEAR")),
            as_of_date=a.get("ASOFDATE"),
            raw=data,
        )


# ──────────────────────────────────────────────
# CONVENIENCE
# ──────────────────────────────────────────────

_default_client: Optional[WPRDCClient] = None


def get_client() -> WPRDCClient:
    global _default_client
    if _default_client is None:
        _default_client = WPRDCClient()
    return _default_client


# ──────────────────────────────────────────────
# SMOKE TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    from scripts.property_analysis.normalize import parse_address

    test_addresses = [
        "1417 S Canal St, Pittsburgh, PA 15215",
        "57 Lower Rd, Pittsburgh, PA 15215",
        # Non-PA — should be rejected with clear message
        "123 Main St, Austin, TX 78701",
    ]
    client = WPRDCClient()
    for raw in test_addresses:
        print(f"\n{'='*60}\n  {raw}\n{'='*60}")
        parsed = parse_address(raw)
        lookup, details = client.fetch_for_address(parsed)
        print(f"  Lookup found: {lookup.found}")
        if lookup.error:
            print(f"  Error: {lookup.error}")
        if lookup.parcel_id:
            print(f"  PARCELID: {lookup.parcel_id}")
            print(f"  Municipality: {lookup.municipality}")
        if details:
            print(f"  Use: {details.property_use}, Style: {details.style}")
            print(f"  Beds/Baths/SqFt: {details.beds}/{details.total_baths}/{details.finished_sqft}")
            print(f"  Year built: {details.year_built}, Condition: {details.condition}")
            print(f"  Assessed total: ${details.assessed_total:,.0f}" if details.assessed_total else "  Assessed: n/a")
            print(f"  Last sale: ${details.last_sale_price:,.0f} on {details.last_sale_date}" if details.last_sale_price else "  No sale history")
            print(f"  Muni / School: {details.muni_code} / {details.school_district}")
