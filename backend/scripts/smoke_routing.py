"""Live verification of the routing templates, GDS Dijkstra included.

Picks two intersections a real walk apart (BFS over the ingested routing
graph), then routes between them twice:

1. ``route_between_intersections`` — the wired shortestPath baseline.
2. ``graph_project_routing`` -> ``route_gds_dijkstra`` -> ``graph_drop_routing``
   — the GDS path that has never run against a live GDS instance.

shortestPath minimizes *hops*, Dijkstra minimizes *metres*, so the check is not
equality: Dijkstra's total must be <= the baseline's (it optimizes the thing we
measure) and both must produce a coherent path. The projection is dropped in a
``finally`` so a failed run cannot leak a named graph into the instance.

Run from ``backend/`` with the stack up and ingested::

    uv run python -m scripts.smoke_routing
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from collections import deque

from core.geo import bounds_of, expand_bounds_m
from graph.neo4j_client import Neo4jClient

TARGET_WALK_M = 1_500.0

#: A seed to sample around. Any intersection will do -- pick_pair walks out
#: from whatever it is handed and rejects the tiny islands.
SEED = """
MATCH (i:Intersection)
RETURN i.osm_node_id AS id,
       i.location.latitude AS lat,
       i.location.longitude AS lon
LIMIT 1
"""

#: The edges to BFS over: the seed's neighbourhood, not the whole graph.
#:
#: This used to be an unbounded scan of every CONNECTS_TO. At 84k
#: intersections that now dies on the server's db.transaction.timeout (10s,
#: added by the Phase 1 hardening) -- and a client timeout cannot raise a
#: server ceiling, only lower it. A bounded sample is also closer to what the
#: endpoint does, which projects around its own query.
EDGES = """
MATCH (a:Intersection)-[c:CONNECTS_TO]->(b:Intersection)
WHERE a.location.latitude >= $min_lat AND a.location.latitude <= $max_lat
  AND a.location.longitude >= $min_lon AND a.location.longitude <= $max_lon
RETURN a.osm_node_id AS from_node, b.osm_node_id AS to_node,
       c.distance_m AS distance_m
