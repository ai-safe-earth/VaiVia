"""Sample the DEM onto the network: vertex elevation, and an edge profile.

225 Copernicus GLO-30 tiles were loaded on 2026-08-19 and read by nothing.
Everything about difficulty needs them, and so does the elevation panel in the
app that currently renders "no profile yet".

Division of labour, as CLAUDE.md sets it out. Sampling is one statement over a
whole table, so PostGIS does it: ST_Value against the tiled raster, **bilinear**
(see sql/0008_elevation.sql for the measurements that settled that, and for why
there is no noise threshold). Turning a profile into ascent and descent is
per-feature branching over missing samples, so Python does it, in
curate/profile.py, where a unit test pins the rule.

REPLACE, NEVER MERGE: a run overwrites every value it computes.

Elevation describes ONE build of the network, like curated.edge_route: the
profile is aligned to `geom` point by point, so an edge whose geometry a repair
moved has a profile describing the old line. topology/repair.py clears it and
says so; `--check` reports staleness without changing anything.

Run from pipeline/ (network built, DEM loaded):
    uv run python -m curate.elevation --dry-run
    uv run python -m curate.elevation
    uv run python -m curate.elevation --check
"""

from __future__ import annotations

import argparse
import json
import uuid

from core import connect
from curate.profile import ascent_descent

# GDAL drivers are disabled by default in PostGIS for good security reasons;
# the DEM loader enables GTiff for its session and so must anything reading the
# raster it wrote.
ENABLE_GDAL = "SET postgis.gdal_enabled_drivers = 'GTiff'"

# One statement, whole table. The LEFT JOIN is load-bearing: an inner join drops
# points with no covering tile, which would silently SHORTEN the profile array
# and leave it misaligned with the geometry it indexes. A missing sample must
# stay in place as NULL.
VERTEX_ELEVATION = """
UPDATE curated.vertex v
SET elevation_m = s.z
FROM (
    SELECT v.vertex_id, ST_Value(d.rast, 1, v.geom, true, 'bilinear') AS z
    FROM curated.vertex v
    LEFT JOIN staging.dem d ON ST_Intersects(d.rast, v.geom)
) s
WHERE s.vertex_id = v.vertex_id
"""

# Same shape for the profile, but ordered: array_agg over the dumped points in
# path order is what makes profile_m[i] the height of geom's i-th point.
EDGE_PROFILE = """
UPDATE curated.edge e
SET profile_m = s.profile
FROM (
    SELECT p.edge_id, array_agg(p.z ORDER BY p.i) AS profile
    FROM (
        SELECT d.edge_id, d.i,
               ST_Value(r.rast, 1, d.p, true, 'bilinear') AS z
        FROM (
            SELECT edge_id,
                   (ST_DumpPoints(geom)).path[1] AS i,
                   (ST_DumpPoints(geom)).geom    AS p
            FROM curated.edge
        ) d
        LEFT JOIN staging.dem r ON ST_Intersects(r.rast, d.p)
    ) p
    GROUP BY p.edge_id
) s
WHERE s.edge_id = e.edge_id
"""

PROFILES = "SELECT edge_id, profile_m FROM curated.edge WHERE profile_m IS NOT NULL"

NETWORK_RUNS = "SELECT DISTINCT run_id FROM curated.edge"

# The profile indexes geom point by point, so a length mismatch means the
# geometry moved under it. Cheap, and the only check that catches a repair that
# forgot to clear.
ALIGNMENT = """
SELECT count(*) FROM curated.edge
WHERE profile_m IS NOT NULL
  AND array_length(profile_m, 1) IS DISTINCT FROM ST_NPoints(geom)
"""

SUMMARY = """
SELECT count(*) FILTER (WHERE profile_m IS NOT NULL)              AS with_profile,
       count(*) FILTER (WHERE ascent_m IS NOT NULL)               AS with_climb,
       round(sum(ascent_m)::numeric, 0)                           AS ascent_m,
       round(sum(descent_m)::numeric, 0)                          AS descent_m,
       round((sum(length_m) FILTER (WHERE ascent_m IS NULL) / 1000)::numeric, 1)
                                                                  AS km_unknown
FROM curated.edge
"""

# The validation that means something. Saddles sit on gentle ground, so the DEM
# should agree with their OSM `ele` tag; peaks are sharp and convex, so a 30 m
# cell reads them low and MUST NOT be used to judge the sample. Both are
# printed, because the contrast is the evidence.
AGAINST_OSM = """
SELECT poi.poi_type,
       count(*),
       round(avg(ST_Value(d.rast, 1, poi.geom, true, 'bilinear') - poi.ele_m)::numeric, 1),
       round(percentile_cont(0.5) WITHIN GROUP (
           ORDER BY abs(ST_Value(d.rast, 1, poi.geom, true, 'bilinear') - poi.ele_m)
       )::numeric, 1)
FROM staging.osm_poi poi
JOIN staging.dem d ON ST_Intersects(d.rast, poi.geom)
WHERE poi.ele_m IS NOT NULL
  AND ST_GeometryType(poi.geom) = 'ST_Point'
  AND poi.poi_type IN ('saddle', 'peak')
GROUP BY 1 ORDER BY 1
"""


