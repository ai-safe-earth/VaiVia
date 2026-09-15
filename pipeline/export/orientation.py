"""Which way a mapped route walks each edge. Pure, so it is tested.

Edges store their attributes along their own stored geometry (source →
target); a route that walks an edge backwards swaps ascent and descent and
reverses the profile — the inversion metadata-rules.md specifies and
`draw/assemble.py` already applies to generated routes, because the generator
KNOWS its walk. A mapped OSM relation records membership, not orientation
(`source_map.edge_route` has no direction column), so for mapped routes the
walk has to be INFERRED from geometry — which is what this module does, from
each edge's position along the merged line it belongs to.

The rules, each pinned by a test:

  * **An edge's direction is where its endpoints sit along its piece.** SQL
    supplies ``ST_LineLocatePoint`` fractions for the edge's start and end;
    start-before-end means the walk agrees with the stored edge.
  * **A closed ring wraps.** The edge spanning the ring's seam reads
    f_start≈0.98, f_end≈0.02 — a naive comparison calls it backwards. On a
    closed piece the comparison is modular: forward when the wrapped gap
    (f_end − f_start mod 1) is under a half-turn.
  * **Pieces chain in member order, oriented by their endpoints.** A route
    held in pieces (small gaps, ≥0.9 matched — the owner's floor) walks its
    pieces in the relation's member order; each piece faces whichever way
    puts its start nearest the previous piece's end. The first pair is
    chosen jointly, then the chain is greedy.
  * **A twice-walked edge contributes both its numbers.** An out-and-back
    stretch (the same way twice in one relation) climbs the edge's ascent
    AND its descent — one per pass — whichever way it is stored. Any odder
    repetition count makes the climb absent rather than guessed, and any
    repetition makes the profile absent: two passes have no honest order
    along a collapsed corridor.
  * **Absent is not zero**, as everywhere: an edge that joined no piece, or
    a piece that will not chain, makes the whole orientation None and the
    climb unknown — never a confident understatement.
"""

from __future__ import annotations

import math
from typing import NamedTuple

Point = tuple[float, float]


class Edge(NamedTuple):
    """One distinct edge of a route, located along its piece."""

    edge_id: int
    piece_no: int
    f_start: float  # fraction of the edge's stored start along the piece
    f_end: float  # fraction of the edge's stored end along the piece
    length_m: float
    ascent_m: float | None
    descent_m: float | None
    profile_m: list[float] | None
    occurrences: int  # how many times the relation walks this edge


class Piece(NamedTuple):
    """One piece of the merged line, as stored."""

    piece_no: int
    start: Point
    end: Point
    min_member: int  # earliest member_index among its edges: walk order
    closed: bool  # start == end: a ring, where fractions wrap


class Step(NamedTuple):
    """One step of the walk: an edge, walked forward or backwards."""

    edge: Edge
    forward: bool


def _forward_on_piece(edge: Edge, closed: bool) -> bool:
    if closed:
        return (edge.f_end - edge.f_start) % 1.0 < 0.5
    return edge.f_start < edge.f_end


def _entry_fraction(edge: Edge, forward: bool) -> float:
    return edge.f_start if forward else edge.f_end


def _piece_steps(edges: list[Edge], closed: bool) -> list[Step]:
    """One piece's edges in stored-line order, each with its direction."""
    steps = [Step(e, _forward_on_piece(e, closed)) for e in edges]
    steps.sort(key=lambda s: _entry_fraction(s.edge, s.forward))
    return steps


