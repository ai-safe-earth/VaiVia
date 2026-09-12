"""Persist a drawn route the moment a user keeps it.

Nothing is written while a user merely looks at cards; a favourite (or a
share, when sharing exists) writes the route DOCUMENT to the store the
geometry endpoints serve from, and one (:Route) to Neo4j — the same node a
catalogue route gets, built by the same vaivia_routes.neo4j_rows mapping,
so every reader downstream treats it identically.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from vaivia_routes.neo4j_rows import document_rows

from graph.neo4j_client import Neo4jClient
from graph.query_loader import parse

logger = logging.getLogger(__name__)

CYPHER = Path(__file__).resolve().parent.parent / "graph" / "save_route.cypher"


def _templates() -> dict[str, str]:
    return parse(CYPHER.read_text(encoding="utf-8"))


async def save_drawn_route(db: Neo4jClient, document: dict, documents_dir: str) -> None:
    """Document to disk first, node second: a (:Route) whose document is
    missing would 503 on every geometry fetch (the routing endpoints verify
    the pair), so the file must exist before the graph says the route does."""
    rows = document_rows(document)
    path = Path(documents_dir) / f"{document['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    templates = _templates()
    saved_at = datetime.datetime.now(datetime.UTC).isoformat()
    await db.run(
        templates["save_route_node"],
        route_id=rows["route"]["route_id"],
        props=rows["route"]["props"],
        saved_at=saved_at,
        run_id=document.get("provenance", {}).get("run_id"),
    )
    if rows["places"]:
        await db.run(templates["save_route_places"], rows=rows["places"])
        await db.run(templates["save_route_passes"], rows=rows["passes"])
    if rows["start"] is not None:
        await db.run(templates["save_route_start"], row=rows["start"])
        await db.run(templates["save_route_starts_at"], **rows["start_link"])
    logger.info("drawn route saved", extra={"route_id": document["id"]})
