"""The pack format round-trips and refuses what a router would trip over."""

from pathlib import Path

import numpy as np
import pytest

from vaivia_routes import pack


def synthetic() -> tuple[dict[str, np.ndarray], dict]:
    """Four vertices in a square with a diagonal, one place at vertex 0.

    0 -- 1
    |  / |
    2 -- 3   edge 1-2 bike-only one way (foot forbidden both ways).
    """
    edges = [(0, 1), (1, 3), (2, 3), (0, 2), (1, 2)]
    n_e = len(edges)
    lon = np.array([9.38, 9.39, 9.38, 9.39])
    lat = np.array([45.85, 45.85, 45.84, 45.84])
    geoms = [
        np.array([[lon[u], lat[u]], [lon[v], lat[v]]], dtype=np.float64)
        for u, v in edges
    ]
    geoms[4] = np.insert(geoms[4], 1, [9.385, 45.845], axis=0)  # a bend
    geom_offsets, flat = pack.ragged(geoms)
    ele = np.full(len(flat), 200.0, dtype=np.float32)
    ele[3] = np.nan
    surface, surface_table = pack.encode(["gravel", None, "asphalt", "gravel", "dirt"])
    highway, highway_table = pack.encode(["path"] * n_e)
    empty, empty_table = pack.encode([None] * n_e)
    foot = np.array([100, 120, 100, 80, -1], dtype=np.float32)
    bike = np.array([100, 120, 100, 80, 50], dtype=np.float32)
    bike_rev = np.array([100, 120, 100, 80, -1], dtype=np.float32)
    arrays = {
        "vertex_id": np.array([10, 11, 12, 13], dtype=np.int64),
        "vertex_lon": lon,
        "vertex_lat": lat,
        "vertex_component": np.zeros(4, dtype=np.int64),
        "edge_id": np.arange(n_e, dtype=np.int64) + 100,
        "edge_u": np.array([u for u, _ in edges], dtype=np.int32),
        "edge_v": np.array([v for _, v in edges], dtype=np.int32),
        "edge_length_m": foot.clip(min=1),
        "edge_ascent_m": np.array([10, np.nan, 0, 5, 10], dtype=np.float32),
        "edge_descent_m": np.zeros(n_e, dtype=np.float32),
        "edge_urban_m": np.zeros(n_e, dtype=np.float32),
        "edge_cost_foot": foot,
        "edge_cost_foot_rev": foot,
        "edge_cost_bike": bike,
        "edge_cost_bike_rev": bike_rev,
        "edge_surface": surface,
        "edge_highway": highway,
        "edge_sac_scale": empty,
        "edge_mtb_scale": empty,
        "edge_routable_bike": np.ones(n_e, dtype=bool),
        "geom_offsets": geom_offsets,
        "geom_lon": flat[:, 0].copy(),
        "geom_lat": flat[:, 1].copy(),
        "geom_ele": ele,
        "place_vertex": np.array([0], dtype=np.int32),
        "place_lon": lon[:1],
        "place_lat": lat[:1],
        "place_source": np.array([0], dtype=np.int16),
        "place_kind": np.array([0], dtype=np.int16),
        "place_name": np.array(["Parcheggio Piani d'Erna"]),
        "place_source_id": np.array(["n1"]),
        "place_ele_m": np.array([np.nan], dtype=np.float32),
        "place_distance_m": np.array([4.5], dtype=np.float32),
        "place_is_start": np.array([True]),
        "place_start_class": np.array([0], dtype=np.int16),
        "place_n_trips": np.array([-1], dtype=np.int32),
        "place_service_start": np.array([-1], dtype=np.int32),
        "place_service_end": np.array([-1], dtype=np.int32),
        # format 2: travel and reach (one start place, one kind, no matrices)
        "start_trail_share_5km": np.array([1200.0], dtype=np.float32),
        "potential": np.array([0, 100, 80, 180], dtype=np.float16),
        "drive_row_place": np.empty(0, dtype=np.int32),
        "drive_col_place": np.array([0], dtype=np.int32),
        "drive_min": np.empty(0, dtype=np.float16),
        "rail_row_place": np.empty(0, dtype=np.int32),
        "rail_min": np.empty(0, dtype=np.float16),
    }
    manifest = {
        "run_id": "pack-test0000",
        "counts": {
            "V": 4,
            "E": n_e,
            "P": len(flat),
            "K": 1,
            "Q": 1,
            "D": 0,
            "S": 1,
            "R": 0,
        },
        "codes": {
            "surface": surface_table,
            "highway": highway_table,
            "sac_scale": empty_table,
            "mtb_scale": empty_table,
            "place_source": ["osm"],
            "place_kind": ["parking"],
            "start_class": ["parking"],
        },
    }
    return arrays, manifest


