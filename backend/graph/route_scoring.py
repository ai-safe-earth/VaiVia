"""Score and deduplicate generated route candidates.

Stage 3 of the route pipeline (docs/route-pipeline.md). Generation is cheap and
indiscriminate — many seeds per trailhead per distance — so what makes the
catalogue good is what happens here: rank the candidates honestly, throw away
the near-duplicates, keep a few.

Pure functions, no I/O. That is deliberate: these encode taste, and taste should
be arguable in a test rather than buried in a script.

Nothing here filters. A candidate that scores badly is still returned, ranked
low, because "good enough to offer" is a product decision and silently dropping
routes inside the scorer would hide how thin coverage really is.
"""

import math
from dataclasses import dataclass, field
from typing import Any

from core.geo import LatLon, haversine_m

# Weights sum to 1. They encode what makes a loop worth walking, in the order a
# person would notice something wrong:
#   LENGTH   ask for 15 km and get 25 km and nothing else matters.
#   OFF_ROAD the app exists to find trails; a road loop is a different product.
#   VARIETY  retracing your steps is the difference between a loop and an
#            out-and-back, and it is what our own generator was worst at.
#   CLIMB    a completely flat "mountain" loop is suspicious, but this is a
#            mild preference and must not dominate — some good routes are flat.
WEIGHT_LENGTH = 0.40
WEIGHT_OFF_ROAD = 0.30
WEIGHT_VARIETY = 0.20
WEIGHT_CLIMB = 0.10

# Beyond this fraction of the target, length error is scored as total failure
# rather than continuing to shade down: 2x the requested distance is not
# "somewhat wrong", it is a different outing.
LENGTH_ERROR_CEILING = 0.5

# Climb that counts as a full score, in metres per kilometre. 40 m/km is a
# gently rolling route; anything steeper is not additionally rewarded, so the
# scorer never prefers a brutal route just because it climbs.
CLIMB_SATURATION_M_PER_KM = 40.0

# Grid cell for the duplicate signature. ~110 m at this latitude: fine enough
# that two genuinely different valleys never collide, coarse enough that the
# same path sampled differently lands in the same cells.
SIGNATURE_CELL_DEG = 0.001
DUPLICATE_JACCARD = 0.6


@dataclass
class RouteCandidate:
    """One generated route, before it earns a place in the catalogue."""

    trailhead_id: str
    target_m: float
    coordinates: list[LatLon]
    distance_m: float
    off_road_share: float
    retrace: float
    ascent_m: float | None = None
    source: str = "local"
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return self.scores.get("total", 0.0)


def length_score(distance_m: float, target_m: float) -> float:
    if target_m <= 0:
        return 0.0
    error = abs(distance_m - target_m) / target_m
    return max(0.0, 1.0 - error / LENGTH_ERROR_CEILING)


def climb_score(ascent_m: float | None, distance_m: float) -> float:
    """Unknown climb scores neutral, not zero.

    Our own routing reports no elevation at all (fragility #6). Scoring that as
    flat would rank every locally generated route below every GraphHopper one
    for a reason that is about instrumentation, not about the route.
    """
    if ascent_m is None or distance_m <= 0:
        return 0.5
    per_km = ascent_m / (distance_m / 1000.0)
    return min(1.0, per_km / CLIMB_SATURATION_M_PER_KM)


def score_candidate(candidate: RouteCandidate) -> RouteCandidate:
    """Fill `scores` in place and return the candidate, for use in a map()."""
    parts = {
        "length": length_score(candidate.distance_m, candidate.target_m),
        "off_road": max(0.0, min(1.0, candidate.off_road_share)),
        "variety": max(0.0, 1.0 - max(0.0, min(1.0, candidate.retrace))),
        "climb": climb_score(candidate.ascent_m, candidate.distance_m),
    }
    parts["total"] = (
        WEIGHT_LENGTH * parts["length"]
        + WEIGHT_OFF_ROAD * parts["off_road"]
        + WEIGHT_VARIETY * parts["variety"]
        + WEIGHT_CLIMB * parts["climb"]
    )
    candidate.scores = {k: round(v, 4) for k, v in parts.items()}
    return candidate


def signature(coordinates: list[LatLon]) -> frozenset[tuple[int, int]]:
    """The set of grid cells a route touches.

    Comparing geometry point-by-point is both expensive and wrong — two
    generators sample the same path at different densities. Cell occupancy is
    stable under resampling, which is the property that matters.
    """
    return frozenset(
        (
            int(math.floor(lat / SIGNATURE_CELL_DEG)),
            int(math.floor(lon / SIGNATURE_CELL_DEG)),
        )
        for lat, lon in coordinates
    )


def jaccard(a: frozenset[Any], b: frozenset[Any]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def dedupe(
    candidates: list[RouteCandidate], threshold: float = DUPLICATE_JACCARD
) -> list[RouteCandidate]:
    """Keep the best of each cluster of near-identical routes, best first.

    Seeds around one trailhead overlap heavily — the same valley reached three
    ways is one route to a user. Sorting by score before comparing means the
    survivor of each cluster is its best member, not whichever was generated
    first.
    """
    ranked = sorted(candidates, key=lambda c: -c.score)
    kept: list[RouteCandidate] = []
    signatures: list[frozenset[tuple[int, int]]] = []
    for candidate in ranked:
        sig = signature(candidate.coordinates)
        if any(jaccard(sig, seen) >= threshold for seen in signatures):
            continue
        kept.append(candidate)
        signatures.append(sig)
    return kept


def select(candidates: list[RouteCandidate], keep: int = 3) -> list[RouteCandidate]:
    """Score, deduplicate, and take the best few. The whole of stage 3."""
    scored = [score_candidate(c) for c in candidates]
    return dedupe(scored)[:keep]


def retrace_fraction(coordinates: list[LatLon]) -> float:
    """How much of the line is walked twice, measured on undirected point pairs.

    Direction-insensitive because retracing a leg reverses it: an out-and-back
    would otherwise look like a clean loop.
    """
    if len(coordinates) < 2:
        return 1.0
    pairs = [
        frozenset(
            (
                (round(a[0], 6), round(a[1], 6)),
                (round(b[0], 6), round(b[1], 6)),
            )
        )
        for a, b in zip(coordinates, coordinates[1:], strict=False)
    ]
    return 1.0 - (len(set(pairs)) / len(pairs))


def polyline_length_m(coordinates: list[LatLon]) -> float:
    return sum(
        haversine_m(a, b) for a, b in zip(coordinates, coordinates[1:], strict=False)
    )
