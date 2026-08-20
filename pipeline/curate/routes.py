"""Join route relations onto the network: curated.edge_route.

752 relations have sat in staging since they were loaded, read by nothing. Their
members are OSM way ids and curated.edge.way_id is the same id, so this is the
one piece of metadata that costs a join rather than an algorithm -- and it is
what turns 101,951 anonymous edges into named sentieri.

REPLACE, NEVER MERGE, like build_network: the table is derived from staging and
the current network, and derived data merged over a changed base accumulates
rows the current derivation never produced.

The link describes one build of the network, so it is cleared by
topology/build_network.py and topology/repair.py. Running this against a network
that has since been repaired would describe edges that no longer exist; --check
reports that without changing anything.

Run from pipeline/ (network built, staging loaded):
    uv run python -m curate.routes --dry-run
    uv run python -m curate.routes
    uv run python -m curate.routes --check
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections import defaultdict

from core import connect
from curate.route_links import expand_members

# Relations are loaded route=hiking|foot|mtb|bicycle already (load/osm.py), so
# there is no second filter here -- filtering twice in two files is how the two
# definitions drift apart.
RELATIONS = "SELECT rel_id, tags, members FROM staging.osm_relation ORDER BY rel_id"

PIECES = (
    "SELECT way_id, edge_id, piece_index FROM curated.edge ORDER BY way_id, piece_index"
)

NETWORK_RUNS = "SELECT DISTINCT run_id FROM curated.edge"

LINKED = """
SELECT count(*) AS links,
       count(DISTINCT edge_id) AS edges,
       count(DISTINCT rel_id) AS routes
FROM curated.edge_route
"""

LINKED_KM = """
SELECT coalesce(sum(e.length_m), 0) / 1000
FROM curated.edge e
WHERE EXISTS (SELECT 1 FROM curated.edge_route er WHERE er.edge_id = e.edge_id)
"""

# What the join is for, stated as a number: edges that carry no name of their
# own and now carry a route's.
NAMED = """
SELECT count(DISTINCT er.edge_id)
FROM curated.edge_route er
JOIN curated.edge e ON e.edge_id = er.edge_id
JOIN staging.osm_relation r ON r.rel_id = er.rel_id
WHERE NOT (e.tags ? 'name') AND (r.tags ? 'name' OR r.tags ? 'ref')
"""


def network_runs_of(conn, run_id: str) -> list[str]:
    """The network run ids a given curate run recorded itself as built against."""
    row = conn.execute(
        "SELECT parameters -> 'network_run_id' FROM build_run WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    if not row or row[0] is None:
        return []
    return list(row[0])


def check(conn) -> int:
    """Report whether the stored link still describes the network. 0 = current."""
    (links,) = conn.execute("SELECT count(*) FROM curated.edge_route").fetchone()
    if links == 0:
        print("curated.edge_route is empty - run `python -m curate.routes`")
        return 1

    built_against: set[str] = set()
    for (run_id,) in conn.execute("SELECT DISTINCT run_id FROM curated.edge_route"):
        built_against.update(network_runs_of(conn, run_id))

    network = {r for (r,) in conn.execute(NETWORK_RUNS)}
    unseen = network - built_against
    if unseen:
        print(
            f"STALE: {links:,} links were built against {sorted(built_against)}, "
            f"but curated.edge now holds edges from {sorted(unseen)}.\n"
            "Re-run `python -m curate.routes`."
        )
        return 2

    print(f"current: {links:,} links against network run(s) {sorted(built_against)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="count what would be written, write nothing",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the stored link still describes the current network",
    )
    args = parser.parse_args()

    with connect() as conn:
        if args.check:
            raise SystemExit(check(conn))

        pieces: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for way_id, edge_id, piece_index in conn.execute(PIECES):
            pieces[way_id].append((edge_id, piece_index))
        if not pieces:
            raise SystemExit("curated.edge is empty - build the network first")
        network = sorted(r for (r,) in conn.execute(NETWORK_RUNS))
        print(
            f"network: {sum(len(v) for v in pieces.values()):,} edges over "
            f"{len(pieces):,} ways, run(s) {network}"
        )

        relations = conn.execute(RELATIONS).fetchall()
        print(f"relations: {len(relations):,}")

        links = []
        matched_relations = 0
        skipped_nodes = 0
        skipped_relations = 0
        # Unioned, not summed: a way carrying two routes is one member way.
        matched_ways: set[int] = set()
        missing_ways: set[int] = set()
        for rel_id, _tags, members in relations:
            result = expand_members(rel_id, members, pieces)
            matched_ways |= result.matched_way_ids
            missing_ways |= result.missing_way_ids
            skipped_nodes += result.skipped_nodes
            skipped_relations += result.skipped_relations
            if result.links:
                matched_relations += 1
                links.extend(result.links)

        distinct_members = len(matched_ways | missing_ways)
        print(
            f"member ways: {len(matched_ways):,} of {distinct_members:,} distinct "
            f"are in the network, {len(missing_ways):,} are not "
            "(beyond the region bboxes, or excluded by load/legality.py)"
        )
        if skipped_relations:
            print(
                f"skipped {skipped_relations} nested relation member(s) - "
                "superroute stages, joined on their own (curate/route_links.py)"
            )
        print(
            f"links: {len(links):,} over {len({link.edge_id for link in links}):,} "
            f"edges and {matched_relations:,} of {len(relations):,} relations"
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
                        "builder": "curate.routes",
                        # Which network these edge_ids belong to. --check reads
                        # this back; without it a stale link is undetectable.
                        "network_run_id": network,
                    }
                ),
            ),
        )

        # Replace, not merge: see the module docstring.
        conn.execute("TRUNCATE curated.edge_route")
        with (
            conn.cursor() as cur,
            cur.copy(
                "COPY curated.edge_route (edge_id, rel_id, member_index,"
                " piece_index, role, run_id) FROM STDIN"
            ) as copy,
        ):
            for link in links:
                copy.write_row((*link, run_id))

        counts = dict(
            zip(("links", "edges", "routes"), conn.execute(LINKED).fetchone())
        )
        (km,) = conn.execute(LINKED_KM).fetchone()
        (newly_named,) = conn.execute(NAMED).fetchone()
        counts |= {
            "km": round(km, 1),
            "newly_named_edges": newly_named,
            "relations": len(relations),
            "member_ways_matched": len(matched_ways),
            "member_ways_missing": len(missing_ways),
            "nested_relations_skipped": skipped_relations,
        }
        conn.execute(
            "UPDATE build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (json.dumps(counts), run_id),
        )

        print(
            f"\nwrote {counts['links']:,} links: {counts['edges']:,} edges "
            f"({counts['km']:,} km) across {counts['routes']:,} routes"
        )
        print(f"{newly_named:,} edges with no name of their own now carry a route's")
        print(
            f"run {run_id} - layers: qa.v_route, qa.v_route_edge, qa.v_route_coverage"
        )


if __name__ == "__main__":
    main()
