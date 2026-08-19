"""POI classification: OSM tags -> one pipeline poi_type. First match wins.

Ported from backend/ingestion/osm_extract.py POI_TAG_MAP with the survey's
coverage fixes applied:

  * hut, viewpoint, chapel, castle, ruins, waterfall and spring now match on
    AREAS too, not only nodes — 8 huts in the whole Grigna was a query bug
    (rifugi mapped as buildings were invisible), not terrain.
  * `ele` is read wherever present: 281 peaks without their height made poor
    destination cards.

Two roles in one table, deliberately: ANCHORS an outing can start from
(parking, station) and DESTINATIONS worth reaching. What reaches chat is
decided downstream, not here.
"""

from __future__ import annotations

POI_TAG_MAP: list[tuple[str, str, str]] = [
    ("natural", "water", "lake"),
    ("tourism", "alpine_hut", "hut"),
    ("tourism", "wilderness_hut", "hut"),
    ("tourism", "camp_site", "campsite"),
    ("tourism", "viewpoint", "viewpoint"),
    ("railway", "station", "station"),
    ("amenity", "swimming_area", "bathing_water"),
    ("leisure", "swimming_area", "bathing_water"),
    # Anchors
    ("amenity", "parking", "parking"),
    # Destinations
    ("natural", "peak", "peak"),
    ("natural", "saddle", "saddle"),
    ("natural", "beach", "beach"),
    ("natural", "spring", "spring"),
    ("natural", "cave_entrance", "cave"),
    ("waterway", "waterfall", "waterfall"),
    # An ermita/eremo is tagged three common ways; one type, not a guess
    # between three.
    ("building", "chapel", "chapel"),
    ("historic", "wayside_shrine", "chapel"),
    ("historic", "wayside_cross", "chapel"),
    ("historic", "castle", "castle"),
    ("historic", "ruins", "ruins"),
    ("tourism", "picnic_site", "picnic_site"),
]


def poi_type_for(tags: dict[str, str]) -> str | None:
    for key, value, poi_type in POI_TAG_MAP:
        if tags.get(key) == value:
            return poi_type
    return None


def parse_ele_m(tags: dict[str, str]) -> float | None:
    """The ele tag as metres, or None. OSM values are metres by convention but
    arrive with junk often enough ('1234 m', '1,234') that parsing defensively
    beats trusting float()."""
    raw = tags.get("ele")
    if not raw:
        return None
    cleaned = raw.replace(",", ".").replace("m", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    # A Lombard POI below Como's lake level or above Mont Blanc is a typo.
    return value if 150.0 <= value <= 4900.0 else None


SETTLEMENT_PLACES = frozenset(
    {"city", "town", "village", "hamlet", "isolated_dwelling"}
)


def settlement_kind(tags: dict[str, str]) -> str | None:
    place = tags.get("place")
    if place in SETTLEMENT_PLACES:
        return place
    if tags.get("landuse") == "residential":
        return "residential"
    return None
