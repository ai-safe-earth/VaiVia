"""Live graph smoke test: ingestion must be idempotent.

Ingestion MERGEs on stable ids (`osm_way_id`, `osm_node_id`, Trailforks ids), so
running it twice must not create a second copy of anything. Nothing offline can
prove that -- MERGE semantics only exist in the database -- so this runs both
ingesters twice against a real Neo4j and compares node and relationship counts.

Run from ``backend/`` with the stack up::

    docker compose --env-file .env -f infra/docker-compose.yml up -d neo4j
    uv run python -m scripts.init_schema
    uv run python -m scripts.smoke_graph

The Overpass response is cached under ``fixtures/overpass_cache/``, so only the
first ever run touches the network; both passes here read the same cached
payload, which is what makes the comparison meaningful.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from core.config import get_settings
from graph.neo4j_client import Neo4jClient
from ingestion import osm_ingest, trailforks_ingest

COUNTS = """
MATCH (n)
UNWIND labels(n) AS label
RETURN 'node:' + label AS kind, count(*) AS total
UNION ALL
MATCH ()-[r]->()
RETURN 'rel:' + type(r) AS kind, count(*) AS total
"""


async def snapshot(db: Neo4jClient) -> dict[str, int]:
    rows = await db.run(COUNTS)
    return {row["kind"]: row["total"] for row in rows}


async def ingest_once(bbox: tuple[float, float, float, float]) -> None:
    # use_cache=True: the second pass must feed on identical input, or a
    # difference in counts would say nothing about idempotency.
    await osm_ingest.ingest(bbox, use_cache=True)
    await trailforks_ingest.ingest(trailforks_ingest.load_mock())


def render(before: dict[str, int], after: dict[str, int]) -> tuple[str, bool]:
    lines = [f"{'entity':<28}{'pass 1':>10}{'pass 2':>10}   verdict"]
    lines.append("-" * 62)
    ok = True
    for kind in sorted(set(before) | set(after)):
        first, second = before.get(kind, 0), after.get(kind, 0)
        same = first == second
        ok &= same
        verdict = "same" if same else f"CHANGED (+{second - first})"
        lines.append(f"{kind:<28}{first:>10}{second:>10}   {verdict}")
    return "\n".join(lines), ok


async def run(reset: bool) -> int:
    # The driver logs a multi-line notification per batched write; at this volume
    # it buries the actual result.
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)

    settings = get_settings()
    async with Neo4jClient() as db:
        if reset:
            await db.run("MATCH (n) DETACH DELETE n")
            print("cleared the graph\n")

        print("pass 1: osm_ingest + trailforks_ingest --mock")
        await ingest_once(settings.bbox)
        before = await snapshot(db)

        print("pass 2: the same two ingesters, same inputs")
        await ingest_once(settings.bbox)
        after = await snapshot(db)

    table, ok = render(before, after)
    print("\n" + table)
    if ok:
        print("\nPASS - re-ingestion created nothing new; MERGE keys hold.")
        return 0
    print("\nFAIL - re-ingestion duplicated data; a MERGE key is not stable.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete every node first, so pass 1 starts from an empty graph",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.reset))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
