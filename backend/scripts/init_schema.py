"""Apply graph/schema.cypher and seed every configured region (REGIONS).

Run from backend/:  uv run python -m scripts.init_schema
"""

import asyncio
import logging
from pathlib import Path

from core.config import get_settings
from graph.neo4j_client import Neo4jClient

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "graph" / "schema.cypher"

SEED_REGION = """
MERGE (r:Region {name: $name})
SET r.min_lat = $min_lat, r.min_lon = $min_lon,
    r.max_lat = $max_lat, r.max_lon = $max_lon
"""


async def main() -> None:
    settings = get_settings()
    async with Neo4jClient() as db:
        count = await db.run_cypher_file(SCHEMA_PATH)
        logging.info("applied %d schema statements", count)
        for name, (min_lat, min_lon, max_lat, max_lon) in settings.region_list:
            await db.run(
                SEED_REGION,
                name=name,
                min_lat=min_lat,
                min_lon=min_lon,
                max_lat=max_lat,
                max_lon=max_lon,
            )
            logging.info(
                "seeded region %s (%s, %s, %s, %s)",
                name,
                min_lat,
                min_lon,
                max_lat,
                max_lon,
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
