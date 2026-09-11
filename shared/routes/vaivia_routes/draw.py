"""Drawing shapes over the pack — the same semantics as pipeline/draw/generate.py.

These are the pgRouting draw functions re-said over the CSR: the same via
rings, the same soft penalty for walked legs, the same strict-return
construction. The parity test (627 catalogue ids reproduced from the pack)
is what makes "the same" a checked claim rather than a hope.
"""

from __future__ import annotations

from vaivia_routes.assemble import WalkedEdge, strict_return
from vaivia_routes.loops import ring_points
from vaivia_routes.network import Network

Coord = tuple[float, float]


def draw_loop(
    net: Network,
    start_vertex: int,
    start: Coord,
    target_m: float,
    seed: int,
) -> list[WalkedEdge] | None:
    """start → via₁ → via₂ → start, with walked legs soft-penalised."""
    vias = [
        net.nearest_vertex(p.lon, p.lat) for p in ring_points(start, target_m, seed)
    ]
    waypoints = [start_vertex, *vias, start_vertex]
    steps: list[tuple[int, bool]] = []
    walked: set[int] = set()
    for leg_from, leg_to in zip(waypoints, waypoints[1:]):
        if leg_from == leg_to:
            continue
        leg = net.route(leg_from, leg_to, penalised=walked)
        if leg is None:
            return None  # disconnected ask — the whole loop is off
        steps.extend(leg)
        walked.update(i for i, _forward in leg)
    if not steps:
        return None
    return net.walked(steps)


def draw_out_and_back(
    net: Network, start_vertex: int, destination_vertex: int
) -> list[WalkedEdge] | None:
    """Out to the destination, back with the out leg soft-penalised."""
    if destination_vertex == start_vertex:
        return None
    out = net.route(start_vertex, destination_vertex)
    if out is None:
        return None
    back = net.route(destination_vertex, start_vertex, penalised={i for i, _f in out})
    if back is None:
        return None
    return net.walked(out + back)


def draw_strict_out_and_back(
    net: Network, start_vertex: int, destination_vertex: int
) -> list[WalkedEdge] | None:
    """Out over the two-way subset, home on EXACTLY the outbound edges."""
    if destination_vertex == start_vertex:
        return None
    out = net.route(start_vertex, destination_vertex, two_way_only=True)
    if out is None:
        return None
    return net.walked(out + strict_return(out))
