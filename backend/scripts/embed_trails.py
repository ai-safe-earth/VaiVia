"""Embed trail descriptions into the trail_embeddings vector index.

For each (:Trail), the embedding input is the owner-ratified concatenation
(description + landscape_description + difficulty_notes, extended 2026-08-16
with activity/difficulty, best seasons, and the POIs along the way). A sha256
of that input is stored on the node, so re-runs embed only trails whose text
changed — the job is idempotent and cheap to run after every ingestion.

Costs money (OpenAI embeddings API; text-embedding-3-small, fractions of a
cent for the beta data set). Run from ``backend/``::

    uv run python -m scripts.embed_trails
    uv run python -m scripts.embed_trails --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from core.embeddings import Embedder, OpenAIEmbedder, embedding_input, input_sha
from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

BATCH_SIZE = 64  # well under the API's input limits; one call for the beta set

FETCH_TRAILS = """
MATCH (t:Trail)
CALL (t) {
  OPTIONAL MATCH (t)-[:COMPOSED_OF]->(:Segment)-[:PASSES_BY]->(p1:POI)
  WITH t, collect(DISTINCT p1) AS direct
  OPTIONAL MATCH (t)-[:NEAR_POI]->(p2:POI)
  WITH direct, collect(DISTINCT p2) AS near
  WITH direct + near AS all_pois
  UNWIND all_pois AS p
  WITH DISTINCT p
  RETURN collect({name: p.name, type: p.type}) AS pois
}
RETURN t.id AS id,
       t.description AS description,
       t.landscape_description AS landscape_description,
       t.difficulty_notes AS difficulty_notes,
       t.activity AS activity,
       t.difficulty AS difficulty,
       t.best_seasons AS best_seasons,
       pois AS pois,
       t.embedding_input_sha AS embedded_sha
"""

# setNodeVectorProperty stores the typed vector the index expects; a plain
# SET would store a generic list.
WRITE_EMBEDDINGS = """
UNWIND $rows AS row
MATCH (t:Trail {id: row.id})
CALL db.create.setNodeVectorProperty(t, 'description_embedding', row.embedding)
SET t.embedding_input_sha = row.sha
"""


def plan(trails: list[dict]) -> tuple[list[dict], int, int]:
    """Split trails into (to_embed, unchanged, empty). Pure, so testable."""
    to_embed: list[dict] = []
    unchanged = 0
    empty = 0
    for trail in trails:
        text = embedding_input(
            trail.get("description"),
            trail.get("landscape_description"),
            trail.get("difficulty_notes"),
            activity=trail.get("activity"),
            difficulty=trail.get("difficulty"),
            best_seasons=trail.get("best_seasons"),
            pois=trail.get("pois"),
        )
        if not text:
            empty += 1
            continue
        sha = input_sha(text)
        if trail.get("embedded_sha") == sha:
            unchanged += 1
            continue
        to_embed.append({"id": trail["id"], "text": text, "sha": sha})
    return to_embed, unchanged, empty


async def run(embedder: Embedder, dry_run: bool) -> int:
    async with Neo4jClient() as db:
        trails = await db.run(FETCH_TRAILS)
        to_embed, unchanged, empty = plan([dict(t) for t in trails])
        print(
            f"{len(trails)} trails: {len(to_embed)} to embed, "
            f"{unchanged} unchanged, {empty} with no text"
        )
        if dry_run or not to_embed:
            return 0

        for start in range(0, len(to_embed), BATCH_SIZE):
            batch = to_embed[start : start + BATCH_SIZE]
            vectors = await embedder.embed_texts([item["text"] for item in batch])
            rows = [
                {"id": item["id"], "embedding": vector, "sha": item["sha"]}
                for item, vector in zip(batch, vectors, strict=True)
            ]
            await db.run(WRITE_EMBEDDINGS, rows=rows)
            print(f"  embedded {start + len(batch)}/{len(to_embed)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="report the plan without embedding"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(run(OpenAIEmbedder(), args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