def check(conn) -> int:
    """Report whether the stored elevation still describes the network."""
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE profile_m IS NOT NULL), count(*) FROM curated.edge"
    ).fetchone()
    profiled, edges = row
    if profiled == 0:
        print("no edge carries a profile - run `python -m curate.elevation`")
        return 1

    (misaligned,) = conn.execute(ALIGNMENT).fetchone()
    if misaligned:
        print(
            f"STALE: {misaligned:,} edges have a profile whose length does not match "
            "their geometry - the geometry moved after the sample.\n"
            "Re-run `python -m curate.elevation`."
        )
        return 2

    if profiled < edges:
        print(
            f"PARTIAL: {profiled:,} of {edges:,} edges carry a profile "
            f"({edges - profiled:,} sampled since the last run, or outside the DEM)."
        )
        return 3

    print(f"current: {profiled:,} edges profiled, all aligned with their geometry")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report coverage and the validation against OSM, write nothing",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the stored elevation still describes the network",
    )
    args = parser.parse_args()

    with connect() as conn:
        conn.execute(ENABLE_GDAL)
        if args.check:
            raise SystemExit(check(conn))

        (tiles,) = conn.execute("SELECT count(*) FROM staging.dem").fetchone()
        if not tiles:
            raise SystemExit("staging.dem is empty - load the DEM first")
        (edges,) = conn.execute("SELECT count(*) FROM curated.edge").fetchone()
        if not edges:
            raise SystemExit("curated.edge is empty - build the network first")
        network = sorted(r for (r,) in conn.execute(NETWORK_RUNS))
        print(f"DEM: {tiles} tiles.  network: {edges:,} edges, run(s) {network}")

        uncovered = conn.execute("""
            SELECT count(*) FROM curated.vertex v
            WHERE NOT EXISTS (SELECT 1 FROM staging.dem d WHERE ST_Intersects(d.rast, v.geom))
        """).fetchone()[0]
        if uncovered:
            print(
                f"{uncovered:,} vertices lie outside DEM coverage - the edges touching "
                "them get NULL climb, not a partial sum"
            )

        print("\nDEM against the OSM `ele` tag (bilinear):")
        for poi_type, n, bias, abs_err in conn.execute(AGAINST_OSM):
            note = (
                "gentle ground - this is the honest test"
                if poi_type == "saddle"
                else "sharp and convex - a 30 m cell reads it low by design"
            )
            print(
                f"  {poi_type:<8} n={n:<4} mean bias {bias:>7} m   median |err| {abs_err:>5} m   ({note})"
            )

        if args.dry_run:
            print("\n--dry-run: nothing written")
            return

        run_id = f"curate-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO build_run (run_id, stage, parameters) VALUES (%s, 'curate', %s)",
            (
                run_id,
                json.dumps(
                    {
                        "builder": "curate.elevation",
                        "resample": "bilinear",
                        "threshold_m": None,  # measured: there is no noise floor
                        "network_run_id": network,
                    }
                ),
            ),
        )

        print("\nsampling vertices...")
        conn.execute(VERTEX_ELEVATION)
        print("sampling edge profiles (one value per geometry point)...")
        conn.execute(EDGE_PROFILE)

        (misaligned,) = conn.execute(ALIGNMENT).fetchone()
        if misaligned:
            raise SystemExit(
                f"{misaligned:,} profiles do not match their geometry's point count - "
                "refusing to write climb from a misaligned sample"
            )

        print("computing ascent and descent...")
        climbs = []
        for edge_id, profile in conn.execute(PROFILES):
            result = ascent_descent(profile)
            if result is not None:
                climbs.append((edge_id, result[0], result[1]))

        conn.execute("UPDATE curated.edge SET ascent_m = NULL, descent_m = NULL")
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE climb (edge_id bigint PRIMARY KEY,"
                " ascent_m double precision, descent_m double precision)"
                " ON COMMIT DROP"
            )
            with cur.copy(
                "COPY climb (edge_id, ascent_m, descent_m) FROM STDIN"
            ) as copy:
                for row in climbs:
                    copy.write_row(row)
            cur.execute(
                "UPDATE curated.edge e SET ascent_m = c.ascent_m, descent_m = c.descent_m"
                " FROM climb c WHERE c.edge_id = e.edge_id"
            )

        summary = dict(
            zip(
                ("with_profile", "with_climb", "ascent_m", "descent_m", "km_unknown"),
                conn.execute(SUMMARY).fetchone(),
            )
        )
        (vertices,) = conn.execute(
            "SELECT count(*) FROM curated.vertex WHERE elevation_m IS NOT NULL"
        ).fetchone()
        lo, hi = conn.execute(
            "SELECT min(elevation_m), max(elevation_m) FROM curated.vertex"
        ).fetchone()
        summary |= {
            "vertices_with_elevation": vertices,
            "vertices_uncovered": uncovered,
        }

        conn.execute(
            "UPDATE build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (
                json.dumps(
                    {k: float(v) if v is not None else None for k, v in summary.items()}
                ),
                run_id,
            ),
        )

        print(
            f"\nvertices: {vertices:,} with elevation, {lo:.0f}..{hi:.0f} m"
            f"\nedges:    {summary['with_profile']:,} profiled, "
            f"{summary['with_climb']:,} with climb "
            f"({summary['km_unknown']} km left unknown)"
            f"\nclimb:    {summary['ascent_m']:,} m up, {summary['descent_m']:,} m down"
        )
        print(f"run {run_id} - layers: qa.v_elevation, qa.v_route_elevation")


if __name__ == "__main__":
    main()