def test_round_trip(tmp_path: Path) -> None:
    arrays, manifest = synthetic()
    pack.write(tmp_path, arrays, manifest)
    p = pack.load(tmp_path)
    assert p.run_id == "pack-test0000"
    assert p.manifest["format"] == pack.FORMAT
    assert set(p.arrays) == set(pack.SPEC)
    for name, a in arrays.items():
        np.testing.assert_array_equal(p[name], a)
    assert p.decode("edge_surface") == ["gravel", None, "asphalt", "gravel", "dirt"]
    assert p.decode("edge_sac_scale") == [None] * 5
    assert p.edge_geometry(4).shape == (3, 2)
    assert p.edge_geometry(0).tolist() == [[9.38, 45.85], [9.39, 45.85]]
    assert p.edge_profile(0).tolist() == [200.0, 200.0]
    assert np.isnan(p.edge_profile(1)[1])  # ele[3] is edge 1's second point
    assert p.decode("place_start_class") == ["parking"]


def test_encode_and_ragged() -> None:
    codes, table = pack.encode(["b", None, "a", "b"])
    assert table == ["a", "b"]
    assert codes.tolist() == [1, -1, 0, 1]
    assert codes.dtype == np.int16
    offsets, flat = pack.ragged([np.array([1.0, 2.0]), np.array([]), np.array([3.0])])
    assert offsets.tolist() == [0, 2, 2, 3]
    assert flat.tolist() == [1.0, 2.0, 3.0]
    offsets, flat = pack.ragged([])
    assert offsets.tolist() == [0] and len(flat) == 0


@pytest.mark.parametrize(
    "break_it, message",
    [
        (lambda a, m: a.pop("edge_u"), "array missing"),
        (lambda a, m: a.__setitem__("extra", np.zeros(1)), "not in the spec"),
        (lambda a, m: a.__setitem__("edge_u", a["edge_u"].astype(np.int64)), "dtype"),
        (lambda a, m: a.__setitem__("edge_u", a["edge_u"][:-1]), "shape"),
        (lambda a, m: a["edge_v"].__setitem__(0, 4), "outside the vertex table"),
        (lambda a, m: a["place_vertex"].__setitem__(0, -1), "outside the vertex"),
        (lambda a, m: a["edge_v"].__setitem__(0, 0), "u == v"),
        (lambda a, m: a["edge_cost_foot"].__setitem__(0, 0.0), "neither -1 nor"),
        (lambda a, m: a["edge_cost_bike_rev"].__setitem__(0, -5.0), "neither -1 nor"),
        (lambda a, m: a["edge_cost_bike"].__setitem__(0, np.nan), "neither -1 nor"),
        (lambda a, m: a["geom_offsets"].__setitem__(1, 1), "fewer than two points"),
        (lambda a, m: a["geom_offsets"].__setitem__(-1, 3), "monotone offsets"),
        (lambda a, m: a["geom_offsets"].__setitem__(2, 0), "monotone offsets"),
        (lambda a, m: a["edge_surface"].__setitem__(0, 7), "outside its table"),
        (lambda a, m: a["vertex_id"].__setitem__(1, 10), "not unique"),
        (lambda a, m: m.__setitem__("format", 1), "format 1"),
        (lambda a, m: m["codes"].pop("surface"), "codes lack 'surface'"),
        (lambda a, m: m["counts"].pop("K"), "counts lack"),
    ],
)
def test_invariants(break_it, message: str) -> None:
    arrays, manifest = synthetic()
    manifest["format"] = pack.FORMAT
    break_it(arrays, manifest)
    with pytest.raises(pack.PackError, match=message):
        pack.validate(arrays, manifest)


def test_fixture_pack_loads() -> None:
    """The committed Lecco cut loads and validates like the real export."""
    p = pack.load(Path(__file__).parent / "fixtures" / "pack-lecco-3km")
    v, e = p.manifest["counts"]["V"], p.manifest["counts"]["E"]
    assert v > 1000 and e > v  # a real network, not a stub
    assert len(p["vertex_id"]) == v and len(p["edge_id"]) == e
    # edges crossing the cut bbox keep their outside endpoint; allow ~2 km slack
    lon, lat = p["vertex_lon"], p["vertex_lat"]
    assert lon.min() >= 9.36 and lon.max() <= 9.44
    assert lat.min() >= 45.82 and lat.max() <= 45.89
    assert not np.isnan(p["geom_ele"]).any()  # height on every point
    assert p["place_is_start"].any()
    assert (p["edge_cost_foot"] != -1).any() and (p["edge_cost_bike"] != -1).any()


def test_write_refuses_a_bad_pack(tmp_path: Path) -> None:
    arrays, manifest = synthetic()
    arrays["edge_u"][0] = 9
    with pytest.raises(pack.PackError):
        pack.write(tmp_path, arrays, manifest)
    assert not (tmp_path / pack.NETWORK).exists()
