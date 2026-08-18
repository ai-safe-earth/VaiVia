"""Is the routing graph actually connected? Health check for pathfinding.

Routing can only ever work inside one connected component. A network that looks
healthy by node count can still be shattered into islands, in which case
Dijkstra silently returns nothing and the app reports "no route found" as though
the request were unreasonable.

Found 2026-08-17 on the Lecco region: 15,438 intersections in 1,627 components,
the largest holding only 32% of them, and the Lecco waterfront sitting on an
island of 14. Root cause is the ingestion filter — see docs/fragilities.md #9.

Run from backend/ with Neo4j up and a region ingested:
    uv run python -m scripts.check_graph_connectivity
"""

import asyncio
import logging
from contextlib import suppress
from uuid import uuid4

from neo4j.exceptions import Neo4jError

from core.config import get_settings
from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

TOP_N = 10


async def main() -> None:
    settings = get_settings()
    db = Neo4jClient()
    await db.connect()
    graph_name = f"connectivity_{uuid4().hex[:12]}"
    min_lat, min_lon, max_lat, max_lon = settings.bbox

    try:
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

        totals = await db.run(f"""
            CALL gds.wcc.stream('{graph_name}')
            YIELD componentId
            RETURN count(DISTINCT componentId) AS components
            """)
        components = totals[0]["components"]

        rows = await db.run(f"""
            CALL gds.wcc.stream('{graph_name}')
            YIELD nodeId, componentId
            WITH componentId, count(*) AS size
            RETURN componentId, size ORDER BY size DESC LIMIT {TOP_N}
            """)
        largest = rows[0]["size"] if rows else 0
        share = 100 * largest / nodes if nodes else 0

        print(f"connected components: {components}")
        print(f"largest component:    {largest} ({share:.1f}% of the network)\n")
        print(f"top {TOP_N} components:")
        for row in rows:
            print(f"  {row['size']:>6} intersections")

        edge_types = await db.run("""
            MATCH ()-[c:CONNECTS_TO]->()
            RETURN c.highway_type AS highway_type, count(*) AS n
            ORDER BY n DESC
            """)
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
