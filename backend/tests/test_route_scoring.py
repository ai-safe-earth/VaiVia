"""Scoring and dedup encode taste, so the taste is argued here rather than
buried in a script."""

from graph.route_scoring import (
    RouteCandidate,
    climb_score,
    dedupe,
    jaccard,
    length_score,
    retrace_fraction,
    score_candidate,
    select,
    signature,
)


def line(start_lat: float, n: int = 40, lon: float = 9.4) -> list[tuple[float, float]]:
    return [(start_lat + i * 0.001, lon) for i in range(n)]


def candidate(**kw) -> RouteCandidate:
    base = dict(
        trailhead_id="th1",
        target_m=10_000.0,
        coordinates=line(45.85),
        distance_m=10_000.0,
        off_road_share=0.7,
        retrace=0.05,
        ascent_m=400.0,
    )
    base.update(kw)
    return RouteCandidate(**base)


def test_length_score_peaks_on_target_and_falls_off_symmetrically():
    assert length_score(10_000, 10_000) == 1.0
    assert length_score(11_000, 10_000) == length_score(9_000, 10_000)
    assert length_score(11_000, 10_000) < 1.0


def test_length_score_bottoms_out_rather_than_going_negative():
    """Twice the requested distance is a different outing, not a worse one."""
    assert length_score(20_000, 10_000) == 0.0
    assert length_score(100_000, 10_000) == 0.0


def test_unknown_climb_scores_neutral_not_zero():
    """Our own routing reports no elevation at all. Scoring that as flat would
    rank every local route below every GraphHopper one for a reason that is
    about instrumentation, not about the route."""
    assert climb_score(None, 10_000) == 0.5
    assert climb_score(0.0, 10_000) == 0.0


def test_climb_score_saturates_so_brutal_is_not_rewarded_over_pleasant():
    gentle = climb_score(400, 10_000)  # 40 m/km
    brutal = climb_score(2000, 10_000)  # 200 m/km
    assert gentle == 1.0
    assert brutal == gentle


def test_a_perfect_route_outscores_a_road_slog_of_the_same_length():
    good = score_candidate(candidate(off_road_share=0.9, retrace=0.0))
    road = score_candidate(candidate(off_road_share=0.05, retrace=0.0))
    assert good.score > road.score


def test_an_out_and_back_is_penalised_against_a_true_loop():
    loop = score_candidate(candidate(retrace=0.02))
    there_and_back = score_candidate(candidate(retrace=0.5))
    assert loop.score > there_and_back.score


def test_length_outweighs_every_other_single_factor():
    """Ask for 15 km, get 25 km, and nothing else rescues it."""
    wrong_length = score_candidate(
        candidate(distance_m=20_000, off_road_share=1.0, retrace=0.0, ascent_m=400)
    )
    right_length_mediocre = score_candidate(
        candidate(distance_m=10_000, off_road_share=0.35, retrace=0.35, ascent_m=None)
    )
    assert right_length_mediocre.score > wrong_length.score


def test_signature_is_stable_under_resampling():
    """Two generators sample the same path at different densities; the
    duplicate check must not care."""
    dense = [(45.85 + i * 0.0002, 9.4) for i in range(100)]
    sparse = [(45.85 + i * 0.001, 9.4) for i in range(20)]
    assert jaccard(signature(dense), signature(sparse)) > 0.9


def test_dedupe_keeps_the_best_of_a_cluster_not_the_first():
    weak = score_candidate(candidate(off_road_share=0.1, retrace=0.4))
    strong = score_candidate(candidate(off_road_share=0.95, retrace=0.0))
    kept = dedupe([weak, strong])
    assert len(kept) == 1
    assert kept[0] is strong


def test_dedupe_keeps_genuinely_different_routes():
    here = score_candidate(candidate(coordinates=line(45.85)))
    far_away = score_candidate(candidate(coordinates=line(46.20, lon=9.9)))
    assert len(dedupe([here, far_away])) == 2


def test_select_returns_best_first_and_caps_the_count():
    cands = [
        candidate(coordinates=line(45.85 + i * 0.5, lon=9.4 + i * 0.5))
        for i in range(6)
    ]
    chosen = select(cands, keep=3)
    assert len(chosen) == 3
    assert chosen == sorted(chosen, key=lambda c: -c.score)


def test_nothing_is_silently_filtered_by_scoring():
    """A bad route ranks low; it does not vanish. Dropping inside the scorer
    would hide how thin coverage actually is."""
    awful = score_candidate(candidate(off_road_share=0.0, retrace=0.9))
    assert awful.scores["total"] >= 0.0
    assert len(dedupe([awful])) == 1


def test_retrace_detects_an_out_and_back():
    out = line(45.85, 20)
    there_and_back = out + list(reversed(out))
    assert retrace_fraction(there_and_back) > 0.45
    assert retrace_fraction(out) == 0.0
