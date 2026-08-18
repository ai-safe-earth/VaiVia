"""Routing comfort cost: what a walker or rider would actually choose.

Routing on raw distance picks roads, because roads are straight. After the
connective ways were ingested (docs/fragilities.md #9) a "10 km trail loop"
came back roughly 83% asphalt — plausible-looking and wrong, which is a worse
failure than returning nothing.

So CONNECTS_TO carries `cost_m` alongside `distance_m`:

    cost_m = distance_m * highway_penalty * surface_penalty

Dijkstra minimises `cost_m`; every distance shown to a user still comes from
`distance_m`. Conflating the two would quote inflated lengths.

Penalties are ratios of tolerance, not of speed: `secondary: 4.5` means a rider
would accept about 4.5 km of trail rather than 1 km of that road. They are
deliberately finite — a path that makes roads infinitely expensive fails to
route at all in valleys where the only link is a lane. Calibrate against real
routes rather than intuition; the loop spike prints the surface mix for exactly
this.
"""

# Keyed on the OSM highway tag. Everything ingested by WALKABLE_HIGHWAYS
# appears here; anything else falls back to DEFAULT_HIGHWAY_PENALTY.
HIGHWAY_PENALTY: dict[str, float] = {
    "path": 1.0,  # the thing the app exists to find
    "track": 1.1,
    "bridleway": 1.15,
    "cycleway": 1.3,
    "footway": 1.4,  # usually an urban pavement
    "pedestrian": 1.5,
    "steps": 1.8,  # slow on foot, and a dismount on a bike
    "living_street": 2.0,
    "service": 2.2,
    "residential": 2.4,
    "unclassified": 2.4,
    "tertiary": 3.2,
    "secondary": 4.5,  # walkable, unpleasant, sometimes the only link
}
DEFAULT_HIGHWAY_PENALTY = 2.0

# Surface adjusts within a highway type. Kept mild: it compounds with the
# highway penalty, and most roads are asphalt anyway.
SURFACE_PENALTY: dict[str, float] = {
    "asphalt": 1.3,
    "paved": 1.3,
    "concrete": 1.3,
    "metal": 1.3,
    "sett": 1.15,
    "cobblestone": 1.15,
    "unhewn_cobblestone": 1.15,
    "paving_stones": 1.15,
}
DEFAULT_SURFACE_PENALTY = 1.0


def surface_penalty(surface: str | None) -> float:
    """Untagged surface is NOT penalised.

    Roughly 38% of paths here carry no surface tag, and they are
    disproportionately the small trails the app should prefer. Treating
    "unknown" as bad would bias routing against exactly those, turning a
    mapping gap into a routing preference.
    """
    if not surface:
        return DEFAULT_SURFACE_PENALTY
    return SURFACE_PENALTY.get(surface, DEFAULT_SURFACE_PENALTY)


def highway_penalty(highway_type: str | None) -> float:
    if not highway_type:
        return DEFAULT_HIGHWAY_PENALTY
    return HIGHWAY_PENALTY.get(highway_type, DEFAULT_HIGHWAY_PENALTY)


def comfort_cost_m(
    distance_m: float, highway_type: str | None, surface: str | None
) -> float:
    """Routing weight for one edge. Never the distance reported to a user."""
    return distance_m * highway_penalty(highway_type) * surface_penalty(surface)
