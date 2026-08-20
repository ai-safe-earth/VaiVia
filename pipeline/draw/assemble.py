"""Assembly: a walked edge sequence becomes a route's facts. Pure, tested.

This is where the metadata-rules.md "on join" table finally executes, and where
the provider spike's caveat is repaired: everything here reads the ROUTED EDGE
SEQUENCE — which edge, in which order, in which direction — so a 6 m slip of
footway grabbed by a corridor cannot exist. If an edge is in the sequence, the
route walks it.

The direction rule is the load-bearing one. Edges store their attributes along
their own geometry (source → target). A route that walks an edge backwards
must SWAP ascent and descent and REVERSE the profile — the same inversion
metadata-rules.md specifies for `oneway` and `incline`. Getting this wrong
does not crash; it reports a mountain loop as flat, which is why every rule
here is pinned by a test.
"""

from __future__ import annotations

from typing import NamedTuple

from export.document import (
    SAC_ORDER,
    Span,
    dominant,
    exigent_grade,
    shares,
    significant_grade,
)

Coord = tuple[float, float]

# What counts as off-road. The ways a walker calls "trail" — everything else in
# WALKABLE_HIGHWAYS (residential, service, tertiary...) is road that trails
# legitimately connect through (fragility #9) but should not dominate a walk.
OFF_ROAD_HIGHWAYS = frozenset({"path", "track", "bridleway", "footway", "steps"})

MTB_ORDER = ["0", "1", "2", "3", "4", "5", "6"]


class WalkedEdge(NamedTuple):
    """One step of the route: an edge, walked forward or backwards."""

    edge_id: int
    forward: bool
    length_m: float
    coords: list[Coord]  # as stored, source -> target
    profile_m: list[float] | None  # as stored, aligned to coords
    ascent_m: float | None  # as stored (source -> target)
    descent_m: float | None
    surface: str | None
    sac_scale: str | None
    mtb_scale: str | None
    highway: str | None
    routable_bike: bool


class Assembled(NamedTuple):
    coords: list[Coord]
    distance_m: float
    ascent_m: float | None
    descent_m: float | None
    profile: dict[str, list[float]] | None
    surface: dict[str, float]
    surface_dominant: str | None
    sac_scale: str | None  # character: hardest grade covering >= 5%
    sac_max: str | None  # exigent: hardest metre walked, any length
    graded_share: float  # how much of the route carries ANY grade
    mtb_rideable: bool | None
    mtb_scale: str | None
    bike_blocked_m: float  # metres a bike may not legally ride
    off_road_share: float
    retrace_share: float


def walked_coords(edge: WalkedEdge) -> list[Coord]:
    return edge.coords if edge.forward else list(reversed(edge.coords))


def concatenate(edges: list[WalkedEdge]) -> list[Coord]:
    """The route's line, in walking order, shared vertices not repeated."""
    out: list[Coord] = []
    for edge in edges:
        coords = walked_coords(edge)
        if out and out[-1] == coords[0]:
            coords = coords[1:]
        out.extend(coords)
    return out


def climb(edges: list[WalkedEdge]) -> tuple[float | None, float | None]:
    """Ascent and descent along the WALKED direction.

    An edge walked backwards contributes its stored descent as ascent and vice
    versa. One edge with unknown climb makes the whole route's climb unknown —
    absent is not zero, exactly as in the route documents.
    """
    up = 0.0
    down = 0.0
    for edge in edges:
        if edge.ascent_m is None or edge.descent_m is None:
            return None, None
        if edge.forward:
            up += edge.ascent_m
            down += edge.descent_m
        else:
            up += edge.descent_m
            down += edge.ascent_m
    return up, down


