"""The topological split: junctions by shared use, never by geometry."""

from topology.split import split_at_junctions, vertex_usage

A, B, C, D, E = (0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (1.0, 1.0)


def test_no_junction_one_piece() -> None:
    assert split_at_junctions([A, B, C], set()) == [[A, B, C]]


def test_interior_junction_cuts_and_shares_the_vertex() -> None:
    pieces = split_at_junctions([A, B, C, D], {B})
    assert pieces == [[A, B], [B, C, D]]
    # The cut coordinate belongs to both pieces: a boundary, not a gap.
    assert pieces[0][-1] == pieces[1][0]


def test_usage_counts_occurrences_across_ways() -> None:
    usage = vertex_usage([[A, B, C], [E, B, D]])
    assert usage[B] == 2  # shared by two ways -> junction
    assert usage[A] == 1


def test_self_loop_vertex_is_a_junction_of_the_way_with_itself() -> None:
    lollipop = [A, B, C, E, B]  # stick A-B, loop B-C-E-B
    usage = vertex_usage([lollipop])
    assert usage[B] == 2
    pieces = split_at_junctions(lollipop, {c for c, n in usage.items() if n >= 2})
    assert pieces == [[A, B], [B, C, E, B]]


def test_crossing_without_shared_coordinate_does_not_cut() -> None:
    # A bridge: two ways cross geometrically but share no coordinate. The
    # junction set is built from shared USE, so neither is split — welding
    # them is the pgr_nodeNetwork trap this module exists to avoid.
    over = [(0.0, -1.0), (0.0, 1.0)]
    under = [(-1.0, 0.0), (1.0, 0.0)]
    usage = vertex_usage([over, under])
    junctions = {c for c, n in usage.items() if n >= 2}
    assert junctions == set()
    assert split_at_junctions(over, junctions) == [over]


def test_degenerate_input_returns_no_pieces() -> None:
    assert split_at_junctions([A], set()) == []
    assert split_at_junctions([], set()) == []
    # consecutive duplicates collapse to nothing rather than a zero-length line
    assert split_at_junctions([A, A], set()) == []


def test_closed_ring_stays_one_piece_until_a_junction_cuts_it() -> None:
    ring = [A, B, E, A]
    # A appears twice (start=end): usage 2, so the ring is cut at its seam,
    # which keeps source==target explicit rather than accidental.
    usage = vertex_usage([ring])
    junctions = {c for c, n in usage.items() if n >= 2}
    assert split_at_junctions(ring, junctions) == [[A, B, E, A]]
