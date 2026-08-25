"""Where sibling routes from one terminal part ways (start/end contract §6).

Measured as the normal case, not the edge case: 228 generated routes come
from 12 start vertices, only 156 of 2,127 same-start pairs diverge
immediately, and 493 pairs share a kilometre or more of identical approach.
Five results sharing three kilometres of track are nearly one answer given
five times — so each route records the vertex where it leaves the corridor
shared with its siblings and how long that shared approach is. The fact
lives in the document; diversity ON it belongs to the query service.

Pure over walked-edge steps, so the tests pin it.
"""

from __future__ import annotations

from typing import NamedTuple


class Step(NamedTuple):
    """One walked edge, as (edge, direction) with where it ends."""

    edge_id: int
    forward: bool
    length_m: float
    end_vertex: int


def shared_steps(a: list[Step], b: list[Step]) -> int:
    """How many leading steps two walks share — same edge, same direction."""
    n = 0
    for step_a, step_b in zip(a, b):
        if (step_a.edge_id, step_a.forward) != (step_b.edge_id, step_b.forward):
            break
        n += 1
    return n


def divergence(
    walks: dict[str, list[Step]],
) -> dict[str, tuple[int, float] | None]:
    """Per route: (divergence vertex, approach metres), or None.

    The corridor is measured against the LONGEST-sharing sibling: the route
    leaves the shared approach where it stops matching the sibling it
    matches farthest. A route with no siblings, or whose first step is
    already its own, records None — there is no corridor to leave.
    """
    out: dict[str, tuple[int, float] | None] = {}
    for route_id, steps in walks.items():
        best = 0
        for other_id, other_steps in walks.items():
            if other_id == route_id:
                continue
            best = max(best, shared_steps(steps, other_steps))
        if best == 0 or not steps:
            out[route_id] = None
            continue
        approach_m = sum(step.length_m for step in steps[:best])
        out[route_id] = (steps[best - 1].end_vertex, round(approach_m, 1))
    return out
