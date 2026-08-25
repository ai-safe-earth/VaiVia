"""Topology QA: find the failures, write them as layers, then measure before fixing.

Every detector writes qa.finding rows carrying real geometry, so each rule is a
QGIS layer (filter on `rule`). Nothing is repaired here — repairs are a separate
pass (topology/repair.py) whose tolerance is chosen from the distribution this
module measures, not guessed. That order is deliberate: a snapping tolerance
picked before looking at the near-miss histogram welds real gaps shut and
invents junctions that do not exist.

The detectors:

  gap_dangle_pair   two loose ends within tolerance of each other, not joined.
                    The classic OSM failure: a path drawn in two sessions whose
                    ends never quite met. Both ends stay dangles, and a route
                    that should cross the join cannot.
  gap_dangle_edge   a loose end within tolerance of another edge's interior:
                    an undershoot (stops short of the road) or an overshoot
                    (crosses it and stops). Needs splitting the target edge,
                    not welding two endpoints, so it is a separate rule.
  gap_dangle_junction
                    a loose end within tolerance of a junction that already
                    exists. The largest gap class at 2 m, and the one the other
                    two rules cannot see between them — see the SQL below.
                    Repairs cleanly: the junction is real, so only the dangle
                    moves.
  island            a connected component too small to hold a real route. Not
                    a defect to repair -- often a genuinely isolated fragment
                    at the bbox edge -- but a coverage fact worth seeing, and
                    the failure that once returned 0/10 loops.
  degenerate        edges under a metre, and self-loops. Harmless individually;
                    they break assembly arithmetic if they reach it.
  overlap           two edges sharing more than a point of geometry: the same
                    ground mapped twice, which double-counts length.

`--dry-run` reports counts and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import uuid

from core import connect

# Detectors are (rule, severity, SQL). Each SQL returns (geom, note); the
# runner stamps run_id and rule. Keeping them declarative means a new rule is a
# query plus a row here, and every rule is a QGIS layer by construction.
#
# $tol is the search radius in metres, applied via geography so it is real
# distance rather than degrees.


# The vertical level a way claims, as text for the finding notes -- '0',
# '1+bridge', '-1+tunnel' (topology/levels.py is the Python twin the repair
# guard uses; the two must agree, and the level tests pin the Python side).
# A reviewer seeing levels disagree in a note knows at once why the repair
# refused the weld: it is a grade separation, not a gap.
def _level_sql(alias: str) -> str:
    return (
        f"concat_ws('+', coalesce({alias}.tags->>'layer','0'),"
        f" CASE WHEN coalesce({alias}.tags->>'bridge','no') <> 'no' THEN 'bridge' END,"
        f" CASE WHEN coalesce({alias}.tags->>'tunnel','no') <> 'no' THEN 'tunnel' END)"
    )


def _dangle_level(alias: str) -> str:
    """The level of a dangle's single incident edge."""
    return (
        f"(SELECT {_level_sql('e2')} FROM source_map.edge e2"
        f" WHERE e2.source = {alias}.vertex_id OR e2.target = {alias}.vertex_id"
        f" LIMIT 1)"
    )


GAP_DANGLE_PAIR = """
-- Joined against source_map.vertex_degree directly, NOT through a CTE: a CTE has
-- no indexes, so `FROM dangles a JOIN dangles b ON ST_DWithin(...)` degrades to
-- a nested loop over every pair of loose ends (14,769^2 geography distances,
-- which ran for ten minutes before being killed). Filtering on degree inside
-- the join lets the geography GIST index serve the predicate.
SELECT ST_MakeLine(a.geom, b.geom) AS geom,
       json_build_object(
           'a', a.vertex_id, 'b', b.vertex_id,
           'distance_m', round(ST_Distance(a.geom::geography, b.geom::geography)::numeric, 2),
           'level_a', {level_a}, 'level_b', {level_b}
       )::text AS note
FROM source_map.vertex_degree a
JOIN source_map.vertex_degree b
  ON a.vertex_id < b.vertex_id
 AND b.degree = 1
 AND ST_DWithin(a.geom::geography, b.geom::geography, %(tol)s)
WHERE a.degree = 1
"""

GAP_DANGLE_EDGE = """
SELECT ST_ShortestLine(d.geom, e.geom) AS geom,
       json_build_object(
           'vertex', d.vertex_id, 'edge', e.edge_id,
           'distance_m', round(ST_Distance(d.geom::geography, e.geom::geography)::numeric, 2),
           'level_vertex', {level_d}, 'level_edge', {level_e}
       )::text AS note
FROM source_map.vertex_degree d
JOIN source_map.edge e
  ON ST_DWithin(d.geom::geography, e.geom::geography, %(tol)s)
 AND e.source <> d.vertex_id AND e.target <> d.vertex_id
-- The dangle must be near the edge's INTERIOR: near an endpoint is either the
-- same gap the pair rule already reports, or a legitimate junction.
 AND ST_Distance(d.geom::geography, ST_StartPoint(e.geom)::geography) > %(tol)s
 AND ST_Distance(d.geom::geography, ST_EndPoint(e.geom)::geography) > %(tol)s
WHERE d.degree = 1
"""

