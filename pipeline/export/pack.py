"""Export the pack: the routable network as numpy arrays, for the planner.

docs/route-design.md. The catalogue of pre-drawn routes retires; what the
backend loads instead is this — every routable edge with its costs, tags,
geometry and profile, every vertex, every place — and it draws routes over it
at ask time. The format lives in `vaivia_routes.pack` (shared/routes), which
also validates the pack before it is written; this module only pulls the rows
and shapes them.

What is exported is exactly what `catalogue.v_edges_foot` and `v_edges_bike`
route over today, so a route the planner draws over the pack and one the
factory drew with pgr_dijkstra walk the same arcs at the same costs (the
parity test in slice 2 holds them to it). Edges routable by neither activity
stay in PostGIS: the pack is for drawing, and the review bundle is for looking.

Run from pipeline/ (network built, elevation sampled, places snapped):
    uv run python -m export.pack --out packs/
    uv run python -m export.pack --out ../shared/routes/tests/fixtures \\
        --bbox 9.38,45.84,9.42,45.87 --name pack-lecco-3km   # the test fixture
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import shapely
from vaivia_routes import pack

from core import connect
from export.route_documents import SOURCES

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directional costs come from the 0005 views, not recomputed here, so oneway
# stays decided in one place. LEFT JOIN: an edge routable by foot only has no
# bike row, and its bike costs read -1 (no such arc) like a oneway's.
#
# Two kinds of edge are left behind on purpose. A zero-length edge is a noding
# artefact that would break the "-1 or positive" cost rule; a self-loop
# (source = target) is never on a shortest path, so a router cannot use it
# and a pack invariant can refuse it.
EDGES = """
SELECT e.edge_id, e.source, e.target, e.length_m, e.ascent_m, e.descent_m,
       e.urban_m,
       coalesce(f.cost, -1), coalesce(f.reverse_cost, -1),
       coalesce(b.cost, -1), coalesce(b.reverse_cost, -1),
       e.tags ->> 'surface', e.tags ->> 'highway',
       e.tags ->> 'sac_scale', e.tags ->> 'mtb:scale',
       e.routable_bike, ST_AsBinary(e.geom), e.profile_m, e.regions, e.run_id
FROM source_map.edge e
LEFT JOIN catalogue.v_edges_foot f ON f.id = e.edge_id
LEFT JOIN catalogue.v_edges_bike b ON b.id = e.edge_id
WHERE (e.routable_foot OR e.routable_bike)
  AND e.length_m > 0 AND e.source <> e.target
"""
EDGE_COLUMNS = (
    "edge_id",
    "source",
    "target",
    "length_m",
    "ascent_m",
    "descent_m",
    "urban_m",
    "cost_foot",
    "cost_foot_rev",
    "cost_bike",
    "cost_bike_rev",
    "surface",
    "highway",
    "sac_scale",
    "mtb_scale",
    "routable_bike",
    "wkb",
    "profile_m",
    "regions",
    "run_id",
)
BBOX = " AND e.geom && ST_MakeEnvelope(%(x1)s, %(y1)s, %(x2)s, %(y2)s, 4326)"
ORDER = " ORDER BY e.edge_id"

VERTICES = """
SELECT vertex_id, ST_X(geom), ST_Y(geom), component_id
FROM source_map.vertex
WHERE vertex_id = ANY(%(ids)s)
ORDER BY vertex_id
"""
VERTEX_COLUMNS = ("vertex_id", "lon", "lat", "component_id")

# Every place whose vertex is in the pack, starts included: a start is a place
# with is_start, and its GTFS service window rides along so the planner can
# say "no train in March" the way export.terminals.seasons_block does.
PLACES = """
SELECT p.source, p.source_id, p.kind, coalesce(p.name, ''), p.ele_m, p.vertex_id,
       p.distance_m, p.is_start, p.start_class, p.n_trips,
       ST_X(p.geom), ST_Y(p.geom), g.service_start, g.service_end, p.run_id
FROM source_map.place p
LEFT JOIN staging.gtfs_stop g
       ON p.source = 'gtfs_stop' AND p.source_id = g.feed || ':' || g.stop_id
