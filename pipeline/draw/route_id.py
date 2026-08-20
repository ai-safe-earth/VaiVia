"""The route id: derived from the ground the route covers, nothing else.

docs/social-layer.md imposes this before the first comment exists: photos,
comments and likes key to `route.id`, so an id that changes when the catalogue
is rebuilt orphans them silently. That rules out every convenient identity —
sequence numbers change with generation order, `run_id`s change every run, and
vertex ids do not survive `build_network` (TRUNCATE ... RESTART IDENTITY).

What survives a rebuild is the GROUND: the coordinates a route passes over.
So the id is a hash of the route's own line, with coordinates rounded to 5
decimal places (~1.1 m at this latitude) so that sub-metre geometry noise —
a weld moving an endpoint 40 cm, a float printing differently — cannot rename
a route, while any real change of path does. A regenerated route over the same
ground keeps its id; a genuinely different route IS a new route, and the old
one is superseded rather than mutated, which is exactly what a comment thread
needs.

Direction is normalised: the same loop walked clockwise and anticlockwise is
the same ground, and two candidates that differ only in direction must
collide, not coexist.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

Coord = tuple[float, float]

# ~1.1 m of longitude at 46°N. Inside geometry noise, outside any real reroute.
ROUND = 5


def canonical(coords: Sequence[Coord]) -> tuple[Coord, ...]:
    """The line, rounded and direction-normalised.

    Consecutive duplicates AFTER rounding are collapsed — two points 30 cm
    apart become the same point at 5 decimals, and keeping both would make the
    id depend on vertex density rather than on ground.
    """
    rounded: list[Coord] = []
    for x, y in coords:
        point = (round(x, ROUND), round(y, ROUND))
        if not rounded or rounded[-1] != point:
            rounded.append(point)
    forward = tuple(rounded)
    backward = tuple(reversed(rounded))
    return min(forward, backward)


def route_id(coords: Sequence[Coord]) -> str:
    """`generated-<16 hex>` for the ground this line covers."""
    line = canonical(coords)
    payload = ";".join(f"{x:.{ROUND}f},{y:.{ROUND}f}" for x, y in line)
    digest = hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]
    return f"generated-{digest}"