GAP_DANGLE_JUNCTION = """
-- A loose end stopping just short of a junction that already exists. Found by
-- reconciling the near-miss histogram against the two rules above: at 2 m they
-- account for 106 of 231 loose ends between them, and this is most of the rest.
-- Neither could see it — the pair rule requires both ends to be dangles, and
-- the edge rule excludes anything near an endpoint to avoid double-reporting
-- the pair case, which excluded near-junction gaps along with it.
--
-- Excludes the pair the two vertices already share an edge with: a genuinely
-- short edge between a dangle and a junction is a stub, not a gap.
SELECT ST_MakeLine(d.geom, j.geom) AS geom,
       json_build_object(
           'vertex', d.vertex_id, 'junction', j.vertex_id,
           'distance_m', round(ST_Distance(d.geom::geography, j.geom::geography)::numeric, 2),
           'level_vertex', {level_d},
           'level_junction', (SELECT string_agg(DISTINCT {level_e2}, ',')
                              FROM source_map.edge e2
                              WHERE e2.source = j.vertex_id OR e2.target = j.vertex_id)
       )::text AS note
FROM source_map.vertex_degree d
JOIN source_map.vertex_degree j
  ON j.degree >= 2
 AND j.vertex_id <> d.vertex_id
 AND ST_DWithin(d.geom::geography, j.geom::geography, %(tol)s)
WHERE d.degree = 1
  AND NOT EXISTS (
      SELECT 1 FROM source_map.edge e
      WHERE (e.source = d.vertex_id AND e.target = j.vertex_id)
         OR (e.target = d.vertex_id AND e.source = j.vertex_id)
  )
"""

ISLAND = """
WITH sizes AS (
    SELECT component_id, count(*) AS n FROM source_map.vertex
    WHERE component_id IS NOT NULL GROUP BY component_id
)
SELECT ST_ConvexHull(ST_Collect(v.geom)) AS geom,
       json_build_object('component_id', s.component_id, 'vertices', s.n)::text AS note
FROM sizes s
JOIN source_map.vertex v ON v.component_id = s.component_id
WHERE s.n < %(min_vertices)s
GROUP BY s.component_id, s.n
"""

DEGENERATE = """
SELECT geom,
       json_build_object(
           'edge_id', edge_id, 'length_m', round(length_m::numeric, 2),
           'self_loop', source = target
       )::text AS note
FROM source_map.edge
WHERE length_m < %(min_length_m)s OR source = target
"""

OVERLAP = """
-- Measured by shared LENGTH, not by dimension. ST_Dimension of an empty
-- geometry returns the dimension of its TYPE, so ST_Dimension(LINESTRING
-- EMPTY) is 1, and testing `= 1` matched every bbox-overlapping pair that does
-- not intersect at all: 51,905 phantoms against 146 real overlaps. Length is
-- also the honest measure of the defect -- the same ground mapped twice
-- matters in proportion to how much ground.
SELECT shared AS geom,
       json_build_object('a', a_id, 'b', b_id, 'shared_m', round(m::numeric, 1))::text
           AS note
FROM (
    SELECT a.edge_id AS a_id, b.edge_id AS b_id,
           ST_Intersection(a.geom, b.geom) AS shared,
           ST_Length(ST_Intersection(a.geom, b.geom)::geography) AS m
    FROM source_map.edge a
    JOIN source_map.edge b
      ON a.edge_id < b.edge_id
     AND a.way_id <> b.way_id
     AND a.geom && b.geom
) t
WHERE m >= %(min_overlap_m)s
"""

DETECTORS: list[tuple[str, str, str]] = [
    (
        "gap_dangle_pair",
        "error",
        GAP_DANGLE_PAIR.format(level_a=_dangle_level("a"), level_b=_dangle_level("b")),
    ),
    (
        "gap_dangle_edge",
        "error",
        GAP_DANGLE_EDGE.format(level_d=_dangle_level("d"), level_e=_level_sql("e")),
    ),
    (
        "gap_dangle_junction",
        "error",
        GAP_DANGLE_JUNCTION.format(
            level_d=_dangle_level("d"), level_e2=_level_sql("e2")
        ),
    ),
    ("island", "info", ISLAND),
    ("degenerate", "warning", DEGENERATE),
    ("overlap", "warning", OVERLAP),
]

# The distribution the tolerance is chosen from: for every loose end, how far
# is the nearest thing it is NOT connected to?
NEAR_MISS_DISTANCES = """
SELECT (
    SELECT round(ST_Distance(d.geom::geography, e.geom::geography)::numeric, 2)
    FROM source_map.edge e
    WHERE e.source <> d.vertex_id AND e.target <> d.vertex_id
      AND ST_DWithin(d.geom::geography, e.geom::geography, %(max_m)s)
    ORDER BY d.geom <-> e.geom
    LIMIT 1
) AS nearest_m
FROM source_map.vertex_degree d
WHERE d.degree = 1
  -- NULL region = the whole network; a name scopes the measurement to the
  -- loose ends whose vertex touches that region's edges. The tolerance was
  -- measured once over both provinces; new-province work re-measures on its
  -- own evidence before any repair (the Bergamo gate).
  AND (%(region)s::text IS NULL OR d.regions @> ARRAY[%(region)s::text])
"""


