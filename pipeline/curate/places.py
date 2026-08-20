"""Snap places to the network: POIs, settlements, transit stops.

Three staged sources, one shape of job — nearest vertex, exact distance, and a
verdict on whether a walk can begin there. Written into curated.place.

Division of labour, as CLAUDE.md sets it out. The snap is one statement per
source over a whole table, so PostGIS does it, through a KNN search against the
planar index (sql/0010 explains why planar and not geography). Deciding what a
snapped feature MEANS is per-feature branching over a product rule, so Python
does it, in curate/anchors.py, where the tests pin it.

Nothing is dropped and no distance is thresholded: `distance_m` is stored and
consumers filter on it. See sql/0010.

REPLACE, NEVER MERGE. Places hold vertex_ids, so they describe ONE build of the
network: build_network and repair both clear them, and `--check` reports
staleness without changing anything.

Run from pipeline/ (network built, staging loaded):
    uv run python -m curate.places --dry-run
    uv run python -m curate.places
    uv run python -m curate.places --check
"""

from __future__ import annotations

import argparse
import json
import uuid

from core import connect
from curate.anchors import poi_verdict, settlement_verdict, stop_verdict

# One template, three sources. `key` is the source's own identity spelled as
# text, so curated.place has a single primary key across all three rather than
# three nullable id columns.
#
# ST_PointOnSurface, not ST_Centroid: a marker must lie INSIDE the feature, and
# the centroid of a C-shaped car park or a curved lake lies outside it. The
# marker is for drawing; every distance is measured from the whole geometry.
SNAP = """
SELECT s.key, s.kind, s.name, s.ele_m, s.n_trips, s.regions,
       ST_PointOnSurface(s.geom) AS geom,
       n.vertex_id, n.distance_m
FROM ({source}) s
CROSS JOIN LATERAL (
    SELECT v.vertex_id,
           ST_Distance(s.geom::geography, v.geom::geography) AS distance_m
    FROM curated.vertex v
    ORDER BY ST_Transform(s.geom, 32632) <-> ST_Transform(v.geom, 32632)
    LIMIT 1
) n
"""

SOURCES = {
    "poi": """
        SELECT osm_type || osm_id AS key, poi_type AS kind, name, ele_m,
               NULL::integer AS n_trips, regions, geom
        FROM staging.osm_poi
    """,
    "settlement": """
        SELECT osm_type || osm_id AS key, kind, name, NULL::double precision AS ele_m,
               NULL::integer AS n_trips, regions, geom
        FROM staging.settlement
    """,
    "gtfs_stop": """
        SELECT feed || ':' || stop_id AS key, 'stop' AS kind, name,
               NULL::double precision AS ele_m, n_trips, regions, geom
        FROM staging.gtfs_stop
    """,
}

NETWORK_RUNS = "SELECT DISTINCT run_id FROM curated.edge"

SUMMARY = """
SELECT source,
       count(*)                                                   AS places,
       count(*) FILTER (WHERE is_start)                           AS starts,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY distance_m)::numeric, 1) AS p50_m,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY distance_m)::numeric, 1) AS p90_m,
       round(max(distance_m)::numeric, 0)                         AS max_m,
       count(*) FILTER (WHERE distance_m > 100)                   AS over_100m
FROM curated.place GROUP BY source ORDER BY 2 DESC
"""


def verdict_for(source: str, kind: str, n_trips: int | None):
    """Dispatch to the rule for this source. One place, so it cannot drift."""
    if source == "poi":
        return poi_verdict(kind)
    if source == "settlement":
        return settlement_verdict(kind)
    return stop_verdict(n_trips)


