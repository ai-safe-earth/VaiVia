"""Route generation's pure core. No database — pure functions.

The direction tests are the load-bearing ones: getting the swap wrong does not
crash, it reports a mountain loop as flat.
"""

from __future__ import annotations

import pytest

from draw.assemble import (
    Assembled,
    WalkedEdge,
    assemble,
    climb,
    concatenate,
    mtb,
    off_road,
    profile,
    retrace,
    score,
)
from draw.loops import edge_jaccard, keep_distinct, ring_points
from draw.route_id import canonical, route_id


def edge(
    edge_id: int = 1,
    forward: bool = True,
    length_m: float = 100.0,
    coords: list | None = None,
    profile_m: list | None = None,
    ascent_m: float | None = 0.0,
    descent_m: float | None = 0.0,
    surface: str | None = None,
    sac: str | None = None,
    mtb_scale: str | None = None,
    highway: str | None = "path",
    bike: bool = True,
) -> WalkedEdge:
    return WalkedEdge(
        edge_id,
        forward,
        length_m,
        coords or [(9.30, 45.90), (9.31, 45.91)],
        profile_m,
        ascent_m,
        descent_m,
        surface,
        sac,
        mtb_scale,
        highway,
        bike,
    )


# ── The id ───────────────────────────────────────────────────────────────────


def test_the_id_survives_sub_metre_noise():
    # A weld moving an endpoint 40 cm must not rename the route: a comment
    # would orphan (docs/social-layer.md).
    line = [(9.330442, 45.927688), (9.331000, 45.928100), (9.332500, 45.929000)]
    nudged = [(9.330444, 45.927690), (9.331002, 45.928098), (9.332498, 45.929002)]

    assert route_id(line) == route_id(nudged)


def test_the_id_changes_when_the_ground_changes():
    line = [(9.3304, 45.9277), (9.3310, 45.9281)]
    rerouted = [(9.3304, 45.9277), (9.3350, 45.9300)]

    assert route_id(line) != route_id(rerouted)


def test_the_same_loop_walked_either_way_is_one_route():
    line = [(9.30, 45.90), (9.31, 45.91), (9.32, 45.90), (9.30, 45.90)]

    assert route_id(line) == route_id(list(reversed(line)))


def test_canonical_collapses_points_that_round_together():
    # Two points 30 cm apart are the same ground at 5 decimals; keeping both
    # would make the id depend on vertex density.
    dense = [(9.300001, 45.900001), (9.300002, 45.900002), (9.310000, 45.910000)]

    assert len(canonical(dense)) == 2


def test_the_id_shape_is_stable():
    assert route_id([(9.3, 45.9), (9.4, 45.95)]).startswith("generated-")
    assert len(route_id([(9.3, 45.9), (9.4, 45.95)])) == len("generated-") + 16


# ── Direction ────────────────────────────────────────────────────────────────


def test_an_edge_walked_backwards_swaps_ascent_and_descent():
    # 100 m of climb stored source→target, walked target→source: the walker
    # DESCENDS it. The wrong answer here reports a mountain as flat.
    stored_climb = edge(1, forward=False, ascent_m=100.0, descent_m=5.0)

    up, down = climb([stored_climb])

    assert (up, down) == (5.0, 100.0)


def test_a_loop_of_one_edge_up_then_back_down_balances():
    up_leg = edge(1, forward=True, ascent_m=300.0, descent_m=10.0)
    back_down = edge(1, forward=False, ascent_m=300.0, descent_m=10.0)

    up, down = climb([up_leg, back_down])

    assert up == down == 310.0


def test_unknown_climb_on_any_edge_makes_the_route_unknown():
    edges = [
        edge(1, ascent_m=50.0, descent_m=0.0),
        edge(2, ascent_m=None, descent_m=None),
    ]

    assert climb(edges) == (None, None)


def test_a_reversed_edge_reverses_its_profile():
    forward_edge = edge(1, forward=True, profile_m=[100.0, 150.0, 200.0])
    reversed_edge = edge(2, forward=False, profile_m=[300.0, 250.0, 200.0])
    # walked: 100→200 then (reversed) 200→300

    series = profile([forward_edge, reversed_edge])

    assert series is not None
    assert series["elevation_m"] == [100.0, 150.0, 200.0, 250.0, 300.0]
    assert series["distance_m"][-1] == pytest.approx(200.0, abs=0.2)


def test_concatenate_reverses_geometry_and_drops_shared_vertices():
    a = edge(1, forward=True, coords=[(0.0, 0.0), (1.0, 0.0)])
    b = edge(2, forward=False, coords=[(2.0, 0.0), (1.0, 0.0)])  # stored away from us

    line = concatenate([a, b])

    assert line == [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]


# ── The sequence rules ───────────────────────────────────────────────────────


def test_mtb_conjunction_along_the_sequence():
    # The spike's caveat repaired: a route that WALKS a forbidden edge is not a
    # bike route; one that does not, is — no corridor to slip a crossing in.
    legal = [edge(1, bike=True, mtb_scale="1"), edge(2, bike=True, mtb_scale="1")]
    blocked = [edge(1, bike=True, length_m=4900), edge(2, bike=False, length_m=100)]

    assert mtb(legal) == (True, "1", 0.0)
    # The verdict carries the blocked METRES: failing on 100 m and failing on
    # 1.6 km must not read identically as "no".
    assert mtb(blocked) == (False, None, 100.0)
    assert mtb([]) == (None, None, 0.0)


