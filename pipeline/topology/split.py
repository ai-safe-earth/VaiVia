"""Split ways at junctions. Topological, pure, and pinned by tests.

A junction is a COORDINATE SHARED BY USE, never a geometric crossing: OSM ways
meet at literal shared nodes, so two ways that cross without sharing one are a
bridge over a road — welding them (what geometric noding does) would route
walkers through the air. Coordinates from the same OSM node are bit-identical
in the source, so exact float equality is the correct join key, and rounding
would only invent junctions that do not exist.

Direction: pieces keep their parent way's direction, so directional tags
(oneway, incline) stay valid as stored. The inversion rule in
docs/metadata-rules.md applies when ASSEMBLY reverses a piece, not here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from itertools import pairwise

Coord = tuple[float, float]


def vertex_usage(ways: Iterable[list[Coord]]) -> Counter[Coord]:
    """How often each coordinate occurs across all ways.

    Occurrences, not distinct ways: a way passing through the same coordinate
    twice (a self-loop) makes that coordinate a junction of the way with
    itself, which is topologically exactly what it is.
    """
    usage: Counter[Coord] = Counter()
    for coords in ways:
        usage.update(coords)
    return usage


def split_at_junctions(coords: list[Coord], junctions: set[Coord]) -> list[list[Coord]]:
    """One way's coordinates -> pieces, cut at junctions.

    Endpoints always bound a piece; an interior coordinate cuts when it is a
    junction. A cut coordinate belongs to BOTH adjacent pieces — the piece
    boundary is the shared vertex, not a gap. Degenerate input (fewer than two
    coordinates, or all pieces collapsing) returns no pieces rather than
    inventing geometry.
    """
    if len(coords) < 2:
        return []
    cut_indexes = (
        [0]
        + [i for i in range(1, len(coords) - 1) if coords[i] in junctions]
        + [len(coords) - 1]
    )
    pieces = []
    for a, b in pairwise(cut_indexes):
        piece = coords[a : b + 1]
        # Consecutive duplicate coordinates make zero-length fragments; a
        # piece needs two DISTINCT points to be a line.
        if len(piece) >= 2 and any(p != piece[0] for p in piece[1:]):
            pieces.append(piece)
    return pieces
