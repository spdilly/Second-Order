"""
Address normalization for the property analysis skill.

Parses raw address strings into structured components and produces a
canonical normalized form usable as a lookup key in Property_Overrides.

Uses the usaddress library (probabilistic parser, ~98% accuracy on US
residential addresses).

Usage:
    from scripts.property_analysis.normalize import parse_address

    addr = parse_address("1417 S Canal St, Pittsburgh, PA 15215")
    addr.zip          -> "15215"
    addr.state        -> "PA"
    addr.city         -> "Pittsburgh"
    addr.normalized   -> "1417 s canal st, pittsburgh, pa 15215"
    addr.street_only  -> "1417 s canal st"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import usaddress


# Common abbreviation expansions for normalization
_STREET_SUFFIX_NORMALIZE = {
    "street": "st", "str": "st", "st.": "st",
    "avenue": "ave", "av": "ave", "ave.": "ave",
    "boulevard": "blvd", "blvd.": "blvd",
    "drive": "dr", "dr.": "dr",
    "road": "rd", "rd.": "rd",
    "lane": "ln", "ln.": "ln",
    "court": "ct", "ct.": "ct",
    "place": "pl", "pl.": "pl",
    "terrace": "ter", "ter.": "ter",
    "circle": "cir", "cir.": "cir",
    "parkway": "pkwy", "pkwy.": "pkwy",
    "highway": "hwy", "hwy.": "hwy",
    "way": "way",
}

_DIRECTIONAL_NORMALIZE = {
    "north": "n", "n.": "n",
    "south": "s", "s.": "s",
    "east": "e", "e.": "e",
    "west": "w", "w.": "w",
    "northeast": "ne", "ne.": "ne",
    "northwest": "nw", "nw.": "nw",
    "southeast": "se", "se.": "se",
    "southwest": "sw", "sw.": "sw",
}


@dataclass
class ParsedAddress:
    raw: str
    street_number: Optional[str] = None
    pre_directional: Optional[str] = None
    street_name: Optional[str] = None
    street_suffix: Optional[str] = None
    post_directional: Optional[str] = None
    unit: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    components: dict = field(default_factory=dict)

    @property
    def normalized(self) -> str:
        """Canonical lookup key. Lowercase, expanded abbrevs collapsed back."""
        parts = []
        if self.street_only:
            parts.append(self.street_only)
        if self.city:
            parts.append(self.city.lower().strip())
        if self.state:
            parts.append(self.state.lower().strip())
        if self.zip:
            parts.append(self.zip.strip())
        return ", ".join(parts)

    @property
    def street_only(self) -> str:
        """Street portion only, normalized. '1417 s canal st'."""
        parts = []
        if self.street_number:
            parts.append(self.street_number.strip())
        if self.pre_directional:
            d = self.pre_directional.lower().rstrip(".")
            parts.append(_DIRECTIONAL_NORMALIZE.get(d, d))
        if self.street_name:
            parts.append(self.street_name.lower().strip())
        if self.street_suffix:
            s = self.street_suffix.lower().rstrip(".")
            parts.append(_STREET_SUFFIX_NORMALIZE.get(s, s))
        if self.post_directional:
            d = self.post_directional.lower().rstrip(".")
            parts.append(_DIRECTIONAL_NORMALIZE.get(d, d))
        return " ".join(parts)


def parse_address(raw: str) -> ParsedAddress:
    """
    Parse a raw address string into a structured ParsedAddress.

    Falls back to regex extraction for ZIP and state if usaddress fails.
    """
    addr = ParsedAddress(raw=raw)

    try:
        tagged, _ = usaddress.tag(raw)
    except usaddress.RepeatedLabelError:
        tagged = {}

    addr.components = dict(tagged)
    addr.street_number = tagged.get("AddressNumber")
    addr.pre_directional = tagged.get("StreetNamePreDirectional")
    addr.street_name = tagged.get("StreetName")
    addr.street_suffix = tagged.get("StreetNamePostType")
    addr.post_directional = tagged.get("StreetNamePostDirectional")
    addr.unit = (
        tagged.get("OccupancyIdentifier")
        or tagged.get("SubaddressIdentifier")
    )
    addr.city = tagged.get("PlaceName")
    addr.state = tagged.get("StateName")
    addr.zip = tagged.get("ZipCode")

    # Regex fallbacks
    if not addr.zip:
        m = re.search(r"\b(\d{5})(?:-\d{4})?\b", raw)
        if m:
            addr.zip = m.group(1)
    if not addr.state:
        m = re.search(r"\b([A-Z]{2})\b\s*(?:\d{5}|$)", raw.upper())
        if m:
            addr.state = m.group(1)

    if addr.zip:
        addr.zip = str(addr.zip).strip().zfill(5)
    if addr.state:
        addr.state = addr.state.strip().upper().rstrip(".")

    return addr


if __name__ == "__main__":
    samples = [
        "1417 S Canal St, Pittsburgh, PA 15215",
        "1417 South Canal Street Pittsburgh PA 15215",
        "456 Oak Avenue Apt 2B, Pittsburgh PA 15206",
        "789 Main St Pittsburgh PA",
    ]
    for s in samples:
        a = parse_address(s)
        print(f"\nRaw:        {a.raw}")
        print(f"Normalized: {a.normalized}")
        print(f"Street:     {a.street_only}")
        print(f"ZIP:        {a.zip}, State: {a.state}, City: {a.city}")
