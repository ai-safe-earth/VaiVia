"""The drive-time matrix: minutes by car, settlement -> start vertex.

docs/route-design.md decision 3: drive time is pgRouting over OSM car roads
in this database, computed once per build — "an hour's drive from here"
becomes a lookup. The chain here: node staging.car_road into a topology
(ST_Node — geometric, so a rare grade-separated crossing joins; minutes of
error against a minutes-scale answer), cost each arc by class speed with
oneway honoured, then one chunked many-to-many pgr_dijkstraCost from the
city/town/village settlements to every start's nearest road vertex.

Run from pipeline/ (load.roads done):
    uv run python -m curate.drive --dry-run
    uv run python -m curate.drive
"""

from __future__ import annotations

import argparse
import json
import time
import uuid

from core import connect

#: km/h by class where maxspeed is untagged, scaled by REAL_SPEED_FACTOR —
#: posted limits are not journey speeds on valley roads. Product estimates,
#: same posture as core/durations.py. Reference sanity (checked on the first
#: build, 2026-09-12): Lecco -> Piani Resinelli reads ~33 min against a
#: real-world 25-35; recalibrate the factor if a real drive disagrees.
CLASS_KMH = {
    "motorway": 110.0,
    "motorway_link": 60.0,
    "trunk": 90.0,
    "trunk_link": 50.0,
    "primary": 70.0,
    "primary_link": 40.0,
    "secondary": 60.0,
    "secondary_link": 40.0,
    "tertiary": 50.0,
    "tertiary_link": 35.0,
    "unclassified": 40.0,
    "residential": 30.0,
    "living_street": 10.0,
}
REAL_SPEED_FACTOR = 0.75

#: Origins: city/town/village only. A hamlet anchors to its nearest village;
#: 2,000 near-duplicate origin rows would double the pack for no better
#: answer (0010_drive_rail.sql).
SETTLEMENT_KINDS = ("city", "town", "village")

#: How far a settlement or start may sit from the nearest car road and still
#: get a row. Beyond it, the honest matrix entry is NO entry — a car does
#: not go there.
SNAP_M = 2000.0

NODE_AND_COST = """
INSERT INTO staging.car_edge (way_id, length_m, cost_s, reverse_cost_s, geom)
SELECT r.way_id,
       ST_Length(seg.geom::geography),
       ST_Length(seg.geom::geography) / (%(factor)s * best.kmh / 3.6),
       CASE
           WHEN r.oneway IN ('yes', '1', 'true') THEN -1
           WHEN r.oneway = '-1' THEN
               ST_Length(seg.geom::geography) / (%(factor)s * best.kmh / 3.6)
           ELSE ST_Length(seg.geom::geography) / (%(factor)s * best.kmh / 3.6)
       END,
       seg.geom
FROM (
    SELECT (ST_Dump(ST_Node(ST_Collect(geom)))).geom AS geom
    FROM staging.car_road
) seg
JOIN LATERAL (
    SELECT cr.way_id, cr.highway, cr.oneway, cr.maxspeed
    FROM staging.car_road cr
    WHERE ST_DWithin(cr.geom, ST_LineInterpolatePoint(seg.geom, 0.5), 1e-6)
    ORDER BY ST_Distance(cr.geom, ST_LineInterpolatePoint(seg.geom, 0.5))
    LIMIT 1
) r ON true
JOIN LATERAL (
    SELECT CASE
        WHEN r.maxspeed ~ '^[0-9]+$' THEN
            greatest(least(r.maxspeed::float, 130.0), 10.0)::float
        ELSE (%(class_kmh)s::jsonb ->> r.highway)::float
    END AS kmh
) best ON true
WHERE ST_GeometryType(seg.geom) = 'ST_LineString'
  AND ST_Length(seg.geom::geography) > 0
"""

# oneway='-1' means digitised AGAINST travel: forward forbidden, reverse open.
FIX_REVERSED = """
UPDATE staging.car_edge e
SET cost_s = -1
FROM staging.car_road r
WHERE r.way_id = e.way_id AND r.oneway = '-1'
"""

TOPOLOGY = "SELECT pgr_createTopology('staging.car_edge', 1e-6, 'geom', 'edge_id')"

SETTLEMENTS = """
SELECT p.source_id, v.id AS vid
FROM source_map.place p
JOIN LATERAL (
    SELECT vp.id
    FROM staging.car_edge_vertices_pgr vp
    WHERE ST_DWithin(vp.the_geom::geography, p.geom::geography, %(snap)s)
    ORDER BY vp.the_geom <-> p.geom
    LIMIT 1
) v ON true
WHERE p.source = 'settlement' AND p.kind = ANY(%(kinds)s)
"""

