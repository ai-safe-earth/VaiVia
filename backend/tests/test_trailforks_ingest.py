"""Offline tests for the Trailforks normalizer and the shipped mock fixture."""

import pytest

from ingestion.trailforks_ingest import (
    hazards_by_season,
    hazards_union,
    load_mock,
    trail_polyline,
    trail_row,
)


def test_mock_fixture_loads_and_normalizes():
    rows = [trail_row(raw) for raw in load_mock()]
    assert len(rows) >= 3
    by_id = {r["id"]: r for r in rows}
    assert by_id["tf_001"]["activity"] == "mtb"
    assert by_id["tf_001"]["total_distance_m"] == pytest.approx(12_400)
    assert by_id["tf_001"]["difficulty_level"] == 2


def test_durations_follow_activity():
    by_id = {r["id"]: r for r in (trail_row(raw) for raw in load_mock())}
    assert by_id["tf_001"]["duration_mtb_min"] is not None  # mtb
    assert by_id["tf_001"]["duration_hike_min"] is None
    assert by_id["tf_003"]["duration_hike_min"] is not None  # hike
    assert by_id["tf_003"]["duration_mtb_min"] is None
    mixed = by_id["tf_002"]  # mixed gets both
    assert (
        mixed["duration_hike_min"] is not None and mixed["duration_mtb_min"] is not None
    )


def test_ontology_fields_present_in_fixture():
    for row in (trail_row(raw) for raw in load_mock()):
        assert row["landscape_description"]
        assert isinstance(row["best_seasons"], list) and row["best_seasons"] is not None
        assert isinstance(row["seasonal_hazards"], list)
        assert row["difficulty_level"] in (1, 2, 3, 4)


def test_polyline_converts_geojson_lonlat_to_latlon():
    raw = load_mock()[0]
    lat, lon = trail_polyline(raw)[0]
    assert 44 < lat < 47 and 8 < lon < 11  # Lake Como area, axes not swapped


def test_normalizer_is_idempotent_and_deterministic():
    raw = load_mock()[0]
    assert trail_row(raw) == trail_row(raw)


def test_hazards_are_scoped_per_season():
    row = trail_row(
        {
            "trail_id": "x",
            "name": "X",
            "difficulty": "Difficult",
            "total_distance_km": 10,
            "seasonal_hazards": ["snow", "ice"],
            "hazards_by_season": {"winter": ["snow", "ice"], "spring": ["snow"]},
        }
    )
    assert row["hazards_winter"] == ["snow", "ice"]
    assert row["hazards_spring"] == ["snow"]
    assert row["hazards_summer"] == []  # scoped record: unlisted season is clear
    assert row["seasonal_hazards"] == ["snow", "ice"]  # union kept for display


def test_unscoped_hazards_apply_to_every_season():
    """A hazard we cannot place in time is assumed always possible."""
    scoped = hazards_by_season(
        {"seasonal_hazards": ["rockfall"]}  # no hazards_by_season key
    )
    assert all(
        scoped[f"hazards_{s}"] == ["rockfall"]
        for s in ("spring", "summer", "autumn", "winter")
    )


def test_union_derives_from_scoped_when_flat_list_is_empty():
    union = hazards_union(
        {
            "seasonal_hazards": [],
            "hazards_by_season": {"winter": ["ice"], "spring": ["mud_after_rain"]},
        }
    )
    assert union == ["mud_after_rain", "ice"]  # season order, deduped


def test_bergamo_trails_ship_in_the_fixture():
    by_id = {r["id"]: r for r in (trail_row(raw) for raw in load_mock())}
    assert by_id["tf_004"]["activity"] == "hike"
    assert by_id["tf_005"]["activity"] == "mtb"
    assert by_id["tf_004"]["hazards_winter"] == ["ice"]
    assert by_id["tf_004"]["hazards_summer"] == []