def measure(tol_m: float, max_m: float, region: str | None = None) -> None:
    """Report the near-miss distribution: what a tolerance would actually catch."""
    with connect() as conn:
        rows = conn.execute(
            NEAR_MISS_DISTANCES, {"max_m": max_m, "region": region}
        ).fetchall()
    values = sorted(float(r[0]) for r in rows if r[0] is not None)
    total = len(rows)
    where = f" in {region}" if region else ""
    print(
        f"loose ends{where}: {total:,}; "
        f"with something within {max_m:.0f} m: {len(values):,}"
    )
    if not values:
        return
    buckets = [0.5, 1, 2, 5, 10, 20, 50, 100]
    print("\n  distance to nearest unconnected edge (cumulative):")
    previous = 0
    for edge in buckets:
        n = sum(1 for v in values if v <= edge)
        bar = "#" * int(50 * (n - previous) / max(len(values), 1))
        print(f"    <= {edge:>5.1f} m  {n:>6,}  {n / total:>5.1%}  {bar}")
        previous = n
    print(
        f"\n  a tolerance of {tol_m:.1f} m would touch "
        f"{sum(1 for v in values if v <= tol_m):,} of {total:,} loose ends "
        f"({sum(1 for v in values if v <= tol_m) / total:.1%})"
    )


def assert_degrees_fresh(conn) -> None:
    """Refuse to detect against a stale vertex_degree.

    Every rule here reads the materialised degree, so a matview left over from
    an earlier network measures a graph that no longer exists — and the repair
    pass then welds vertices chosen from those numbers. It happened once: a
    rebuild did not refresh, and the same network reported 9 dangle pairs before
    and 19 after, with no edge changed between the two. A count mismatch is the
    cheapest possible detection of it.
    """
    vertices, degrees = conn.execute(
        "SELECT (SELECT count(*) FROM source_map.vertex),"
        "       (SELECT count(*) FROM source_map.vertex_degree)"
    ).fetchone()
    if vertices != degrees:
        raise SystemExit(
            f"source_map.vertex_degree is stale ({degrees:,} rows against "
            f"{vertices:,} vertices). Run:\n"
            "  psql -c 'REFRESH MATERIALIZED VIEW source_map.vertex_degree'\n"
            "or rebuild with `python -m topology.build_network`, which refreshes it."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tolerance-m",
        type=float,
        default=2.0,
        help="near-miss search radius; run --measure first and pick from the data",
    )
    parser.add_argument("--min-component-vertices", type=int, default=10)
    parser.add_argument("--min-length-m", type=float, default=1.0)
    parser.add_argument(
        "--min-overlap-m",
        type=float,
        default=1.0,
        help="shared length before two edges count as overlapping",
    )
    parser.add_argument(
        "--measure",
        action="store_true",
        help="report the near-miss distribution and exit, writing nothing",
    )
    parser.add_argument("--max-measure-m", type=float, default=100.0)
    parser.add_argument(
        "--region",
        default=None,
        help="scope --measure to one region's loose ends (e.g. Bergamo)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with connect() as conn:
        assert_degrees_fresh(conn)

    if args.measure:
        measure(args.tolerance_m, args.max_measure_m, args.region)
        return
    if args.region:
        raise SystemExit(
            "--region scopes --measure only; detection and repair "
            "run over the whole network"
        )

    run_id = f"qa-{uuid.uuid4().hex[:8]}"
    params = {
        "tol": args.tolerance_m,
        "min_vertices": args.min_component_vertices,
        "min_length_m": args.min_length_m,
        "min_overlap_m": args.min_overlap_m,
    }

    with connect() as conn:
        if not args.dry_run:
            conn.execute(
                "INSERT INTO provenance.build_run (run_id, stage, parameters) "
                "VALUES (%s, 'topology', %s)",
                (run_id, json.dumps({"detector": "qa", **params})),
            )
        counts: dict[str, int] = {}
        for rule, severity, sql in DETECTORS:
            if args.dry_run:
                n = conn.execute(
                    f"SELECT count(*) FROM ({sql}) AS d", params
                ).fetchone()[0]
            else:
                n = conn.execute(
                    f"""INSERT INTO qa.finding (run_id, rule, severity, geom, note)
                        SELECT %(run_id)s, %(rule)s, %(severity)s, geom, note
                        FROM ({sql}) AS d""",
                    {**params, "run_id": run_id, "rule": rule, "severity": severity},
                ).rowcount
            counts[rule] = n
            print(f"  {rule:<18} {n:>7,}")
        if not args.dry_run:
            conn.execute(
                "UPDATE provenance.build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
                (json.dumps(counts), run_id),
            )
            print(f"\nwrote findings as run {run_id}")
            print(
                "inspect in QGIS: qa.finding, filter on rule (see pipeline/README.md)"
            )
        else:
            print("\n--dry-run: nothing written.")


if __name__ == "__main__":
    main()
