"""Comfort cost: routing must prefer trails without refusing to use roads."""

import pytest

from core.comfort import (
    DEFAULT_HIGHWAY_PENALTY,
    DEFAULT_SURFACE_PENALTY,
    HIGHWAY_PENALTY,
    comfort_cost_m,
    highway_penalty,
    surface_penalty,
)
from ingestion.overpass_client import WALKABLE_HIGHWAYS


def test_a_path_costs_its_true_length():
    """path is the baseline: the thing the app exists to find is never penalised."""
    assert comfort_cost_m(1000.0, "path", "ground") == 1000.0


@pytest.mark.parametrize(
    ("trail", "road"),
    [("path", "residential"), ("track", "secondary"), ("path", "tertiary")],
)
def test_roads_cost_more_than_trails_of_the_same_length(trail, road):
    assert comfort_cost_m(1000.0, trail, None) < comfort_cost_m(1000.0, road, None)


def test_untagged_surface_is_not_penalised():
    """~38% of paths carry no surface tag and are disproportionately the small
    trails we want. Penalising "unknown" would turn a mapping gap into a
    routing preference against them."""
    assert surface_penalty(None) == DEFAULT_SURFACE_PENALTY
    assert surface_penalty("") == DEFAULT_SURFACE_PENALTY
    assert comfort_cost_m(500.0, "path", None) == 500.0


def test_asphalt_costs_more_than_dirt_on_the_same_kind_of_way():
    assert comfort_cost_m(1000.0, "track", "asphalt") > comfort_cost_m(
        1000.0, "track", "ground"
    )


def test_unknown_values_fall_back_rather_than_exploding():
    assert highway_penalty("teleporter") == DEFAULT_HIGHWAY_PENALTY
    assert highway_penalty(None) == DEFAULT_HIGHWAY_PENALTY
    assert surface_penalty("moondust") == DEFAULT_SURFACE_PENALTY


def test_penalties_stay_finite_so_a_road_link_is_still_routable():
    """A valley whose only connection is a lane must still route. Infinite or
    absurd penalties would reproduce the disconnection we just fixed."""
    for highway, penalty in HIGHWAY_PENALTY.items():
        assert 1.0 <= penalty <= 10.0, highway


def test_every_ingested_highway_type_has_a_penalty():
    """A type we ingest but never price silently lands on the default, which is
    cheaper than a secondary road and would make it attractive."""
    ingested = set(WALKABLE_HIGHWAYS.split("|"))
    assert ingested <= set(HIGHWAY_PENALTY), ingested - set(HIGHWAY_PENALTY)


def test_cost_scales_with_length():
    assert comfort_cost_m(2000.0, "residential", None) == 2 * comfort_cost_m(
        1000.0, "residential", None
    )
