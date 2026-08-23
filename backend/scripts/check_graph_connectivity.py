"""Is the routing graph actually connected? Health check for pathfinding.

Routing can only ever work inside one connected component. A network that looks
healthy by node count can still be shattered into islands, in which case
Dijkstra silently returns nothing and the app reports "no route found" as though
the request were unreasonable.

Found 2026-08-17 on the Lecco region: 15,438 intersections in 1,627 components,
the largest holding only 32% of them, and the Lecco waterfront sitting on an
island of 14. Root cause is the ingestion filter — see docs/fragilities.md #9.

It reports over the WHOLE ingested graph by default. It used to project
settings.bbox, which is one Lecco-shaped box holding 31,514 of the graph's
84,137 intersections once Bergamo was ingested — so its verdict on whether the
network is fragmented was itself computed over a third of the network
(docs/fragilities.md #16). Pass --bbox to narrow it deliberately.

Run from backend/ with Neo4j up, GDS loaded and a region ingested:
    uv run python -m scripts.check_graph_connectivity
    uv run python -m scripts.check_graph_connectivity --bbox 45.8,9.3,46.0,9.6
"""

import argparse
import asyncio
import logging
from contextlib import suppress
from uuid import uuid4

from neo4j.exceptions import Neo4jError

from graph.extent import BBOX_HELP, describe, projection_bbox
from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

TOP_N = 10

# One stream, not two. This used to call gds.wcc.stream once for the component
# count and again for the top N, doing the whole traversal twice to answer two
# questions about the same result. The collect below is over one row per
# component (348 of them on the two-region graph), never over nodes, so it is
# bounded by a number that stays small however large the network gets.
COMPONENT_SIZES = """
CALL gds.wcc.stream($graph_name)
YIELD nodeId, componentId
WITH componentId, count(*) AS size
ORDER BY size DESC
WITH collect(size) AS sizes
RETURN size(sizes) AS components, sizes[..$top_n] AS top
"""

# Every ingested edge by type. A whole-relationship scan with an aggregate:
# measured 2026-08-23 at ~1.1 s over 200,565 CONNECTS_TO, comfortably inside the
# server's 10 s db.transaction.timeout. Re-measure if the graph grows an order
# of magnitude -- a client timeout can only lower that ceiling, never raise it.
EDGE_TYPES = """
MATCH ()-[c:CONNECTS_TO]->()
RETURN c.highway_type AS highway_type, count(*) AS n
ORDER BY n DESC
"""


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", help=BBOX_HELP)
    args = parser.parse_args()

    db = Neo4jClient()
    await db.connect()
    graph_name = f"connectivity_{uuid4().hex[:12]}"

    try:
        bbox = await projection_bbox(db, args.bbox)
        min_lat, min_lon, max_lat, max_lon = bbox
        print(describe(bbox, args.bbox))

        projected = await db.run_named(
            "graph_project_routing",
            graph_name=graph_name,
            min_lat=min_lat,
            min_lon=min_lon,
            max_lat=max_lat,
            max_lon=max_lon,
        )
        if not projected or not projected[0].get("nodes"):
            print("Projection empty — is the region ingested and GDS loaded?")
            return

        nodes = projected[0]["nodes"]
        print(f"routing graph: {nodes} intersections / {projected[0]['rels']} edges\n")

        # $graph_name as a parameter, not an f-string: the same rule the named
        # templates follow, and GDS takes the graph name as one.
        summary = await db.run(COMPONENT_SIZES, graph_name=graph_name, top_n=TOP_N)
        components = summary[0]["components"]
        top = summary[0]["top"]
        largest = top[0] if top else 0
        share = 100 * largest / nodes if nodes else 0

        print(f"connected components: {components}")
        print(f"largest component:    {largest} ({share:.1f}% of the network)\n")
        print(f"top {TOP_N} components:")
        for size in top:
            print(f"  {size:>6} intersections")

        edge_types = await db.run(EDGE_TYPES)
        print("\ningested edge types:")
        for row in edge_types:
            print(f"  {row['highway_type'] or 'unknown':<12} {row['n']:>7}")

        if share < 80:
            print(
                "\nWARNING: the network is fragmented. Routing between two points "
                "in different components is impossible, and loop construction will "
                "mostly fail. See docs/fragilities.md #9."
            )
    finally:
        with suppress(Neo4jError):
            await db.run_named("graph_drop_routing", graph_name=graph_name)
        await db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
