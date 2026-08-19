"""Map-back: a route polyline -> what the graph knows about what it passes."""

import pytest

from graph.route_context import (
    SAMPLE_SPACING_M,
    pois_along_route,
    sample_polyline,
    summarize_pois,
)

# ~1.1 km of straight line north from the Lecco waterfront.
LINE = [(45.856, 9.393), (45.866, 9.393)]


class FakeDb:
    """Records what the bounding query was asked for, returns a fixed set."""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    async def run_named(self, name, /, **params):
        self.calls.append((name, params))
        return self.rows


def poi(osm_id, lat, lon, poi_type="peak", **extra):
    return {
        "osm_id": osm_id,
        "name": extra.get("name", f"POI {osm_id}"),
        "type": poi_type,
        "lat": lat,
        "lon": lon,
        "description": extra.get("description"),
        "description_source": extra.get("description_source"),
        "description_license": extra.get("description_license"),
        "description_url": extra.get("description_url"),
    }


def test_sampling_closes_gaps_a_long_edge_would_leave():
    """Two vertices 1.1 km apart would leave the middle of the line invisible
    to any radius query, which is the whole reason sampling exists."""
    samples = sample_polyline(LINE, SAMPLE_SPACING_M)
    assert len(samples) > 10
    assert samples[0] == LINE[0]
    assert samples[-1] == LINE[-1]


def test_sampling_leaves_short_and_degenerate_lines_alone():
    assert sample_polyline([], SAMPLE_SPACING_M) == []
    assert sample_polyline([(45.0, 9.0)], SAMPLE_SPACING_M) == [(45.0, 9.0)]
    tiny = [(45.8560, 9.3930), (45.8561, 9.3930)]
    assert sample_polyline(tiny, SAMPLE_SPACING_M) == tiny


@pytest.mark.asyncio
async def test_returns_pois_on_the_line_and_drops_ones_merely_in_the_box():
    """The Cypher radius is padded and index-shaped, so it returns candidates
    that are near a SAMPLE but far from the LINE. Those must be filtered."""
    on_line = poi("a", 45.861, 9.3931)  # ~8 m from the line
    far = poi("b", 45.861, 9.399)  # ~460 m east, still near a sample's radius
    db = FakeDb([on_line, far])

    found = await pois_along_route(db, LINE, radius_m=150)

    assert [p["osm_id"] for p in found] == ["a"]
    assert found[0]["distance_m"] < 20


@pytest.mark.asyncio
async def test_results_are_nearest_first():
    db = FakeDb([poi("far", 45.861, 9.3942), poi("near", 45.861, 9.3931)])
    found = await pois_along_route(db, LINE, radius_m=200)
    assert [p["osm_id"] for p in found] == ["near", "far"]
    assert found[0]["distance_m"] < found[1]["distance_m"]


@pytest.mark.asyncio
async def test_poi_types_filter():
    db = FakeDb([poi("p", 45.861, 9.3931, "peak"), poi("c", 45.8612, 9.3931, "chapel")])
    found = await pois_along_route(db, LINE, radius_m=150, poi_types=["chapel"])
    assert [p["osm_id"] for p in found] == ["c"]


@pytest.mark.asyncio
async def test_bounding_radius_is_padded_beyond_the_requested_one():
    """A POI exactly `radius_m` from the line can sit further than that from the
    nearest sample. Under-padding here silently loses real results."""
    db = FakeDb([])
    await pois_along_route(db, LINE, radius_m=150)
    _, params = db.calls[0]
    assert params["radius_m"] > 150


@pytest.mark.asyncio
async def test_a_degenerate_polyline_asks_the_database_nothing():
    db = FakeDb([poi("a", 45.856, 9.393)])
    assert await pois_along_route(db, [(45.856, 9.393)]) == []
    assert db.calls == []


def test_summary_counts_and_names():
    pois = [
        {"type": "peak", "name": "Grigna", "distance_m": 5.0},
        {"type": "peak", "name": None, "distance_m": 9.0},
        {"type": "chapel", "name": "San Martino", "distance_m": 12.0},
    ]
    summary = summarize_pois(pois)
    assert summary["count"] == 3
    assert summary["by_type"] == {"peak": 2, "chapel": 1}
    assert [n["name"] for n in summary["named"]] == ["Grigna", "San Martino"]


def test_summary_excludes_wikidata_one_liners_from_described():
    """A 27-character category label is not a description, and presenting it as
    one makes a route look richer than it is."""
    pois = [
        {
            "type": "peak",
            "name": "Grigna",
            "description": "La Grigna e la vetta piu alta del gruppo...",
            "description_source": "wikipedia",
            "description_license": "CC-BY-SA-4.0",
            "description_url": "https://it.wikipedia.org/wiki/Grigna",
        },
        {
            "type": "saddle",
            "name": "Passo X",
            "description": "valico alpino",
            "description_source": "wikidata",
            "description_license": "CC0-1.0",
            "description_url": "https://www.wikidata.org/wiki/Q1",
        },
    ]
    summary = summarize_pois(pois)
    assert [d["name"] for d in summary["described"]] == ["Grigna"]
    assert summary["attributions"] == [
        {"url": "https://it.wikipedia.org/wiki/Grigna", "license": "CC-BY-SA-4.0"}
    ]


def test_an_area_poi_is_measured_to_its_shore_not_its_centre():
    """Lago di Como's centroid is 5.1 km out on the water. Measuring to it
    reports every shoreline path as far away, so "a route around the lake" can
    never be answered — which is exactly what happened before boundaries."""
    from graph.route_context import poi_distance_to_route

    # A route hugging the west shore of a north-south lake.
    route = [(45.85, 9.390), (45.87, 9.390), (45.89, 9.390)]
    lake = {
        "lat": 45.87,
        "lon": 9.45,  # centre, ~4.7 km east of the route
        "boundary": [[45.85, 9.391], [45.89, 9.391], [45.89, 9.51], [45.85, 9.51]],
    }
    assert poi_distance_to_route(lake, route) < 150
    # Without the boundary the same lake reads as kilometres away.
    assert poi_distance_to_route({**lake, "boundary": []}, route) > 4000


def test_a_node_poi_still_measures_to_its_point():
    from graph.route_context import poi_distance_to_route

    route = [(45.85, 9.390), (45.89, 9.390)]
    summit = {"lat": 45.87, "lon": 9.3905, "boundary": []}
    assert poi_distance_to_route(summit, route) < 60


@pytest.mark.asyncio
async def test_areas_are_asked_for_separately_and_only_once():
    """The performance-critical shape of the bounding step.

    Widening the per-sample radius by each POI's own extent is the obvious way
    to catch a lake whose centroid sits offshore, and it makes the predicate
    depend on the node being tested, so the point index stops serving it: 1,707
    sample points then scan every POI, 14 s per route. Areas therefore get one
    query for the whole route, and the sampled one keeps a constant radius.
    """
    db = FakeDb([])
    await pois_along_route(db, LINE, radius_m=150)

    names = [name for name, _ in db.calls]
    assert names.count("area_pois_near_point") == 1, "one scan per route, not per point"

    _, sampled = db.calls[names.index("pois_near_points")]
    assert "extent" not in str(sampled), "the sampled radius must stay a constant"

    _, area = db.calls[names.index("area_pois_near_point")]
    # Reach must cover the far corner of the route from the centre it measures
    # from, or a lakeside route at the end of a long line is missed.
    assert area["reach_m"] > 150
