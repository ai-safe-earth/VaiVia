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
    vv2-<16 hex>:fwd|:rev   a shape where the walker CHOOSES a direction
                            (loop, circular, linear): each direction is its
                            own document, sharing the digest

The `:fwd` sense is fixed by geometry alone: the orientation whose canonical
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

#: ~1.1 m of longitude at 46°N. Inside geometry noise, outside any real reroute.
ROUND = 5

PREFIX = "vv2"

#: Shapes where direction is the walker's choice: two documents per ground,
#: `:fwd` and `:rev`. An out-and-back (`out_and_back`, `destination`) is one
#: outing containing both legs, so it carries no suffix.
DIRECTED_SHAPES = frozenset({"loop", "circular", "linear"})


def canonical_piece(coords: Sequence[Coord]) -> tuple[Coord, ...]:
    """One line, rounded and direction-normalised.

    Consecutive duplicates AFTER rounding are collapsed — two points 30 cm
    apart become the same point at 5 decimals, and keeping both would make
    the id depend on vertex density rather than on ground.
    """
    rounded: list[Coord] = []
    for x, y in coords:
        point = (round(x, ROUND), round(y, ROUND))
        if not rounded or rounded[-1] != point:
            rounded.append(point)
    forward = tuple(rounded)
    backward = tuple(reversed(rounded))
    return min(forward, backward)


def forward_is_stored(coords: Sequence[Coord]) -> bool:
    """Is the STORED orientation the `:fwd` one?

    True when the line as given reads as the canonical (lexicographic
    minimum) orientation. A palindrome — a strict out-and-back's coordinate
    list — reads True, harmlessly: its shapes carry no suffix.
    """
    rounded: list[Coord] = []
    for x, y in coords:
        point = (round(x, ROUND), round(y, ROUND))
        if not rounded or rounded[-1] != point:
            rounded.append(point)
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
        return f"{core}:{direction}"
    return core


def sibling_id(one_id: str) -> str | None:
    """The other direction's id, or None for an undirected id."""
    if one_id.endswith(":fwd"):
        return one_id[:-4] + ":rev"
    if one_id.endswith(":rev"):
        return one_id[:-4] + ":fwd"
    return None