def test_retrace_counts_repeat_visits_whatever_the_direction():
    out_and_back = [
        edge(1, forward=True, length_m=500),
        edge(2, forward=True, length_m=500),
        edge(2, forward=False, length_m=500),
        edge(1, forward=False, length_m=500),
    ]

    assert retrace(out_and_back) == pytest.approx(0.5)


def test_a_true_loop_retraces_nothing():
    loop = [edge(1, length_m=400), edge(2, length_m=300), edge(3, length_m=300)]

    assert retrace(loop) == 0.0


def test_off_road_is_a_length_share_of_trail_highways():
    edges = [
        edge(1, length_m=600, highway="path"),
        edge(2, length_m=400, highway="residential"),
    ]

    assert off_road(edges) == pytest.approx(0.6)


def test_assemble_carries_every_rule_at_once():
    edges = [
        edge(
            1,
            length_m=1000,
            surface="gravel",
            sac="mountain_hiking",
            ascent_m=120.0,
            descent_m=10.0,
            highway="path",
            mtb_scale="1",
        ),
        edge(
            2,
            forward=False,
            length_m=1000,
            surface="gravel",
            sac="mountain_hiking",
            ascent_m=10.0,
            descent_m=90.0,
            highway="track",
            mtb_scale="1",
        ),
    ]

    result = assemble(edges)

    assert result.distance_m == 2000.0
    assert result.ascent_m == 120.0 + 90.0  # the reversed edge's descent climbs
    assert result.descent_m == 10.0 + 10.0
    assert result.sac_scale == "mountain_hiking"
    assert result.mtb_rideable is True
    assert result.off_road_share == 1.0
    assert result.surface == {"gravel": 1.0}


def test_score_prefers_off_road_loops_near_target():
    def assembled(distance_m, off_road_share, retrace_share):
        return Assembled(
            coords=[],
            distance_m=distance_m,
            ascent_m=None,
            descent_m=None,
            profile=None,
            surface={},
            surface_dominant=None,
            sac_scale=None,
            sac_max=None,
            graded_share=0.0,
            mtb_rideable=None,
            mtb_scale=None,
            bike_blocked_m=0.0,
            off_road_share=off_road_share,
            retrace_share=retrace_share,
        )

    good = assembled(10_000, off_road_share=0.9, retrace_share=0.05)
    bad = assembled(6_000, off_road_share=0.2, retrace_share=0.6)

    assert score(good, 10_000) > score(bad, 10_000)
    assert 0.0 <= score(bad, 10_000) <= 1.0


def test_assemble_carries_the_exigent_twin_beside_the_character():
    # "A T2 walk with a T4 move in it": character T2, exigent T4, both true.
    edges = [
        edge(1, length_m=9_600, sac="mountain_hiking"),
        edge(2, length_m=30, sac="alpine_hiking"),
        edge(3, length_m=370, sac=None),
    ]

    result = assemble(edges)

    assert result.sac_scale == "mountain_hiking"  # character (>=5%)
    assert result.sac_max == "alpine_hiking"  # exigent (any length)
    assert result.graded_share == pytest.approx(9_630 / 10_000)


def test_junk_grades_do_not_become_the_exigent_grade():
    result = assemble([edge(1, length_m=100, sac="a sentence about ruins")])

    assert result.sac_max is None
    assert result.graded_share == 0.0


# ── Loop shapes and dedupe ───────────────────────────────────────────────────


def test_ring_points_are_deterministic_and_rotate_with_the_seed():
    a = ring_points((9.35, 45.9), 10_000, seed=0)
    b = ring_points((9.35, 45.9), 10_000, seed=0)
    c = ring_points((9.35, 45.9), 10_000, seed=3)

    assert a == b  # same ask, same loop: the id depends on this
    assert a != c
    assert len(a) == 2


def test_ring_radius_scales_with_target():
    import math

    near = ring_points((9.35, 45.9), 5_000, seed=0)[0]
    far = ring_points((9.35, 45.9), 15_000, seed=0)[0]

    def dist(p):
        return math.hypot((p.lon - 9.35) * 78_000, (p.lat - 45.9) * 111_320)

    assert dist(far) == pytest.approx(3 * dist(near), rel=0.01)
    assert dist(near) == pytest.approx(5_000 / 5.0, rel=0.01)


def test_keep_distinct_drops_the_same_route_asked_twice():
    candidates = [
        {"score": 0.9, "edge_ids": {1, 2, 3, 4}},
        {"score": 0.8, "edge_ids": {1, 2, 3, 5}},  # 60%+ shared with the first
        {"score": 0.7, "edge_ids": {10, 11, 12}},
    ]

    kept = keep_distinct(candidates, max_keep=3)

    assert [c["score"] for c in kept] == [0.9, 0.7]


def test_keep_distinct_respects_the_cap_and_order():
    candidates = [
        {"score": s, "edge_ids": {i * 10, i * 10 + 1}}
        for i, s in enumerate([0.5, 0.9, 0.7])
    ]

    kept = keep_distinct(candidates, max_keep=2)

    assert [c["score"] for c in kept] == [0.9, 0.7]


def test_edge_jaccard_edges():
    assert edge_jaccard({1, 2}, {1, 2}) == 1.0
    assert edge_jaccard({1, 2}, {3, 4}) == 0.0
    assert edge_jaccard(set(), set()) == 1.0