STARTS = """
SELECT s.vertex_id, v.id AS vid
FROM qa.v_start s
JOIN LATERAL (
    SELECT vp.id
    FROM staging.car_edge_vertices_pgr vp
    WHERE ST_DWithin(vp.the_geom::geography, s.geom::geography, %(snap)s)
    ORDER BY vp.the_geom <-> s.geom
    LIMIT 1
) v ON true
"""

MATRIX = """
SELECT start_vid, end_vid, agg_cost
FROM pgr_dijkstraCost(
    'SELECT edge_id AS id, source, target, cost_s AS cost,
            reverse_cost_s AS reverse_cost FROM staging.car_edge',
    %(sources)s::bigint[], %(targets)s::bigint[], directed := true)
"""

CHUNK = 20


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_id = f"drive-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()

    with connect() as conn:
        roads = conn.execute("SELECT count(*) FROM staging.car_road").fetchone()[0]
        if roads == 0:
            raise SystemExit("staging.car_road is empty — run load.roads first")

        print(f"noding {roads:,} car roads ...")
        conn.execute("TRUNCATE staging.car_edge RESTART IDENTITY")
        conn.execute(
            NODE_AND_COST,
            {"factor": REAL_SPEED_FACTOR, "class_kmh": json.dumps(CLASS_KMH)},
        )
        conn.execute(FIX_REVERSED)
        conn.execute(TOPOLOGY)
        n_edges = conn.execute("SELECT count(*) FROM staging.car_edge").fetchone()[0]
        print(f"  {n_edges:,} noded drive edges, {time.monotonic() - started:.0f}s")

        settlements = conn.execute(
            SETTLEMENTS, {"snap": SNAP_M, "kinds": list(SETTLEMENT_KINDS)}
        ).fetchall()
        starts = conn.execute(STARTS, {"snap": SNAP_M}).fetchall()
        print(f"  {len(settlements)} settlement origins, {len(starts)} start targets")
        if args.dry_run:
            print("--dry-run: matrix not computed, nothing written.")
            return

        conn.execute(
            "INSERT INTO provenance.build_run (run_id, stage, parameters) "
            "VALUES (%s, 'drive', %s)",
            (
                run_id,
                json.dumps(
                    {
                        "builder": "curate.drive",
                        "real_speed_factor": REAL_SPEED_FACTOR,
                        "snap_m": SNAP_M,
                        "settlements": len(settlements),
                        "starts": len(starts),
                    }
                ),
            ),
        )

        # A start vertex can snap to the same road vertex as several others;
        # the matrix is per ROAD vertex and fans back out on write.
        by_settlement_vid: dict[int, list[str]] = {}
        for source_id, vid in settlements:
            by_settlement_vid.setdefault(vid, []).append(source_id)
        by_start_vid: dict[int, list[int]] = {}
        for vertex_id, vid in starts:
            by_start_vid.setdefault(vid, []).append(vertex_id)
        source_vids = sorted(by_settlement_vid)
        target_vids = sorted(by_start_vid)

        conn.execute("TRUNCATE source_map.drive_min")
        written = 0
        for i in range(0, len(source_vids), CHUNK):
            chunk = source_vids[i : i + CHUNK]
            rows = conn.execute(
                MATRIX, {"sources": chunk, "targets": target_vids}
            ).fetchall()
            with (
                conn.cursor() as cur,
                cur.copy(
                    "COPY source_map.drive_min (settlement_source_id, start_vertex,"
                    " minutes, run_id) FROM STDIN"
                ) as copy,
            ):
                for start_vid, end_vid, cost_s in rows:
                    for source_id in by_settlement_vid[start_vid]:
                        for vertex_id in by_start_vid[end_vid]:
                            copy.write_row(
                                (source_id, vertex_id, cost_s / 60.0, run_id)
                            )
                            written += 1
            print(
                f"  matrix {min(i + CHUNK, len(source_vids))}/{len(source_vids)} "
                f"origins, {written:,} pairs, {time.monotonic() - started:.0f}s"
            )

        conn.execute(
            "UPDATE provenance.build_run SET finished_at = now(), counts = %s "
            "WHERE run_id = %s",
            (json.dumps({"pairs": written, "edges": n_edges}), run_id),
        )
    print(f"done: {written:,} drive pairs (run {run_id}) — layer: qa.v_drive_start")


if __name__ == "__main__":
    main()
