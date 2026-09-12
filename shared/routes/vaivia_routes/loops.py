"""Loop shapes and candidate hygiene. Pure, tested.

A loop of target length T is drawn as start → via₁ → via₂ → start, with the
two vias placed on a ring around the start: an equilateral-ish triangle of
perimeter T has sides T/3, which puts its far corners roughly T/3.6 from the
start as the crow flies (paths wander, so the ring is deliberately tighter
than the arithmetic T/3). Each SEED rotates the ring, so seeds explore
bearings, deterministically — the same (start, target, seed) always asks for
the same loop, which is what makes a rebuild reproducible and the
geometry-derived route id stable.

Dedupe is by shared ground, not by geometry equality: two candidates that walk
mostly the same edges are one route asked twice, and the better-scored one
speaks for both.
"""

from __future__ import annotations

import math
from typing import NamedTuple

Coord = tuple[float, float]

# Crow-flies ring radius as a share of target length. T/3 is the equilateral
# arithmetic; paths wander. 1/3.6 was the first guess and the first catalogue
# measured its error: median actual/target of 1.43 (5 km asks worst at 1.62).
# 1/5.0 is that measurement folded back in — the constant is calibrated, not
# assumed, like every other tolerance in this pipeline.
RING_SHARE = 1 / 5.0

# Walking a loop as two legs through two vias: bearings 120° apart make the
# triangle; the seed rotates the whole figure.
VIA_BEARINGS = (0.0, 120.0)
SEED_STEP_DEG = 360.0 / 7  # 7 seeds cover the circle without repeating


class ViaTarget(NamedTuple):
    lon: float
    lat: float


def ring_points(
    start: Coord, target_m: float, seed: int, bearings: tuple[float, ...] = VIA_BEARINGS
) -> list[ViaTarget]:
    """Where the vias should roughly be, as coordinates on the ring.

    Local equirectangular approximation — at ring radii of a few km the error
    is centimetres, and the via only needs to land near SOME vertex.
    """
    lon0, lat0 = start
    radius_m = target_m * RING_SHARE
    rotation = seed * SEED_STEP_DEG
    metres_per_deg_lat = 111_320.0
    metres_per_deg_lon = metres_per_deg_lat * math.cos(math.radians(lat0))
    out = []
    for bearing in bearings:
        theta = math.radians(bearing + rotation)
        out.append(
            ViaTarget(
                lon=lon0 + radius_m * math.sin(theta) / metres_per_deg_lon,
                lat=lat0 + radius_m * math.cos(theta) / metres_per_deg_lat,
            )
        )
    return out


def edge_jaccard(a: set[int], b: set[int]) -> float:
    """Shared ground between two candidates, as edge-set Jaccard."""
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union)


def keep_distinct(
    candidates: list[dict], *, max_keep: int, max_overlap: float = 0.5
) -> list[dict]:
    """Best-scored candidates that are actually different routes.

    Candidates must carry `score` and `edge_ids` (a set). Greedy by score: a
    candidate sharing more than `max_overlap` of its ground with an already
    kept one is the same route asked twice, and the better one already speaks.
    """
    # 0.5: a candidate sharing half its ground with a kept one is a variation,
    # not a route. Generous thresholds let near-duplicates crowd out genuinely
    # different loops from the same start.
    kept: list[dict] = []
    for candidate in sorted(candidates, key=lambda c: -c["score"]):
        if len(kept) >= max_keep:
            break
        if any(
            edge_jaccard(candidate["edge_ids"], other["edge_ids"]) > max_overlap
            for other in kept
        ):
            continue
        kept.append(candidate)
    return kept