def _gap(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _ends(piece: Piece, reversed_: bool) -> tuple[Point, Point]:
    return (piece.end, piece.start) if reversed_ else (piece.start, piece.end)


def _piece_order(pieces: list[Piece]) -> list[tuple[Piece, bool]]:
    """Pieces in member order, each True when walked against its stored line."""
    ordered = sorted(pieces, key=lambda p: p.min_member)
    if len(ordered) == 1:
        return [(ordered[0], False)]
    # The first pair fixes both orientations jointly; after that each piece
    # simply faces the walk.
    first, second = ordered[0], ordered[1]
    best = min(
        ((r1, r2) for r1 in (False, True) for r2 in (False, True)),
        key=lambda rs: _gap(_ends(first, rs[0])[1], _ends(second, rs[1])[0]),
    )
    flips = [best[0], best[1]]
    for nxt in ordered[2:]:
        prev_end = _ends(ordered[len(flips) - 1], flips[-1])[1]
        flips.append(_gap(nxt.end, prev_end) < _gap(nxt.start, prev_end))
    return list(zip(ordered, flips))


def walk_route(edges: list[Edge], pieces: list[Piece]) -> list[Step] | None:
    """The whole route's walk: pieces in order, steps in order, directions set.

    None when the walk cannot be honestly known: an edge outside every piece
    (piece_no < 0 marks the SQL's failed join) or a piece with no edges.
    """
    if not edges or not pieces:
        return None
    if any(e.piece_no < 0 for e in edges):
        return None
    by_piece: dict[int, list[Edge]] = {}
    for edge in edges:
        by_piece.setdefault(edge.piece_no, []).append(edge)
    if set(by_piece) != {p.piece_no for p in pieces}:
        return None

    walk: list[Step] = []
    for piece, reversed_ in _piece_order(pieces):
        steps = _piece_steps(by_piece[piece.piece_no], piece.closed)
        if reversed_:
            steps = [Step(s.edge, not s.forward) for s in reversed(steps)]
        walk.extend(steps)
    return walk


def climb(walk: list[Step] | None) -> tuple[float, float] | None:
    """(ascent, descent) along the walk, or None when it cannot be known.

    A twice-walked edge contributes ascent AND descent — one per pass. Any
    other repetition count is a shape this rule does not cover: absent.
    """
    if walk is None:
        return None
    up = down = 0.0
    for edge, forward in walk:
        if edge.ascent_m is None or edge.descent_m is None:
            return None
        if edge.occurrences == 1:
            up += edge.ascent_m if forward else edge.descent_m
            down += edge.descent_m if forward else edge.ascent_m
        elif edge.occurrences == 2:
            up += edge.ascent_m + edge.descent_m
            down += edge.ascent_m + edge.descent_m
        else:
            return None
    return up, down


def walked_distance(walk: list[Step] | None) -> float | None:
    """Metres actually walked: a twice-walked edge counts twice."""
    if walk is None:
        return None
    return sum(edge.length_m * edge.occurrences for edge, _forward in walk)


def profile_steps(walk: list[Step] | None) -> list[tuple[list[float], float]] | None:
    """(elevations, length_m) per step, reversed where walked backwards.

    None when the walk is unknown, any sample is missing, or any edge is
    walked twice — two passes have no honest order along a collapsed line.
    """
    if walk is None:
        return None
    out: list[tuple[list[float], float]] = []
    for edge, forward in walk:
        if edge.occurrences != 1:
            return None
        if not edge.profile_m or any(z is None for z in edge.profile_m):
            return None
        series = list(edge.profile_m) if forward else list(reversed(edge.profile_m))
        out.append((series, edge.length_m))
    return out


def climbing_gradients(
    distance_m: list[float], elevation_m: list[float]
) -> tuple[float, float] | None:
    """Mean climbing gradient of this direction and of its reverse.

    The owner's recommendation rule (2026-08-27): of a loop's two directions,
    recommend the one that takes the steep side UP — the higher mean gradient
    over its climbing metres. This direction's climbs are the reverse
    direction's descents, so one pass over the series yields both.
    """
    if len(distance_m) < 2 or len(distance_m) != len(elevation_m):
        return None
    up = down = 0.0
    up_run = down_run = 0.0
    for i in range(1, len(distance_m)):
        run = distance_m[i] - distance_m[i - 1]
        delta = elevation_m[i] - elevation_m[i - 1]
        if delta > 0:
            up += delta
            up_run += run
        elif delta < 0:
            down -= delta
            down_run += run
    forward = up / up_run if up_run > 0 else 0.0
    reverse = down / down_run if down_run > 0 else 0.0
    return forward, reverse
