"""Legality: whether a way may carry a route at all. Hard rules, not scores.

The old ingestion routed over access=private tracks and sent bikes down
foot=designated paths, because access tags were parsed and discarded. Here they
decide routability outright, per activity, and the decision is a pure function
of the tags so it can be pinned by tests and audited in one place.

OSM access semantics, reduced to what a route product needs:

  * `access` is the general key; a specific key (`foot`, `bicycle`) overrides it
    for that mode. So access=private + foot=yes is walkable (a signed-through
    path on private land is common in these valleys), while access=private
    alone is not ours to route over.
  * `permissive` and `designated` and `yes` all permit; `destination` permits
    (a walker is always "destination" traffic); `private`, `no`, `military`,
    and `customers` do not.
  * A missing tag permits: most legal paths carry no access tag at all, and
    treating absence as forbidden would erase the network (same reasoning as
    the surface penalty in backend/core/comfort.py — never turn a mapping gap
    into a rule against the thing the product exists to find).

Highway types are the walkable set the old ingestion proved out
(fragility #9: trails connect through roads, so residential/tertiary stay in),
minus anything a car product would add. Bikes exclude steps outright: a foot
loop over 200 steps is not a bike route, it is impassable — the same reasoning
that made hike and mtb separate GraphHopper profiles.
"""

from __future__ import annotations

WALKABLE_HIGHWAYS = frozenset(
    {
        "path",
        "track",
        "cycleway",
        "footway",
        "bridleway",
        "steps",
        "pedestrian",
        "living_street",
        "residential",
        "unclassified",
        "service",
        "tertiary",
        "secondary",
    }
)

# steps: impassable, not merely unpleasant. footway/pedestrian without a
# bicycle tag stay in — Italian practice tolerates a pushed bike, and the
# comfort model already prices them as unattractive rather than illegal.
BIKEABLE_HIGHWAYS = WALKABLE_HIGHWAYS - {"steps"}

FORBIDDING = frozenset({"private", "no", "military", "customers"})
PERMITTING = frozenset({"yes", "designated", "permissive", "destination"})


def _mode_allowed(tags: dict[str, str], mode_key: str) -> tuple[bool, str | None]:
    """(allowed, reason-if-not) for one access mode over general + specific keys."""
    specific = tags.get(mode_key)
    if specific in FORBIDDING:
        return False, f"{mode_key}={specific}"
    if specific in PERMITTING:
        return True, None  # a specific permission overrides a general refusal
    general = tags.get("access")
    if general in FORBIDDING:
        return False, f"access={general}"
    return True, None


def routable_foot(tags: dict[str, str]) -> tuple[bool, str | None]:
    """(routable, reason-if-not) for a walker."""
    highway = tags.get("highway")
    if highway not in WALKABLE_HIGHWAYS:
        return False, f"highway={highway}"
    return _mode_allowed(tags, "foot")


def routable_bike(tags: dict[str, str]) -> tuple[bool, str | None]:
    """(routable, reason-if-not) for a bike."""
    highway = tags.get("highway")
    if highway not in BIKEABLE_HIGHWAYS:
        return False, f"highway={highway}"
    return _mode_allowed(tags, "bicycle")