def profile(edges: list[WalkedEdge]) -> dict[str, list[float]] | None:
    """The altitude profile along the walk, cumulative distance + elevation.

    Reversed edges contribute their profile reversed. Any missing sample makes
    the whole profile absent rather than silently shorter.
    """
    distances: list[float] = []
    elevations: list[float] = []
    travelled = 0.0
    for edge in edges:
        if not edge.profile_m or any(z is None for z in edge.profile_m):
            return None
        series = edge.profile_m if edge.forward else list(reversed(edge.profile_m))
        step = edge.length_m / max(len(series) - 1, 1)
        start_index = 1 if distances else 0  # shared vertex: sample once
        for i, z in enumerate(series):
            if i < start_index:
                continue
            distances.append(round(travelled + i * step, 1))
            elevations.append(round(z, 1))
        travelled += edge.length_m
    if len(distances) < 2:
        return None
    return {"distance_m": distances, "elevation_m": elevations}


def mtb(edges: list[WalkedEdge]) -> tuple[bool | None, str | None, float]:
    """The access conjunction, along the sequence: one forbidding edge forbids.

    This is the spike's caveat repaired — a route that WALKS a non-bikeable
    edge is genuinely not a bike route; there is no corridor to slip in a
    crossing. Nothing walked means nothing known.

    The BLOCKED METRES come back with the verdict, because a verdict without
    them is invisible data: a loop failing on 6 m of steps and a loop failing
    on 1.6 km of private road read identically as "no", and the reader deciding
    what to do about it needs to know which one it is.
    """
    if not edges:
        return None, None, 0.0
    blocked = sum(e.length_m for e in edges if not e.routable_bike)
    if blocked > 0:
        return False, None, blocked
    grade = significant_grade([Span(e.mtb_scale, e.length_m) for e in edges], MTB_ORDER)
    return True, grade, 0.0


def retrace(edges: list[WalkedEdge]) -> float:
    """Metres walked more than once, as a share of the total.

    Counts REPEAT visits: a 10 km loop that walks one 1 km edge twice retraces
    1 of 11 km. An out-and-back retraces half its metres, whichever direction
    each pass took — direction does not make the same ground new.
    """
    total = sum(e.length_m for e in edges)
    if total <= 0:
        return 0.0
    seen: set[int] = set()
    repeated = 0.0
    for edge in edges:
        if edge.edge_id in seen:
            repeated += edge.length_m
        seen.add(edge.edge_id)
    return repeated / total


def off_road(edges: list[WalkedEdge]) -> float:
    total = sum(e.length_m for e in edges)
    if total <= 0:
        return 0.0
    return sum(e.length_m for e in edges if e.highway in OFF_ROAD_HIGHWAYS) / total


def assemble(edges: list[WalkedEdge]) -> Assembled:
    """Every rule at once: the sequence in, the route's facts out."""
    up, down = climb(edges)
    surface = shares(Span(e.surface, e.length_m) for e in edges)
    rideable, grade, blocked_m = mtb(edges)
    total = sum(e.length_m for e in edges)
    sac_spans = [Span(e.sac_scale, e.length_m) for e in edges]
    graded = sum(e.length_m for e in edges if e.sac_scale in SAC_ORDER)
    return Assembled(
        coords=concatenate(edges),
        distance_m=total,
        ascent_m=up,
        descent_m=down,
        profile=profile(edges),
        surface=surface,
        surface_dominant=dominant(surface),
        sac_scale=significant_grade(sac_spans, SAC_ORDER),
        sac_max=exigent_grade(sac_spans, SAC_ORDER),
        graded_share=(graded / total) if total > 0 else 0.0,
        mtb_rideable=rideable,
        mtb_scale=grade,
        bike_blocked_m=blocked_m,
        off_road_share=off_road(edges),
        retrace_share=retrace(edges),
    )


def score(
    assembled: Assembled, target_m: float, *, weights: dict[str, float] | None = None
) -> float:
    """Descriptive, never a filter (route-pipeline.md: dropping candidates
    inside the build step hides a product decision).

    v0: off-road share half, loop shape a third, length fit the rest. The
    weights are arguments so a recalibration is a parameter, not a code change.
    """
    w = {"off_road": 0.5, "shape": 0.3, "fit": 0.2} | (weights or {})
    fit = max(0.0, 1.0 - abs(assembled.distance_m - target_m) / target_m)
    return round(
        w["off_road"] * assembled.off_road_share
        + w["shape"] * (1.0 - assembled.retrace_share)
        + w["fit"] * fit,
        4,
    )
