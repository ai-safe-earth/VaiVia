"""Build the noded network: staging.osm_way -> curated.vertex + curated.edge.

Split logic is topology/split.py (pure, tested); this module is the plumbing
around it — read ways, count vertex usage, split, assign vertex ids, COPY, and
write connected components with pgr_connectedComponents so "can a route exist
between here and there" becomes a property comparison.

A run REPLACES the network (TRUNCATE + rebuild): edges are derived data with
deterministic provenance (way_id, piece_index), and the lesson from
backend/scripts/build_trailheads.py stands — derived data merged over a changed
base accumulates rows the current derivation never produced.

Run from pipeline/ (staging loaded):
    uv run python -m topology.build_network
"""

from __future__ import annotations

import json
import time
import uuid

from pyproj import Geod
from shapely import wkb as shapely_wkb
from shapely.geometry import LineString, Point

from core import connect
from load.osm import ewkb4326
from topology.split import Coord, split_at_junctions, vertex_usage

GEOD = Geod(ellps="WGS84")

COMPONENTS = """
UPDATE curated.vertex v
SET component_id = c.component
FROM pgr_connectedComponents(
    'SELECT edge_id AS id, source, target, length_m AS cost FROM curated.edge'
) c
WHERE v.vertex_id = c.node
"""


def line_length_m(coords: list[Coord]) -> float:
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return GEOD.line_length(lons, lats)


def main() -> None:
    run_id = f"topology-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()

    with connect() as conn:
        conn.execute(
            "INSERT INTO build_run (run_id, stage, parameters) VALUES (%s, 'topology', %s)",
            (run_id, json.dumps({"builder": "build_network"})),
        )
        rows = conn.execute("""SELECT way_id, ST_AsBinary(geom), tags, routable_foot,
                      routable_bike, regions
               FROM staging.osm_way
               WHERE routable_foot OR routable_bike""").fetchall()
        print(f"ways in: {len(rows):,}")

        ways: list[dict] = []
        for way_id, geom_wkb, tags, foot, bike, regions in rows:
            line = shapely_wkb.loads(bytes(geom_wkb))
            ways.append(
                {
                    "way_id": way_id,
                    "coords": [(x, y) for x, y in line.coords],  # (lon, lat)
                    "tags": tags,
                    "foot": foot,
                    "bike": bike,
                    "regions": regions,
                }
            )

        usage = vertex_usage(w["coords"] for w in ways)
        junctions = {c for c, n in usage.items() if n >= 2}
        print(
            f"vertices: {len(usage):,} distinct, {len(junctions):,} junctions "
            f"({time.monotonic() - started:.0f}s)"
        )

        vertex_ids: dict[Coord, int] = {}
        edges: list[dict] = []
        for way in ways:
            for piece_index, piece in enumerate(
                split_at_junctions(way["coords"], junctions)
            ):
                for endpoint in (piece[0], piece[-1]):
                    vertex_ids.setdefault(endpoint, len(vertex_ids) + 1)
                edges.append(
                    {
                        "way_id": way["way_id"],
                        "piece_index": piece_index,
                        "source": vertex_ids[piece[0]],
                        "target": vertex_ids[piece[-1]],
                        "geom": ewkb4326(LineString(piece).wkb_hex),
                        "length_m": line_length_m(piece),
                        "tags": json.dumps(way["tags"]),
                        "foot": way["foot"],
                        "bike": way["bike"],
                        "regions": way["regions"],
                    }
                )
        print(f"edges: {len(edges):,}, vertices used: {len(vertex_ids):,}")

        # Replace, not merge: see module docstring. curated.edge_route is in the
        # list because it holds edge_ids from the network being replaced —
        # PostgreSQL would refuse the TRUNCATE otherwise, which is the right
        # answer: a route link that survives a rebuild points at edges that no
        # longer exist. Clearing it makes the gap visible; re-run curate.routes.
        conn.execute(
            "TRUNCATE curated.edge_route, curated.place, curated.edge,"
            " curated.vertex RESTART IDENTITY"
        )
        with conn.cursor() as cur:
            with cur.copy("COPY curated.vertex (geom, run_id) FROM STDIN") as copy:
                for coord in vertex_ids:  # insertion order == id order
                    copy.write_row((ewkb4326(Point(coord).wkb_hex), run_id))
            with cur.copy(
                "COPY curated.edge (way_id, piece_index, source, target, geom,"
                " length_m, tags, routable_foot, routable_bike, regions, run_id)"
                " FROM STDIN"
            ) as copy:
                for e in edges:
                    copy.write_row(
                        (
                            e["way_id"],
                            e["piece_index"],
                            e["source"],
                            e["target"],
                            e["geom"],
                            e["length_m"],
                            e["tags"],
                            e["foot"],
                            e["bike"],
                            e["regions"],
                            run_id,
                        )
                    )

        print("computing connected components...")
        conn.execute(COMPONENTS)
        # Every QA detector reads vertex_degree, and 0004 says this is where it
        # is refreshed — but it was not, so a rebuild left the previous
        # network's degrees in place and the detectors then measured a network
        # that no longer existed. Refreshing here, where the claim already was.
        conn.execute("REFRESH MATERIALIZED VIEW curated.vertex_degree")
        comp = conn.execute("""SELECT count(DISTINCT component_id) AS components,
                      (SELECT count(*) FROM curated.vertex v2
                       WHERE v2.component_id = (
                           SELECT component_id FROM curated.vertex
                           GROUP BY component_id ORDER BY count(*) DESC LIMIT 1
                       ))::float / count(*) AS largest_share
               FROM curated.vertex""").fetchone()
        counts = {
            "edges": len(edges),
            "vertices": len(vertex_ids),
            "components": comp[0],
            "largest_component_share": round(comp[1], 4),
        }
        conn.execute(
            "UPDATE build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (json.dumps(counts), run_id),
        )
        print(
            f"components: {comp[0]:,}; largest holds {comp[1]:.1%} of vertices "
            f"(old graph baseline: 171 components / 98.1%)"
        )
        print(f"done in {time.monotonic() - started:.0f}s (run {run_id})")


if __name__ == "__main__":
    main()
