"""Expanding a relation's members into edge links. No database — pure functions.

Every case here is one that was measured against the real 752 relations before
it was written: a way listed twice in the same relation (140 of them), nested
superroute members (16), node members (2,639), and member ways the network does
not hold (5,146 of 15,392 distinct member ways).
"""

from __future__ import annotations

from curate.route_links import Link, expand_members, member_ref, normalise_role


def way(ref: int, role: str = "") -> dict:
    return {"type": "w", "ref": ref, "role": role}


def test_a_member_way_contributes_every_piece_it_was_cut_into():
    # The relation is a claim about the WAY; the pieces are an artefact of
    # noding, so all of them belong to the route.
    result = expand_members(7, [way(100)], {100: [(1, 0), (2, 1), (3, 2)]})

    assert result.links == [
        Link(edge_id=1, rel_id=7, member_index=0, piece_index=0, role=None),
        Link(edge_id=2, rel_id=7, member_index=0, piece_index=1, role=None),
        Link(edge_id=3, rel_id=7, member_index=0, piece_index=2, role=None),
    ]
    assert result.matched_way_ids == {100}
    assert result.missing_way_ids == frozenset()


def test_member_order_is_kept_as_the_relation_gave_it():
    result = expand_members(7, [way(200), way(100)], {100: [(1, 0)], 200: [(9, 0)]})

    assert [(link.edge_id, link.member_index) for link in result.links] == [
        (9, 0),
        (1, 1),
    ]


def test_the_same_way_twice_in_one_relation_gives_two_rows():
    # Measured: 140 (relation, way) pairs appear more than once — an out-and-back
    # leg walked in both directions. (edge_id, rel_id) is therefore not a key,
    # and collapsing the second visit would lose the second half of the route.
    result = expand_members(
        7, [way(100), way(200), way(100)], {100: [(1, 0)], 200: [(2, 0)]}
    )

    assert [link.member_index for link in result.links] == [0, 1, 2]
    assert result.matched_way_ids == {100, 200}  # distinct ways, not member entries


def test_a_member_way_the_network_does_not_hold_is_counted_not_dropped_silently():
    # Outside the region bboxes, or excluded by load/legality.py. Expected, but
    # it is the number that says whether a route is clipped or absent.
    result = expand_members(7, [way(100), way(999)], {100: [(1, 0)]})

    assert result.links == [Link(1, 7, 0, 0, None)]
    assert result.matched_way_ids == {100}
    assert result.missing_way_ids == {999}


def test_the_same_missing_way_twice_counts_once():
    result = expand_members(7, [way(999), way(999)], {})

    assert result.missing_way_ids == {999}


def test_node_and_relation_members_are_skipped_and_counted():
    members = [
        {"type": "n", "ref": 5, "role": "guidepost"},
        way(100),
        {"type": "r", "ref": 42, "role": ""},
    ]
    result = expand_members(7, members, {100: [(1, 0)]})

    assert [link.edge_id for link in result.links] == [1]
    assert result.skipped_nodes == 1
    assert result.skipped_relations == 1
    # The member index is the position in the FULL member list: dropping node
    # members from the numbering would renumber the route's ordering.
    assert result.links[0].member_index == 1


def test_an_empty_role_becomes_null_not_an_empty_string():
    # OSM spells "no role" as ''; the database spells it NULL. Keeping both
    # would make `role IS NULL` and `role = ''` two questions about one fact.
    assert expand_members(7, [way(100)], {100: [(1, 0)]}).links[0].role is None
    assert expand_members(7, [way(100, "forward")], {100: [(1, 0)]}).links[0].role == (
        "forward"
    )


def test_normalise_role():
    assert normalise_role("") is None
    assert normalise_role("   ") is None
    assert normalise_role(" forward ") == "forward"
    assert normalise_role(None) is None
    assert normalise_role(3) is None


def test_member_ref_accepts_what_osmium_and_a_fixture_write():
    assert member_ref({"ref": 100}) == 100
    assert member_ref({"ref": "100"}) == 100
    assert member_ref({"ref": "not-an-id"}) is None
    assert member_ref({}) is None
    # bool is an int in Python and would silently become way 1.
    assert member_ref({"ref": True}) is None


def test_long_member_type_spellings_are_accepted():
    # osmium writes 'w'/'n'/'r'; the OSM XML API writes the long form. Accepting
    # both costs nothing and means a fixture cannot be silently empty.
    result = expand_members(
        7,
        [
            {"type": "way", "ref": 100, "role": ""},
            {"type": "node", "ref": 5, "role": ""},
        ],
        {100: [(1, 0)]},
    )

    assert [link.edge_id for link in result.links] == [1]
    assert result.skipped_nodes == 1


def test_a_relation_with_nothing_in_the_network_produces_no_links():
    result = expand_members(7, [way(999)], {100: [(1, 0)]})

    assert result.links == []
    assert result.matched_way_ids == frozenset()
