"""REGIONS parsing: the multi-region beta scope is configuration, not code."""

import pytest

from core.config import Settings


def test_default_regions_parse():
    regions = Settings(_env_file=None).region_list
    names = [name for name, _ in regions]
    assert names == ["Lecco", "Bergamo"]
    for _, bbox in regions:
        min_lat, min_lon, max_lat, max_lon = bbox
        assert min_lat < max_lat and min_lon < max_lon


def test_malformed_region_entry_is_rejected():
    bad = Settings(_env_file=None, regions="Lecco:1,2,3")
    with pytest.raises(ValueError, match="REGIONS entries"):
        _ = bad.region_list
