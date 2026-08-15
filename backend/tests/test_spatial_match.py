"""Matcher precision on synthetic fixtures (docs/fragilities.md #1).

Trail runs due north along lon 9.0; candidates sit on it, ~15 m off it
(parallel), and far away.
"""

from ingestion.spatial_match import MatchCandidate, is_compatible, match_trail

TRAIL = [(45.000, 9.0), (45.001, 9.0), (45.002, 9.0), (45.003, 9.0), (45.004, 9.0)]

ON_TRAIL_SOUTH = MatchCandidate(
    osm_way_id="on_south",
    highway_type="path",
    coordinates=[(45.000, 9.0), (45.001, 9.0)],
    location=(45.0005, 9.0),
)
ON_TRAIL_NORTH = MatchCandidate(
    osm_way_id="on_north",
    highway_type="track",
    coordinates=[(45.003, 9.0), (45.004, 9.0)],
    location=(45.0035, 9.0),
)
# ~15.6 m east of the trail (0.0002 deg lon at lat 45)
PARALLEL_15M = MatchCandidate(
    osm_way_id="parallel",
    highway_type="path",
    coordinates=[(45.001, 9.0002), (45.002, 9.0002)],
    location=(45.0015, 9.0002),
)
FAR_AWAY = MatchCandidate(
    osm_way_id="far",
    highway_type="path",
    coordinates=[(45.05, 9.05), (45.06, 9.05)],
    location=(45.055, 9.05),
)
FOOTWAY_ON_TRAIL = MatchCandidate(
    osm_way_id="footway",
    highway_type="footway",
    coordinates=[(45.002, 9.0), (45.003, 9.0)],
    location=(45.0025, 9.0),
)

ALL = [FAR_AWAY, ON_TRAIL_NORTH, FOOTWAY_ON_TRAIL, ON_TRAIL_SOUTH, PARALLEL_15M]


def test_on_trail_matches_far_does_not():
    matches = match_trail(TRAIL, "mtb", [ON_TRAIL_SOUTH, FAR_AWAY], threshold_m=20)
    assert [m.osm_way_id for m in matches] == ["on_south"]
    assert matches[0].match_confidence > 0.9


def test_parallel_within_threshold_still_matches_at_20m():
    # Documented residual risk: 15 m parallel trail matches at the 20 m default…
    assert any(
        m.osm_way_id == "parallel"
        for m in match_trail(TRAIL, "mtb", [PARALLEL_15M], 20)
    )
    # …and a tighter threshold excludes it (operator-tunable via env)
    assert match_trail(TRAIL, "mtb", [PARALLEL_15M], threshold_m=10) == []


def test_activity_compatibility_filters_highway_type():
    assert not is_compatible("footway", "mtb")
    assert is_compatible("footway", "hike")
    matches = match_trail(TRAIL, "mtb", ALL, threshold_m=20)
    assert "footway" not in {m.osm_way_id for m in matches}
    hike_matches = match_trail(TRAIL, "hike", [FOOTWAY_ON_TRAIL], threshold_m=20)
    assert [m.osm_way_id for m in hike_matches] == ["footway"]


def test_seq_orders_along_trail_south_to_north():
    matches = match_trail(TRAIL, "mtb", ALL, threshold_m=20)
    ordered = [m.osm_way_id for m in sorted(matches, key=lambda m: m.seq)]
    assert ordered.index("on_south") < ordered.index("on_north")
    assert [m.seq for m in sorted(matches, key=lambda m: m.seq)] == list(
        range(len(matches))
    )


def test_matcher_is_deterministic():
    a = match_trail(TRAIL, "mtb", ALL, threshold_m=20)
    b = match_trail(TRAIL, "mtb", list(ALL), threshold_m=20)
    assert [vars(m) for m in a] == [vars(m) for m in b]