def check(conn) -> int:
    """Report whether the stored places still describe the network."""
    (places,) = conn.execute("SELECT count(*) FROM curated.place").fetchone()
    if places == 0:
        print("curated.place is empty - run `python -m curate.places`")
        return 1

    built_against: set[str] = set()
    for (run_id,) in conn.execute("SELECT DISTINCT run_id FROM curated.place"):
        row = conn.execute(
            "SELECT parameters -> 'network_run_id' FROM build_run WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        if row and row[0]:
            built_against.update(row[0])

    network = {r for (r,) in conn.execute(NETWORK_RUNS)}
    unseen = network - built_against
    if unseen:
        print(
            f"STALE: {places:,} places were snapped against {sorted(built_against)}, "
            f"but curated.edge now holds edges from {sorted(unseen)}.\n"
            "Re-run `python -m curate.places`."
        )
        return 2

    print(
        f"current: {places:,} places snapped to network run(s) {sorted(built_against)}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written, write nothing",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the stored places still describe the network",
    )
    args = parser.parse_args()

    with connect() as conn:
        if args.check:
            raise SystemExit(check(conn))

        (vertices,) = conn.execute("SELECT count(*) FROM curated.vertex").fetchone()
        if not vertices:
            raise SystemExit("curated.vertex is empty - build the network first")
        network = sorted(r for (r,) in conn.execute(NETWORK_RUNS))
        print(f"network: {vertices:,} vertices, run(s) {network}")

        rows = []
        rejected: dict[str, int] = {}
        for source, select in SOURCES.items():
            snapped = conn.execute(SNAP.format(source=select)).fetchall()
            starts = 0
            for (
                key,
                kind,
                name,
                ele_m,
                n_trips,
                regions,
                geom,
                vertex_id,
                distance_m,
            ) in snapped:
                verdict = verdict_for(source, kind, n_trips)
                if verdict.is_start:
                    starts += 1
                else:
                    rejected[verdict.note] = rejected.get(verdict.note, 0) + 1
                rows.append(
                    (
                        source,
                        key,
                        kind,
                        name,
                        ele_m,
                        vertex_id,
                        distance_m,
                        verdict.is_start,
                        verdict.note,
                        n_trips,
                        regions,
                        geom,
                        None,
                    )
                )
            print(
                f"  {source:<12} {len(snapped):>6,} snapped, {starts:>5,} can start a walk"
            )

        print(f"\ntotal: {len(rows):,} places, {sum(1 for r in rows if r[7]):,} starts")
        print("not a start, by reason:")
        for note, n in sorted(rejected.items(), key=lambda kv: -kv[1])[:8]:
            print(f"  {n:>6,}  {note}")

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
                        "builder": "curate.places",
                        "threshold_m": None,  # deliberate: see sql/0010
                        "network_run_id": network,
                    }
                ),
            ),
        )

        # Replace, not merge: see the module docstring.
        conn.execute("TRUNCATE curated.place")
        with (
            conn.cursor() as cur,
            cur.copy(
                "COPY curated.place (source, source_id, kind, name, ele_m, vertex_id,"
                " distance_m, is_start, start_note, n_trips, regions, geom, run_id)"
                " FROM STDIN"
            ) as copy,
        ):
            for row in rows:
                copy.write_row(row[:-1] + (run_id,))

        print(
            "\n{:<12}{:>8}{:>8}{:>8}{:>8}{:>8}{:>10}".format(
                "source", "places", "starts", "p50 m", "p90 m", "max m", ">100 m"
            )
        )
        for r in conn.execute(SUMMARY):
            print(
                f"{r[0]:<12}{r[1]:>8,}{r[2]:>8,}{r[3]:>8}{r[4]:>8}{r[5]:>8}{r[6]:>10,}"
            )

        counts = {
            "places": len(rows),
            "starts": sum(1 for r in rows if r[7]),
            "start_vertices": conn.execute(
                "SELECT count(DISTINCT vertex_id) FROM curated.place WHERE is_start"
            ).fetchone()[0],
        }
        conn.execute(
            "UPDATE build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (json.dumps(counts), run_id),
        )
        print(
            f"\n{counts['starts']:,} starts on {counts['start_vertices']:,} distinct "
            "vertices — several car parks often share one lane end"
        )
        print(f"run {run_id} - layers: qa.v_place, qa.v_place_link, qa.v_start")


if __name__ == "__main__":
    main()
