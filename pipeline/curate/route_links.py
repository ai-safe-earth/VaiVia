"""Expanding a route relation's members into edge links. Pure, so it is tested.

The join itself is one equality (`member ref == curated.edge.way_id`) and could
have been a single SQL statement. It is here instead because the expansion is
per-feature branching -- member types to skip, an empty role that means "no
role", a way listed twice in the same relation, a member way the network does
not hold -- and the division of labour in CLAUDE.md puts that in Python where a
unit test can pin it. The volume is 22,085 members, so nothing is lost by it.

What is deliberately NOT done here:

  * Nested relation members are skipped, and counted. Sixteen exist, all of them
    a superroute listing its stages (Ciclovia Pedemontana Alpina lists ten). The
    parent keeps whatever ways it holds directly; the stages are relations in
    their own right and are joined on their own. Flattening the parent would
    make every stage's edges appear twice under two names, which is a claim
    about the route that OSM did not make.
  * Node members are skipped. They are guideposts and summits, not network.
  * Direction is not resolved. See sql/0007_edge_route.sql.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple


class Link(NamedTuple):
    """One row of curated.edge_route."""

    edge_id: int
    rel_id: int
    member_index: int
    piece_index: int
    role: str | None


class Expansion(NamedTuple):
    """What one relation contributed.

    The way ids are sets rather than counts because the caller unions them
    across all 752 relations, and a way carrying two routes must not be counted
    twice in "how much of OSM's route mileage does this network hold".
    """

    links: list[Link]
    matched_way_ids: frozenset[int]  # member ways the network holds
    missing_way_ids: frozenset[int]  # member ways it does not
    skipped_nodes: int
    skipped_relations: int


def member_ref(member: dict) -> int | None:
    """The member's OSM id, or None when it is not usable as one.

    osmium writes `ref` as an int; a hand-built fixture may spell it as a
    string. Both are accepted, anything else is not a member id.
    """
    ref = member.get("ref")
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str):
        try:
            return int(ref)
        except ValueError:
            return None
    return None


def normalise_role(role: object) -> str | None:
    """OSM's "no role" is the empty string; the database's is NULL.

    Keeping both would make `role IS NULL` and `role = ''` two different
    questions about the same fact.
    """
    if not isinstance(role, str):
        return None
    stripped = role.strip()
    return stripped or None


def expand_members(
    rel_id: int,
    members: Iterable[dict],
    edges_by_way: dict[int, list[tuple[int, int]]],
) -> Expansion:
    """Turn one relation's member list into edge_route rows.

    `edges_by_way` maps a way id to its (edge_id, piece_index) pieces -- the
    pieces build_network cut the way into. A member way contributes every one of
    its pieces, because the relation is a claim about the WAY and the pieces are
    an artefact of noding.
    """
    links: list[Link] = []
    seen_matched: set[int] = set()
    seen_missing: set[int] = set()
    skipped_nodes = 0
    skipped_relations = 0

    for member_index, member in enumerate(members):
        kind = member.get("type")
        if kind in ("n", "node"):
            skipped_nodes += 1
            continue
        if kind in ("r", "relation"):
            skipped_relations += 1
            continue
        if kind not in ("w", "way"):
            continue

        way_id = member_ref(member)
        if way_id is None:
            continue
        pieces = edges_by_way.get(way_id)
        if not pieces:
            seen_missing.add(way_id)
            continue

        seen_matched.add(way_id)
        role = normalise_role(member.get("role"))
        for edge_id, piece_index in pieces:
            links.append(Link(edge_id, rel_id, member_index, piece_index, role))

    return Expansion(
        links=links,
        matched_way_ids=frozenset(seen_matched),
        missing_way_ids=frozenset(seen_missing),
        skipped_nodes=skipped_nodes,
        skipped_relations=skipped_relations,
    )