WHERE p.vertex_id = ANY(%(ids)s)
ORDER BY p.source, p.source_id
"""
PLACE_COLUMNS = (
    "source",
    "source_id",
    "kind",
    "name",
    "ele_m",
    "vertex_id",
    "distance_m",
    "is_start",
    "start_class",
    "n_trips",
    "lon",
    "lat",
    "service_start",
    "service_end",
    "run_id",
)

DRIVE = "SELECT settlement_source_id, start_vertex, minutes FROM source_map.drive_min"
RAIL = "SELECT feed_stop_a, feed_stop_b, minutes FROM staging.rail_min"

GAZETTEER = Path(__file__).with_name("gazetteer.json")

EPOCH = date(1970, 1, 1)


def days(d: date | None) -> int:
    """A date as days since the epoch; -1 for none (int32 in the pack)."""
    return -1 if d is None else (d - EPOCH).days


def nan_if_none(values: Sequence[float | None]) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=np.float32)


def geometry_arrays(
    wkbs: Sequence[bytes], profiles: Sequence[list[float | None] | None]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """WKB lines and their stored profiles to (offsets, lon, lat, ele).

    profile_m is one sample per point of geom (0001_baseline.sql); a NULL
    array is an edge never sampled, a NULL entry a point outside the DEM.
    Both read NaN. A length that disagrees with the geometry is refused
    rather than stretched: the elevation stage promised alignment.
    """
    lines = shapely.from_wkb(list(wkbs))
    counts = shapely.get_num_coordinates(lines).astype(np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    coords = shapely.get_coordinates(lines)
    if len(coords) == 0:
        coords = np.empty((0, 2), dtype=np.float64)
    ele = np.full(len(coords), np.nan, dtype=np.float32)
    for i, profile in enumerate(profiles):
        if profile is None:
            continue
        if len(profile) != counts[i]:
            raise ValueError(
                f"edge {i}: profile has {len(profile)} samples for {counts[i]} points"
            )
        ele[offsets[i] : offsets[i + 1]] = nan_if_none(profile)
    return offsets, coords[:, 0].copy(), coords[:, 1].copy(), ele


def columns(rows: list[tuple], names: tuple[str, ...]) -> dict[str, tuple]:
    """Rows to name -> column, without a dict per row."""
    cols = list(zip(*rows)) if rows else [()] * len(names)
    return dict(zip(names, cols))


def travel_arrays(
    a: dict[str, np.ndarray],
    p: dict[str, tuple],
    codes: dict[str, list[str]],
    drive_rows: list[tuple],
    rail_rows: list[tuple],
) -> dict[str, int]:
    """Format 2's matrices from the curated tables, filtered to the places
    this pack carries (a bbox cut keeps only its own rows). Pure; returns
    the D/S/R counts. inf = no route, the honest absence."""
    key_index = {
        (src, sid): i for i, (src, sid) in enumerate(zip(p["source"], p["source_id"]))
    }
    start_places = np.flatnonzero(a["place_is_start"])
    start_vertex_cols: dict[int, list[int]] = {}
    for col, place_i in enumerate(start_places):
        start_vertex_cols.setdefault(int(a["place_vertex"][place_i]), []).append(col)

    vertex_ids = a["vertex_id"]
    drive_by_row: dict[int, dict[int, float]] = {}
    for settlement_sid, start_vertex, minutes in drive_rows:
        row = key_index.get(("settlement", settlement_sid))
        # start_vertex is a source_map vertex_id; the pack indexes vertices
        vi = np.searchsorted(vertex_ids, start_vertex)
        if row is None or vi >= len(vertex_ids) or vertex_ids[vi] != start_vertex:
            continue
        for col in start_vertex_cols.get(int(vi), ()):
            drive_by_row.setdefault(row, {})[col] = minutes
    rows = sorted(drive_by_row)
    d, s = len(rows), len(start_places)
    drive = np.full((d, s), np.inf, dtype=np.float16)
    for r_i, row in enumerate(rows):
        for col, minutes in drive_by_row[row].items():
            drive[r_i, col] = minutes
    a["drive_row_place"] = np.array(rows, dtype=np.int32)
    a["drive_col_place"] = start_places.astype(np.int32)
    a["drive_min"] = drive.reshape(-1)

    stations = sorted(
        i for (src, _sid), i in key_index.items() if src == "gtfs_stop"
    )
    station_col = {i: c for c, i in enumerate(stations)}
    rail = np.full((len(stations), len(stations)), np.inf, dtype=np.float16)
    np.fill_diagonal(rail, 0.0)
    for stop_a, stop_b, minutes in rail_rows:
        ia = key_index.get(("gtfs_stop", stop_a))
        ib = key_index.get(("gtfs_stop", stop_b))
        if ia in station_col and ib in station_col:
            rail[station_col[ia], station_col[ib]] = minutes
    a["rail_row_place"] = np.array(stations, dtype=np.int32)
    a["rail_min"] = rail.reshape(-1)
    return {"Q": len(codes["place_kind"]), "D": d, "S": s, "R": len(stations)}


def reach_arrays(a: dict[str, np.ndarray], codes: dict[str, list[str]]) -> None:
    """The computed reach fields: start_trail_share_5km and the potential
    field per place kind — bounded/multi-source Dijkstra over the pack's own
    foot CSR, so pack and planner agree on every metre by construction."""
    from vaivia_routes.network import Network

    from vaivia_routes.assemble import OFF_ROAD_HIGHWAYS

    counts = {
        "V": len(a["vertex_id"]),
        "E": len(a["edge_id"]),
        "P": len(a["geom_lon"]),
        "K": len(a["place_source_id"]),
    }
    if counts["V"] == 0:
        a["start_trail_share_5km"] = np.full(counts["K"], np.nan, dtype=np.float32)
        a["potential"] = np.empty(0, dtype=np.float16).reshape(-1)
        # Q rows over zero vertices is still Q*0 = 0 entries; validate agrees.
        a["potential"] = np.zeros(
            len(codes["place_kind"]) * 0, dtype=np.float16
        )
        return
    net = Network.build(
        pack.Pack(manifest={"run_id": "tmp", "counts": counts, "codes": codes}, arrays=a),
        "foot",
    )
    highway_table = codes["highway"]
    off_road_code = np.array(
        [i for i, h in enumerate(highway_table) if h in OFF_ROAD_HIGHWAYS]
    )
    edge_off_road = np.isin(a["edge_highway"], off_road_code)
    length = a["edge_length_m"]
    u, v = a["edge_u"], a["edge_v"]

    share = np.full(counts["K"], np.nan, dtype=np.float32)
    start_places = np.flatnonzero(a["place_is_start"])
    by_vertex: dict[int, list[int]] = {}
    for i in start_places:
        by_vertex.setdefault(int(a["place_vertex"][i]), []).append(int(i))
    for vertex, place_rows in by_vertex.items():
        dist = net.field_from(vertex, 5000.0)
        opened = edge_off_road & (
            (dist[u] <= 5000.0) | (dist[v] <= 5000.0)
        )
        metres = float(length[opened].sum())
        for i in place_rows:
            share[i] = metres
    a["start_trail_share_5km"] = share

    from scipy.sparse.csgraph import dijkstra

    kind_table = codes["place_kind"]
    potential = np.full((len(kind_table), counts["V"]), np.inf, dtype=np.float16)
    for code, _kind in enumerate(kind_table):
        sources = np.unique(
            a["place_vertex"][np.flatnonzero(a["place_kind"] == code)]
        )
        if len(sources) == 0:
            continue
        field = dijkstra(net.graph, indices=sources, min_only=True)
        potential[code] = field.astype(np.float16)
    a["potential"] = potential.reshape(-1)


def build(
    edges: list[tuple],
    vertices: list[tuple],
    places: list[tuple],
    drive_rows: list[tuple] = (),
    rail_rows: list[tuple] = (),
) -> tuple:
    """Rows to (arrays, counts, codes, regions, source_runs). Pure."""
    v = columns(vertices, VERTEX_COLUMNS)
    e = columns(edges, EDGE_COLUMNS)
    p = columns(places, PLACE_COLUMNS)

    vertex_id = np.array(v["vertex_id"], dtype=np.int64)
    if len(vertex_id) and (np.diff(vertex_id) <= 0).any():
        raise ValueError("vertices must arrive sorted by vertex_id")

    def index(ids: tuple) -> np.ndarray:
        return np.searchsorted(vertex_id, np.asarray(ids, dtype=np.int64)).astype(
            np.int32
        )

    a: dict[str, np.ndarray] = {
        "vertex_id": vertex_id,
        "vertex_lon": np.array(v["lon"], dtype=np.float64),
        "vertex_lat": np.array(v["lat"], dtype=np.float64),
        "vertex_component": np.array(v["component_id"], dtype=np.int64),
        "edge_id": np.array(e["edge_id"], dtype=np.int64),
        "edge_u": index(e["source"]),
        "edge_v": index(e["target"]),
        "edge_length_m": np.array(e["length_m"], dtype=np.float32),
        "edge_ascent_m": nan_if_none(e["ascent_m"]),
        "edge_descent_m": nan_if_none(e["descent_m"]),
        "edge_urban_m": nan_if_none(e["urban_m"]),
        "edge_cost_foot": np.array(e["cost_foot"], dtype=np.float32),
        "edge_cost_foot_rev": np.array(e["cost_foot_rev"], dtype=np.float32),
        "edge_cost_bike": np.array(e["cost_bike"], dtype=np.float32),
        "edge_cost_bike_rev": np.array(e["cost_bike_rev"], dtype=np.float32),
        "edge_routable_bike": np.array(e["routable_bike"], dtype=bool),
        "place_source_id": np.array(p["source_id"], dtype=str),
        "place_name": np.array(p["name"], dtype=str),
        "place_ele_m": nan_if_none(p["ele_m"]),
        "place_vertex": index(p["vertex_id"]),
        "place_distance_m": np.array(p["distance_m"], dtype=np.float32),
        "place_is_start": np.array(p["is_start"], dtype=bool),
        "place_n_trips": np.array(
            [-1 if n is None else n for n in p["n_trips"]], dtype=np.int32
        ),
        "place_lon": np.array(p["lon"], dtype=np.float64),
        "place_lat": np.array(p["lat"], dtype=np.float64),
        "place_service_start": np.array(
            [days(d) for d in p["service_start"]], dtype=np.int32
        ),
        "place_service_end": np.array(
            [days(d) for d in p["service_end"]], dtype=np.int32
        ),
    }
    codes: dict[str, list[str]] = {}
    for array, table, values in (
        ("edge_surface", "surface", e["surface"]),
        ("edge_highway", "highway", e["highway"]),
        ("edge_sac_scale", "sac_scale", e["sac_scale"]),
        ("edge_mtb_scale", "mtb_scale", e["mtb_scale"]),
        ("place_source", "place_source", p["source"]),
        ("place_kind", "place_kind", p["kind"]),
        ("place_start_class", "start_class", p["start_class"]),
    ):
        a[array], codes[table] = pack.encode(list(values))
    a["geom_offsets"], a["geom_lon"], a["geom_lat"], a["geom_ele"] = geometry_arrays(
        e["wkb"], e["profile_m"]
    )

    reach_arrays(a, codes)
    travel_counts = travel_arrays(a, p, codes, list(drive_rows), list(rail_rows))
    counts = {
        "V": len(vertices),
        "E": len(edges),
        "P": len(a["geom_lon"]),
        "K": len(places),
        **travel_counts,
    }
    regions = sorted({r for rs in e["regions"] for r in rs})
    source_runs = sorted(set(e["run_id"]) | set(p["run_id"]))
    return a, counts, codes, regions, source_runs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "pipeline" / "packs"))
    parser.add_argument("--bbox", help="lon,lat,lon,lat — export this window only")
    parser.add_argument("--name", help="directory name; default pack-<hex8>")
    parser.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = parser.parse_args()

    bbox = [float(x) for x in args.bbox.split(",")] if args.bbox else None
    run_id = args.name or f"pack-{uuid.uuid4().hex[:8]}"
    out = Path(args.out) / run_id

    with connect() as conn:
        if not args.dry_run:
            # A NAMED export (--name) is a rebuild of the same artefact —
            # the ledger keeps one row per name, refreshed, not a collision.
            conn.execute(
                "INSERT INTO provenance.build_run (run_id, stage, parameters) "
                "VALUES (%s, 'export', %s) "
                "ON CONFLICT (run_id) DO UPDATE SET "
                "started_at = now(), finished_at = NULL, "
                "parameters = EXCLUDED.parameters",
                (run_id, json.dumps({"builder": "export.pack", "bbox": bbox})),
            )
        params = dict(zip(("x1", "y1", "x2", "y2"), bbox)) if bbox else None
        edges = conn.execute(EDGES + (BBOX if bbox else "") + ORDER, params).fetchall()
        ids = sorted({e[1] for e in edges} | {e[2] for e in edges})
        vertices = conn.execute(VERTICES, {"ids": ids}).fetchall()
        places = conn.execute(PLACES, {"ids": ids}).fetchall()
        drive_rows = conn.execute(DRIVE).fetchall()
        rail_rows = conn.execute(RAIL).fetchall()

        arrays, counts, codes, regions, source_runs = build(
            edges, vertices, places, drive_rows, rail_rows
        )
        print(
            f"vertices {counts['V']:,}  edges {counts['E']:,}  "
            f"points {counts['P']:,} "
            f"({int(np.isnan(arrays['geom_ele']).sum()):,} without elevation)  "
            f"places {counts['K']:,} ({int(arrays['place_is_start'].sum()):,} starts)"
        )
        if args.dry_run:
            return

        gazetteer = json.loads(GAZETTEER.read_text(encoding="utf-8"))["areas"]
        manifest = {
            "run_id": run_id,
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "bbox": bbox,
            "regions": regions,
            "activities": ["foot", "bike"],
            "counts": counts,
            "codes": codes,
            "sources": SOURCES,
            "source_runs": source_runs,
            "gazetteer": gazetteer,
            # Track time between stations: no transfer penalty is priced in,
            # so the figure is a floor on the journey, said here once.
            "rail_note": "track time from the GTFS timetable; no transfer penalty",
        }
        pack.write(out, arrays, manifest)
        size = (out / pack.NETWORK).stat().st_size
        conn.execute(
            "UPDATE provenance.build_run SET finished_at = now(), counts = %s "
            "WHERE run_id = %s",
            (json.dumps({**counts, "bytes": size}), run_id),
        )
    print(f"wrote {out} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
