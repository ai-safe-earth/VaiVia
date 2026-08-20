"""Which snapped features can begin a walk. Pure, so it is tested.

Snapping is one statement over a whole table and belongs in PostGIS. Deciding
what a snapped feature MEANS does not: it is per-feature branching over a
handful of types, it is a product rule people will argue about, and it is
exactly what a unit test is for. Same split, and the same shape, as
load/legality.py — a verdict plus the reason it went that way, so a rejection is
auditable instead of invisible.

NOTHING IS DROPPED. Every snapped feature gets a row; `is_start` and
`start_note` record the verdict beside it. docs/route-pipeline.md settled this
for the off-road score ("descriptive, not a filter -- dropping candidates inside
the build step would hide it") and the same argument holds here: what counts as
a place a walk begins is a product decision, and a build step that silently
discards 999 residential areas has made that decision where nobody can see it.

The anchors docs/route-pipeline.md ratified are parking and stations. Two more
are classified because the data holds them and the start rule needs them:
settlements (you can begin a walk from a village) and GTFS stops with evidence
of service, which is what "reachable without a car" means.
"""

from __future__ import annotations

from typing import NamedTuple


class Verdict(NamedTuple):
    """Whether this feature can begin a walk, and why not when it cannot."""

    is_start: bool
    note: str | None


# A hut, a peak or a lake is somewhere a walk GOES. Classifying them as starts
# would fill the catalogue with routes beginning at a refuge two hours above the
# nearest road.
DESTINATION_NOT_START = {
    "hut": "a hut is a destination, reached on foot",
    "peak": "a summit is a destination, not a trailhead",
    "saddle": "a pass is a place a route crosses",
    "viewpoint": "a viewpoint is a destination",
    "lake": "a lake is a destination",
    "waterfall": "a waterfall is a destination",
    "cave": "a cave is a destination",
    "spring": "a spring is passed, not started from",
    "chapel": "a chapel is passed, not started from",
    "ruins": "ruins are a destination",
    "castle": "a castle is a destination",
    "beach": "a beach is a destination",
    "picnic_site": "a picnic site is a destination",
}

STARTING_POI = {
    "parking": "car park",
    "station": "railway station",
    "campsite": "campsite",
}

# place=* nodes name a settlement at a point. landuse=residential is an AREA
# covering a whole neighbourhood, so "its nearest vertex" is whichever street
# corner the polygon happens to reach first -- a real coordinate standing for
# nothing in particular.
STARTING_SETTLEMENT = frozenset({"city", "town", "village", "hamlet"})


def poi_verdict(poi_type: str) -> Verdict:
    """Can a walk begin at this POI?"""
    if poi_type in STARTING_POI:
        return Verdict(True, None)
    if poi_type in DESTINATION_NOT_START:
        return Verdict(False, DESTINATION_NOT_START[poi_type])
    return Verdict(False, f"{poi_type} is not classified as a starting point")


def settlement_verdict(kind: str) -> Verdict:
    """Can a walk begin at this settlement?"""
    if kind in STARTING_SETTLEMENT:
        return Verdict(True, None)
    if kind == "residential":
        return Verdict(
            False, "a residential area is a polygon, not a point a walk begins at"
        )
    if kind == "isolated_dwelling":
        return Verdict(False, "a single dwelling is not a public starting point")
    return Verdict(False, f"{kind} is not classified as a starting point")


def stop_verdict(n_trips: int | None) -> Verdict:
    """Can a walk begin at this transit stop?

    A stop with no trips in the feed is a sign, not a way home — the rule
    staging.gtfs_stop was built around, applied where it decides something.
    """
    if n_trips is None:
        return Verdict(False, "no service data for this stop")
    if n_trips <= 0:
        return Verdict(
            False, "a stop with no trips in the feed is a sign, not a service"
        )
    return Verdict(True, None)