"""

#: How far around the seed to sample. Wide enough that a 1.5 km walk exists
#: inside it, narrow enough to pull in well under the transaction timeout.
SAMPLE_RADIUS_M = 6_000.0


def pick_pair(rows: list[dict], target_m: float) -> tuple[str, str, float]:
    """Two intersections at least ``target_m`` of walking apart.

    The routing graph has many tiny disconnected islands (dead-end tracks the
    bbox clipped), so a single deterministic start can land in a 30 m component
    and make the whole verification trivially weak. Try starts in order until a
    BFS actually walks the target distance.
    """
    adjacency: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        adjacency.setdefault(row["from_node"], []).append(
            (row["to_node"], row["distance_m"])
        )

    for start in sorted(adjacency):
        best: tuple[str, float] = (start, 0.0)
        seen = {start: 0.0}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            for neighbor, dist in adjacency.get(node, []):
                if neighbor in seen:
                    continue
                walked = seen[node] + dist
                seen[neighbor] = walked
                if walked > best[1]:
                    best = (neighbor, walked)
                if walked < target_m:
                    queue.append(neighbor)
        if best[1] >= target_m:
            return start, best[0], best[1]
    raise SystemExit(f"no component walks {target_m:.0f} m — is the graph ingested?")


def check(label: str, condition: bool, detail: str = "") -> bool:
    suffix = f"  {detail}" if detail else ""
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{suffix}")
    return condition


#: How far around the chosen pair to project. The route between them is a
#: couple of km, so 10 km of margin covers any detour the network forces.
PROJECTION_MARGIN_M = 10_000.0


async def main() -> int:
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)
    ok = True

    async with Neo4jClient() as db:
        seed = (await db.run_read(SEED))[0]
        sample = expand_bounds_m(
            (seed["lat"], seed["lon"], seed["lat"], seed["lon"]), SAMPLE_RADIUS_M
        )
        sample_min_lat, sample_min_lon, sample_max_lat, sample_max_lon = sample
        edges = await db.run_read(
            EDGES,
            min_lat=sample_min_lat,
            min_lon=sample_min_lon,
            max_lat=sample_max_lat,
            max_lon=sample_max_lon,
        )
        print(f"sampled {len(edges)} edges around {seed['id']}")
        start, end, walked = pick_pair(edges, TARGET_WALK_M)
        print(f"route {start} -> {end} (BFS walk {walked:.0f} m)\n")

        baseline_rows = await db.run_named(
            "route_between_intersections",
            start_node=start,
            end_node=end,
            max_distance_m=None,
        )
        ok &= check("shortestPath finds a route", bool(baseline_rows))
        baseline_m = baseline_rows[0]["total_m"] if baseline_rows else float("inf")
        if baseline_rows:
            print(
                f"        baseline: {baseline_m:.0f} m over "
                f"{len(baseline_rows[0]['osm_way_ids'])} edges"
            )

        # Project around the pair, the way the endpoint now does. Projecting
        # settings.default_bbox instead is what made this smoke fail against a
        # working GDS: the BFS picks a pair from the WHOLE graph, and once
        # Bergamo was ingested most of the graph sat outside that one box, so
        # Dijkstra was handed endpoints its in-memory graph had never heard of.
        located = await db.run_named(
            "intersection_locations", osm_node_ids=[start, end]
        )
        found = {row["osm_node_id"]: (row["lat"], row["lon"]) for row in located}
        min_lat, min_lon, max_lat, max_lon = expand_bounds_m(
            bounds_of([found[start], found[end]]), PROJECTION_MARGIN_M
        )

        graph_name = f"routing_smoke_{uuid.uuid4().hex[:8]}"
        try:
            projected = await db.run_named(
                "graph_project_routing",
                graph_name=graph_name,
                min_lat=min_lat,
                min_lon=min_lon,
                max_lat=max_lat,
                max_lon=max_lon,
            )
            row = projected[0]
            ok &= check(
                "projection covers the pair's neighbourhood",
                row["nodes"] > 0 and row["rels"] > 0,
                f"{row['nodes']} nodes, {row['rels']} rels",
            )

            gds_rows = await db.run_named(
                "route_gds_dijkstra",
                graph_name=graph_name,
                start_node=start,
                end_node=end,
            )
            ok &= check("GDS Dijkstra finds a route", bool(gds_rows))
            if gds_rows:
                gds = gds_rows[0]
                # Dijkstra now minimises cost_m (comfort), not distance, so it
                # may deliberately return a LONGER route that avoids roads. The
                # old "GDS beats shortestPath on metres" assertion no longer
                # holds. What must still hold: the reported distance comes from
                # distance_m, and totalCost is >= it, since every penalty >= 1.
                gds_details = await db.run_named(
                    "route_edge_details", node_ids=gds["node_ids"]
                )
                gds_m = sum(d["distance_m"] for d in gds_details)
                ok &= check(
                    "comfort cost >= true distance (penalties never shrink a way)",
                    gds["total_cost"] >= gds_m - 1.0,
                    f"cost {gds['total_cost']:.0f} vs {gds_m:.0f} m",
                )
                off_road = sum(
                    d["distance_m"]
                    for d in gds_details
                    if d["highway_type"] in {"path", "track", "bridleway", "footway"}
                )
                print(
                    f"        comfort route: {gds_m:.0f} m, "
                    f"{off_road / gds_m:.0%} off-road "
                    f"(baseline {baseline_m:.0f} m)"
                )
                ok &= check(
                    "path endpoints are the requested nodes",
                    gds["node_ids"][0] == start and gds["node_ids"][-1] == end,
                )
                ok &= check(
                    "coordinates parallel the node list",
                    len(gds["coordinates"]) == len(gds["node_ids"]),
                )
        finally:
            await db.run_named("graph_drop_routing", graph_name=graph_name)

        leftovers = await db.run(
            "CALL gds.graph.list() YIELD graphName RETURN graphName"
        )
        ok &= check(
            "no projection left behind",
            not any(g["graphName"].startswith("routing_smoke_") for g in leftovers),
        )

    print("\nPASS - GDS routing templates verified live." if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(asyncio.run(main()))
