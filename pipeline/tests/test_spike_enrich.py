"""The spike's enrichment rules. No database, no network — pure functions.

The spike compares providers by enriching every candidate identically, so the
rules doing that enrichment are the part that must not be wrong: a bug here
would score every provider wrong in the same direction and the comparison
would still LOOK consistent.
"""

from __future__ import annotations

import pytest

from spike_providers.common import Candidate, geojson_paths
from spike_providers.enrich import MatchedEdge, combine, followed, mtb_verdict


def edge(
    edge_id: int = 1,
    length_m: float = 100.0,
    surface: str | None = None,
    sac: str | None = None,
    mtb: str | None = None,
    bike: bool = True,
    inside: float = 1.0,
) -> MatchedEdge:
    return MatchedEdge(edge_id, length_m, surface, sac, mtb, bike, True, inside)


def test_a_crossing_edge_is_not_a_followed_edge():
    # A road crossing the route lies briefly inside the corridor; the route's
    # own edges lie almost wholly inside it. The share tells them apart.
    crossing = edge(1, 200, inside=0.12)
    along = edge(2, 200, inside=0.95)

    assert followed([crossing, along]) == [along]


def test_the_follow_threshold_is_inclusive():
    assert followed([edge(1, inside=0.5)]) == [edge(1, inside=0.5)]
    assert followed([edge(1, inside=0.49)]) == []


def test_a_null_share_means_not_followed():
    assert followed([edge(1, inside=None)]) == []  # type: ignore[arg-type]


def test_mtb_one_forbidding_edge_forbids_the_route():
    # The access conjunction from metadata-rules.md, applied to a whole line:
    # 4.9 km of legal singletrack and 100 m of bicycle=no is NOT a bike route.
    edges = [edge(1, 4900, bike=True), edge(2, 100, bike=False)]
    verdict = mtb_verdict(edges)

    assert verdict["rideable"] is False
    assert "100 m" in verdict["reason"]


def test_mtb_grade_uses_the_significant_share_rule():
    # 30 m of S4 on 5 km of S1 is an incident, not the character of the route —
    # the same ≥5% rule as SAC difficulty.
    edges = [edge(1, 5000, mtb="1"), edge(2, 30, mtb="4")]

    assert mtb_verdict(edges)["mtb_scale"] == "1"


def test_mtb_nothing_matched_is_unknown_not_yes():
    verdict = mtb_verdict([])

    assert verdict["rideable"] is None
    assert verdict["reason"] == "no matched ground"


def test_mtb_legal_but_ungraded_is_rideable_with_no_grade():
    verdict = mtb_verdict([edge(1, 1000, bike=True)])

    assert verdict["rideable"] is True
    assert verdict["mtb_scale"] is None


def test_combine_reports_the_share_of_the_line_that_matched():
    # 1 km of line, 400 m of followed edges: matched_share says 40%, and that
    # number is the check on the corridor width — nobody has to trust it.
    edges = [
        edge(1, 250, inside=0.9),
        edge(2, 150, inside=0.8),
        edge(3, 500, inside=0.1),
    ]
    result = combine(1000.0, edges, places=[])

    assert result.matched_edges == 2
    assert result.matched_length_m == 400.0
    assert result.matched_share == pytest.approx(0.4)


def test_combine_share_is_capped_at_one():
    # Parallel carriageways can make matched metres exceed line metres; a share
    # over 100% would read as an error, and 100% is what it means.
    result = combine(100.0, [edge(1, 90), edge(2, 90)], places=[])

    assert result.matched_share == 1.0


def test_combine_difficulty_and_surface_come_from_followed_edges_only():
    followed_edge = edge(1, 1000, surface="gravel", sac="mountain_hiking", inside=0.9)
    crossing = edge(
        2, 1000, surface="asphalt", sac="difficult_alpine_hiking", inside=0.05
    )
    result = combine(1000.0, [followed_edge, crossing], places=[])

    assert result.sac_scale == "mountain_hiking"
    assert result.surface == {"gravel": 1.0}


def test_combine_with_an_empty_line_does_not_divide_by_zero():
    result = combine(0.0, [], places=[])

    assert result.matched_share == 0.0
    assert result.mtb["rideable"] is None


def test_geojson_paths_accepts_both_line_shapes_and_drops_altitude():
    single = geojson_paths(
        {"type": "LineString", "coordinates": [[9.3, 45.9, 612.0], [9.4, 45.95]]}
    )
    multi = geojson_paths(
        {"type": "MultiLineString", "coordinates": [[[9.3, 45.9], [9.4, 45.95]]]}
    )

    assert single == [[(9.3, 45.9), (9.4, 45.95)]]
    assert multi == [[(9.3, 45.9), (9.4, 45.95)]]
    with pytest.raises(ValueError):
        geojson_paths({"type": "Point", "coordinates": [9.3, 45.9]})


def test_candidate_point_count_spans_pieces():
    candidate = Candidate(
        provider="test",
        candidate_id="t-1",
        name=None,
        paths=[[(0.0, 0.0), (1.0, 1.0)], [(2.0, 2.0), (3.0, 3.0), (4.0, 4.0)]],
    )

    assert candidate.points == 5
