"""export.pack shapes PostGIS rows into a pack the shared format accepts."""

from datetime import date

import numpy as np
import pytest
import shapely
from vaivia_routes import pack

from export.pack import (
    EDGE_COLUMNS,
    PLACE_COLUMNS,
    VERTEX_COLUMNS,
    build,
    days,
    geometry_arrays,
)


def wkb(*points: tuple[float, float]) -> bytes:
    return shapely.to_wkb(shapely.LineString(points))


def row(columns: tuple[str, ...], **values) -> tuple:
    """A query row in column order; anything not given is NULL."""
    assert set(values) <= set(columns), set(values) - set(columns)
    return tuple(values.get(c) for c in columns)


EDGES = [
    row(
        EDGE_COLUMNS,
        edge_id=1,
        source=10,
        target=11,
        length_m=120.0,
        ascent_m=5.0,
        descent_m=0.0,
        urban_m=0.0,
        cost_foot=120.0,
        cost_foot_rev=120.0,
        cost_bike=120.0,
        cost_bike_rev=-1.0,
        surface="gravel",
        highway="track",
        routable_bike=True,
        wkb=wkb((9.38, 45.85), (9.381, 45.85)),
        profile_m=[200.0, None],
        regions=["lecco"],
        run_id="topo-1",
    ),
    row(
        EDGE_COLUMNS,
        edge_id=2,
        source=11,
        target=13,
        length_m=80.0,
        cost_foot=80.0,
        cost_foot_rev=80.0,
        cost_bike=-1.0,
        cost_bike_rev=-1.0,
        highway="path",
        sac_scale="T2",
        routable_bike=False,
        wkb=wkb((9.381, 45.85), (9.382, 45.851), (9.383, 45.851)),
        regions=["lecco", "bergamo"],
        run_id="topo-1",
    ),
]
# ordered by vertex_id, as the query is
VERTICES = [
    row(VERTEX_COLUMNS, vertex_id=v, lon=x, lat=y, component_id=0)
    for v, x, y in ((10, 9.38, 45.85), (11, 9.381, 45.85), (13, 9.383, 45.851))
]
PLACES = [
    row(
        PLACE_COLUMNS,
        source="poi",
        source_id="n1",
        kind="parking",
        name="P1",
        vertex_id=10,
        distance_m=3.0,
        is_start=True,
        start_class="parking",
        lon=9.38,
        lat=45.85,
        run_id="places-1",
    ),
    row(
        PLACE_COLUMNS,
        source="gtfs_stop",
        source_id="trenord:S1",
        kind="stop",
        name="Lecco",
        ele_m=214.0,
        vertex_id=13,
        distance_m=40.0,
        is_start=True,
        start_class="station",
        n_trips=120,
        lon=9.383,
        lat=45.851,
        service_start=date(2026, 1, 1),
        service_end=date(2026, 12, 13),
        run_id="places-1",
    ),
]


def test_build_is_a_valid_pack_and_keeps_the_rows() -> None:
    arrays, counts, codes, regions, runs = build(EDGES, VERTICES, PLACES)
    manifest = {"format": 1, "run_id": "t", "counts": counts, "codes": codes}
    pack.validate(arrays, manifest)  # raises on any broken invariant

    assert counts == {"V": 3, "E": 2, "P": 5, "K": 2}
    assert arrays["edge_u"].tolist() == [0, 1] and arrays["edge_v"].tolist() == [1, 2]
    assert arrays["edge_cost_bike_rev"].tolist() == [-1.0, -1.0]
    assert np.isnan(arrays["edge_ascent_m"][1]) and arrays["edge_ascent_m"][0] == 5
    assert codes["surface"] == ["gravel"] and codes["sac_scale"] == ["T2"]
    assert arrays["edge_surface"].tolist() == [0, -1]
    assert arrays["geom_offsets"].tolist() == [0, 2, 5]
    ele = arrays["geom_ele"]
    assert ele[0] == 200.0 and np.isnan(ele[1:]).all()  # a NULL sample, a NULL row
    assert arrays["place_vertex"].tolist() == [0, 2]  # row order is the SQL's
    assert arrays["place_n_trips"].tolist() == [-1, 120]
    assert arrays["place_service_start"].tolist() == [-1, days(date(2026, 1, 1))]
    assert codes["start_class"] == ["parking", "station"]
    assert regions == ["bergamo", "lecco"]
    assert runs == ["places-1", "topo-1"]


def test_empty_store_builds_an_empty_pack() -> None:
    arrays, counts, codes, _, _ = build([], [], [])
    pack.validate(
        arrays, {"format": 1, "run_id": "t", "counts": counts, "codes": codes}
    )
    assert counts == {"V": 0, "E": 0, "P": 0, "K": 0}


def test_helpers() -> None:
    assert days(None) == -1 and days(date(1970, 1, 2)) == 1
    lines = [wkb((1, 2), (3, 4)), wkb((5, 6), (7, 8), (9, 0))]
    offsets, lon, lat, ele = geometry_arrays(lines, [[1.0, 2.0], None])
    assert offsets.tolist() == [0, 2, 5]
    assert lon.tolist() == [1, 3, 5, 7, 9] and lat.tolist() == [2, 4, 6, 8, 0]
    assert ele[:2].tolist() == [1.0, 2.0] and np.isnan(ele[2:]).all()
    with pytest.raises(ValueError, match="2 samples for 3 points"):
        geometry_arrays(lines, [[1.0, 2.0], [1.0, 2.0]])
