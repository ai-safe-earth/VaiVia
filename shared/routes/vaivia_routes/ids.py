"""Route identity: derived from the ground, direction-aware, minted ONLY here.

docs/social-layer.md imposes the stability rule before the first comment
exists: photos, comments and likes key to `route.id`, so an id that changes
when the catalogue is rebuilt orphans them silently. That rules out every
convenient identity — sequence numbers change with generation order,
`run_id`s change every run, vertex and edge ids do not survive
`build_network` (TRUNCATE ... RESTART IDENTITY). What survives is the
GROUND: the coordinates the route passes over, rounded to 5 decimals
(~1.1 m at 46°N) so sub-metre noise cannot rename a route while any real
reroute does.

The v2 format, per the ratified start/end contract (docs/route-document.md):

    vv2-<16 hex>            an out-and-back or destination route — one
                            outing that already contains both directions
    vv2-<16 hex>-fwd|-rev   a shape where the walker CHOOSES a direction
                            (loop, circular, linear): each direction is its
                            own document, sharing the digest. A hyphen, not
                            a colon: the id IS the document's filename, and
                            NTFS reads a colon as an Alternate Data Stream
                            separator — 877 documents once vanished into
                            extension-less stream carriers proving it

The `-fwd` sense is fixed by geometry alone: the orientation whose canonical
rounded coordinate sequence is the lexicographic minimum. Never by edge_id
(reassigned every rebuild — the amendment to PR #32) and never by generation
order. A photo attached to the anticlockwise walk must not migrate to the
clockwise one on the next rebuild.

Mapped (OSM) routes take the same geometry-derived id — the owner's ruling
this refactor implements — with `osm-relation-<n>` retiring into
`identity.osm_relation_id` as provenance. A multi-piece mapped route hashes
its pieces individually normalised and lexicographically ordered, so piece
ORDER (an artefact of assembly) cannot rename a route either.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

Coord = tuple[float, float]

#: One grid cell is 0.77 m east-west and 1.11 m north-south at 46°N — inside
#: geometry noise, outside any real reroute. The honest caveat: rounding is a
#: grid snap without hysteresis, so a repair that moves a vertex ACROSS a
#: cell edge renames the route however small the move (the emit drift guard
#: makes that loud, never silent). The upstream mitigation is for repairs to
#: land welds ON the 1e-5 grid; until then a post-emission weld is treated
#: as a supersession, not an identity.
ROUND = 5

PREFIX = "vv2"

#: Shapes where direction is the walker's choice: two documents per ground,
#: `-fwd` and `-rev`. An out-and-back (`out_and_back`, `destination`) is one
#: outing containing both legs, so it carries no suffix.
DIRECTED_SHAPES = frozenset({"loop", "circular", "linear"})


def _rounded(coords: Sequence[Coord]) -> list[Coord]:
    rounded: list[Coord] = []
    for x, y in coords:
        point = (round(x, ROUND), round(y, ROUND))
        if not rounded or rounded[-1] != point:
            rounded.append(point)
    return rounded


def _ring_canonical(opened: list[Coord]) -> tuple[tuple[Coord, ...], bool]:
    """The canonical cyclic form of an OPEN ring, and whether the stored
    direction is the canonical one.

    A closed way's starting vertex is an assembly artefact — ST_LineMerge
    starts a pure ring wherever its input order lands, and a rebuild
    reorders that input — so a ring is normalised over BOTH directions and
    every rotation that starts at its minimal vertex. Without this, every
    loop-shaped route renamed on rebuild (found live: five rotations of one
    ring, five digests).
    """

    def best_rotation(seq: list[Coord]) -> tuple[Coord, ...]:
        low = min(seq)
        return min(tuple(seq[i:] + seq[:i]) for i, p in enumerate(seq) if p == low)

    forward = best_rotation(opened)
    backward = best_rotation(list(reversed(opened)))
    if forward <= backward:
        return forward, True
    return backward, False


def canonical_piece(coords: Sequence[Coord]) -> tuple[Coord, ...]:
    """One line, rounded, direction-normalised — and rotation-normalised
    when it is a closed ring.

    Consecutive duplicates AFTER rounding are collapsed — two points 30 cm
    apart become the same point at 5 decimals, and keeping both would make
    the id depend on vertex density rather than on ground. A ring is
    re-closed after normalisation, so open and closed lines can never
    collide by construction.
    """
    rounded = _rounded(coords)
    if len(rounded) > 2 and rounded[0] == rounded[-1]:
        canon, _ = _ring_canonical(rounded[:-1])
        return canon + (canon[0],)
    forward = tuple(rounded)
    backward = tuple(reversed(rounded))
    return min(forward, backward)


def forward_is_stored(coords: Sequence[Coord]) -> bool:
    """Is the STORED orientation the `-fwd` one?

    For an open line: the given order reads as the lexicographic-minimum
    orientation. For a closed ring: the given rotation's direction is the
    canonical cycle's direction — the start vertex cancels out, so a
    rebuild that re-opens the ring elsewhere cannot flip the sense. A
    palindrome — a strict out-and-back's coordinate list — reads True,
    harmlessly: its shapes carry no suffix.
    """
    rounded = _rounded(coords)
    if len(rounded) > 2 and rounded[0] == rounded[-1]:
        _, stored_is_forward = _ring_canonical(rounded[:-1])
        return stored_is_forward
    return tuple(rounded) <= tuple(reversed(rounded))


def digest(pieces: Sequence[Sequence[Coord]]) -> str:
    """16 hex over the canonical ground: pieces normalised, then ordered."""
    canon = sorted(canonical_piece(piece) for piece in pieces)
    payload = "|".join(
        ";".join(f"{x:.{ROUND}f},{y:.{ROUND}f}" for x, y in piece) for piece in canon
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]


def route_id(
    pieces: Sequence[Sequence[Coord]],
    shape: str,
    direction: str | None = "fwd",
) -> str:
    """The id for this ground, this shape, this direction.

    Directed shapes require a direction ('fwd' or 'rev'); undirected shapes
    ignore it. P8 emits the `:rev` siblings; until then every directed
    document is the `:fwd` one and `reverse_of` names the id its sibling
    will carry.
    """
    core = f"{PREFIX}-{digest(pieces)}"
    if shape in DIRECTED_SHAPES:
        if direction not in ("fwd", "rev"):
            raise ValueError(f"directed shape {shape!r} needs direction fwd|rev")
        return f"{core}-{direction}"
    return core


def sibling_id(one_id: str) -> str | None:
    """The other direction's id, or None for an undirected id."""
    if one_id.endswith("-fwd"):
        return one_id[:-4] + "-rev"
    if one_id.endswith("-rev"):
        return one_id[:-4] + "-fwd"
    return None
