"""Measure each edge's metres inside urban fabric: source_map.edge.urban_m.

The union of the residential polygons is built once, SUBDIVIDED (a single
region-sized multipolygon defeats every index — the first cut ran past ten
minutes; subdivided to <=128-vertex pieces with a GiST index it is seconds),
and every edge is measured against the pieces in UTM32 — set statements over
whole tables, which is PostGIS's job. Subdivision PARTITIONS the union, so
summing per-piece intersections is exact, never double-counted. Edges
that touch no urban polygon get 0.0; NULL survives only where this pass has
not run for the current network, so a reader can tell "measured: none" from
"never measured" (absent is not zero).

Re-run after any network rebuild, like every derived layer:

    uv run python -m curate.urban [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import uuid

from core import connect

FABRIC = """
CREATE TEMP TABLE fabric AS
SELECT ST_Subdivide(ST_Union(ST_Transform(geom, 32632)), 128) AS geom
FROM staging.settlement
WHERE kind = 'residential'
"""

FABRIC_INDEX = "CREATE INDEX ON fabric USING gist (geom)"

MEASURE = """
UPDATE source_map.edge e
SET urban_m = coalesce(
    (SELECT sum(ST_Length(ST_Intersection(ST_Transform(e.geom, 32632), f.geom)))
     FROM fabric f
     WHERE ST_Transform(e.geom, 32632) && f.geom),
    0.0
)
"""

SUMMARY = """
SELECT count(*)                                          AS edges,
       count(*) FILTER (WHERE urban_m > 0)               AS touching,
       round(sum(urban_m)::numeric / 1000, 1)            AS urban_km,
       round(sum(length_m)::numeric / 1000, 1)           AS total_km
FROM source_map.edge
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with connect() as conn:
        (polygons,) = conn.execute(
            "SELECT count(*) FROM staging.settlement WHERE kind = 'residential'"
        ).fetchone()
        print(f"residential polygons: {polygons:,}")
        if args.dry_run:
            print("--dry-run: nothing written")
            return

        run_id = f"curate-urban-{uuid.uuid4().hex[:8]}"
        with conn.transaction():
            conn.execute(FABRIC)
            conn.execute(FABRIC_INDEX)
            conn.execute("ANALYZE fabric")
            conn.execute(MEASURE)
            edges, touching, urban_km, total_km = conn.execute(SUMMARY).fetchone()
            conn.execute(
                "INSERT INTO provenance.build_run"
                " (run_id, stage, parameters, counts, finished_at)"
                " VALUES (%s, 'curate', %s, %s, now())",
                (
                    run_id,
                    json.dumps({"builder": "curate.urban"}),
                    json.dumps(
                        {
                            "edges": edges,
                            "touching_urban": touching,
                            "urban_km": float(urban_km),
                            "total_km": float(total_km),
                        }
                    ),
                ),
            )
        print(
            f"{touching:,} of {edges:,} edges touch urban fabric — "
            f"{urban_km:,} of {total_km:,} km"
        )
        print(f"run {run_id}")


if __name__ == "__main__":
    main()
