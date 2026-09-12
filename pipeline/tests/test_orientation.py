"""The mapped-route walk inference: every rule in export/orientation.py pinned.

Getting a direction wrong does not crash; it reports a mountain loop as flat
(the same failure mode test_draw.py guards for generated routes), so the
seam wrap, the piece chaining and the out-and-back rule each get a test.
"""

from export.orientation import (
    Edge,
    Piece,
    Step,
    climb,
    climbing_gradients,
    profile_steps,
    walk_route,
    walked_distance,
)


def edge(eid, f_start, f_end, piece_no=1, occurrences=1, **kw):
    defaults = {
        "length_m": 100.0,
        "ascent_m": 30.0,
        "descent_m": 10.0,
        "profile_m": [100.0, 120.0],
    }
    defaults.update(kw)
    return Edge(eid, piece_no, f_start, f_end, occurrences=occurrences, **defaults)


def piece(no=1, start=(0.0, 0.0), end=(1.0, 0.0), min_member=0, closed=False):
    return Piece(no, start, end, min_member, closed)


def test_open_line_direction_and_order():
    walk = walk_route([edge(2, 0.9, 0.5), edge(1, 0.0, 0.5)], [piece()])
    assert walk == [
        Step(edge(1, 0.0, 0.5), True),  # stored with the line
        Step(edge(2, 0.9, 0.5), False),  # stored against it, entered at 0.5
    ]


def test_a_backwards_edge_swaps_ascent_and_descent():
    forward = climb(walk_route([edge(1, 0.0, 1.0)], [piece()]))
    backward = climb(walk_route([edge(1, 1.0, 0.0)], [piece()]))
    assert forward == (30.0, 10.0)
    assert backward == (10.0, 30.0)


def test_the_ring_seam_edge_wraps_instead_of_reading_backwards():
    ring = piece(closed=True, end=(0.0, 0.0))
    seam = edge(1, 0.98, 0.02)
    inner = edge(2, 0.02, 0.5)
    walk = walk_route([seam, inner], [ring])
    assert [(s.edge.edge_id, s.forward) for s in walk] == [(2, True), (1, True)]
    # ...and the genuinely backwards edge on a ring still reads backwards.
    walk = walk_route([edge(1, 0.5, 0.1)], [ring])
    assert walk[0].forward is False


def test_pieces_chain_in_member_order_oriented_by_endpoints():
    # Piece 2 follows piece 1 but is STORED end-first: the chain must flip it.
    first = piece(no=1, start=(0.0, 0.0), end=(1.0, 0.0), min_member=0)
    second = piece(no=2, start=(3.0, 0.0), end=(1.1, 0.0), min_member=5)
    walk = walk_route(
        [edge(1, 0.0, 1.0, piece_no=1), edge(2, 0.0, 1.0, piece_no=2)],
        [first, second],
    )
    assert [(s.edge.edge_id, s.forward) for s in walk] == [(1, True), (2, False)]


def test_the_first_piece_faces_the_chain_too():
    # Piece 1 is stored pointing AWAY from piece 2: the joint first-pair
    # choice must reverse it, flipping its edge.
    first = piece(no=1, start=(1.0, 0.0), end=(0.0, 0.0), min_member=0)
    second = piece(no=2, start=(1.1, 0.0), end=(2.0, 0.0), min_member=3)
    walk = walk_route(
        [edge(1, 0.0, 1.0, piece_no=1), edge(2, 0.0, 1.0, piece_no=2)],
        [first, second],
    )
    assert [(s.edge.edge_id, s.forward) for s in walk] == [(1, False), (2, True)]


def test_a_twice_walked_edge_contributes_both_its_numbers():
    walk = walk_route([edge(1, 0.0, 1.0, occurrences=2)], [piece()])
    assert climb(walk) == (40.0, 40.0)  # ascent + descent, one per pass
    assert walked_distance(walk) == 200.0
    assert profile_steps(walk) is None  # two passes have no honest order


def test_odder_repetition_makes_the_climb_absent():
    assert climb(walk_route([edge(1, 0.0, 1.0, occurrences=3)], [piece()])) is None


def test_absent_is_not_zero():
    assert climb(walk_route([edge(1, 0.0, 1.0, ascent_m=None)], [piece()])) is None
    assert walk_route([edge(1, 0.0, 1.0, piece_no=-1)], [piece()]) is None
    assert climb(None) is None
    assert profile_steps(None) is None
    assert walked_distance(None) is None


def test_profile_steps_reverse_a_backwards_edge():
    walk = walk_route(
        [edge(1, 0.0, 0.5), edge(2, 1.0, 0.5, profile_m=[300.0, 200.0])],
        [piece()],
    )
    assert profile_steps(walk) == [([100.0, 120.0], 100.0), ([200.0, 300.0], 100.0)]


def test_recommended_direction_takes_the_steep_side_up():
    # Steep 50 m climb over 100 m, gentle 10 m drop over 100 m: this
    # direction climbs at 0.5 and its reverse at 0.1 — recommend this one.
    grads = climbing_gradients([0.0, 100.0, 200.0], [0.0, 50.0, 40.0])
    assert grads == (0.5, 0.1)
    flat = climbing_gradients([0.0, 100.0], [10.0, 10.0])
    assert flat == (0.0, 0.0)
    assert climbing_gradients([0.0], [1.0]) is None
