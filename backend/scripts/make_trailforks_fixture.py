"""Re-cut fixtures/trailforks_mock.json geometry along real OSM ways.

The original fixture used round-number synthetic coordinates that sit ~100 m
from the nearest real way — past the 20 m matching threshold — so no trail ever
matched a segment and (:Trail)-[:COMPOSED_OF]->(:Segment) was never exercised
offline. This script rewrites only each trail's ``geometry``, tracing a
connected chain of real segments from the ingested graph, so spatial matching
has something true to bite on. All other fields (ids, names, distances,
difficulty, ontology prose) are preserved verbatim — tests pin them.

Deterministic given the same graph: start nodes and tie-breaks are ordered.

Run from ``backend/`` with the stack up and ingested::

    uv run python -m scripts.make_trailforks_fixture

Then re-run ``ingestion.trailforks_ingest --mock`` (or scripts.smoke_graph) and
every trail should match segments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

from graph.neo4j_client import Neo4jClient
from ingestion.spatial_match import COMPATIBLE_HIGHWAYS

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "trailforks_mock.json"

# Per-trail walk length. Kept modest: the matcher scores every segment against
# every polyline vertex pair in pure Python, so polyline size is the cost knob.
TARGET_LENGTH_M = 2_000.0

EDGES = """
MATCH (a:Intersection)-[c:CONNECTS_TO]->(b:Intersection)
RETURN a.osm_node_id AS from_node, b.osm_node_id AS to_node,
       c.osm_way_id AS way_id, c.highway_type AS highway_type,
       c.distance_m AS distance_m
"""

SEGMENT_COORDS = """
MATCH (s:Segment) WHERE s.osm_way_id IN $way_ids
RETURN s.osm_way_id AS way_id,
       [c IN s.coordinates | [c.longitude, c.latitude]] AS lonlat
"""


def walk(
    edges_by_node: dict[str, list[dict]],
    allowed: set[str],
    target_m: float,
    skip_ways: set[str],
) -> list[dict]:
    """Greedy deterministic walk: longest allowed edge first, no repeats."""
    starts = sorted(
        node
        for node, edges in edges_by_node.items()
        if any(e["highway_type"] in allowed for e in edges)
    )
    for start in starts:
        chain: list[dict] = []
        used: set[str] = set()
        visited = {start}
        node = start
        total = 0.0
        while total < target_m:
            options = [
                e
                for e in edges_by_node.get(node, [])
                if e["highway_type"] in allowed
                and e["way_id"] not in used
                and e["way_id"] not in skip_ways
                and e["to_node"] not in visited
            ]
            if not options:
                break
            # Longest edge first covers the target in fewest segments; way id
            # as tie-break keeps the walk deterministic.
            step = max(options, key=lambda e: (e["distance_m"], e["way_id"]))
            chain.append(step)
            used.add(step["way_id"])
            visited.add(step["to_node"])
            node = step["to_node"]
            total += step["distance_m"]
        if total >= target_m:
            return chain
    raise SystemExit(
        f"no {target_m:.0f} m chain over {sorted(allowed)} — is the graph ingested?"
    )


def oriented(
    lonlat: list[list[float]], cursor: list[float] | None
) -> list[list[float]]:
    """Flip a segment's coordinates if its far end is nearer the chain's tip."""
    if cursor is None:
        return lonlat

    def gap(p: list[float]) -> float:
        return (p[0] - cursor[0]) ** 2 + (p[1] - cursor[1]) ** 2

    return lonlat if gap(lonlat[0]) <= gap(lonlat[-1]) else list(reversed(lonlat))


async def main() -> int:
    trails = json.loads(FIXTURE.read_text(encoding="utf-8"))

    async with Neo4jClient() as db:
        rows = await db.run(EDGES)
        edges_by_node: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            edges_by_node[row["from_node"]].append(row)

        used_ways: set[str] = set()
        for trail in trails:
            activity = trail["activity"]
            allowed = COMPATIBLE_HIGHWAYS[activity]
            chain = walk(edges_by_node, allowed, TARGET_LENGTH_M, used_ways)
            way_ids = [e["way_id"] for e in chain]
            used_ways.update(way_ids)  # distinct trails, no shared geometry

            coord_rows = await db.run(SEGMENT_COORDS, way_ids=way_ids)
            coords_by_way = {r["way_id"]: r["lonlat"] for r in coord_rows}

            polyline: list[list[float]] = []
            for way_id in way_ids:
                piece = oriented(
                    coords_by_way[way_id], polyline[-1] if polyline else None
                )
                # Drop the shared junction point between consecutive segments.
                polyline.extend(piece[1:] if polyline else piece)

            trail["geometry"] = {"type": "LineString", "coordinates": polyline}
            total = sum(e["distance_m"] for e in chain)
            print(
                f"{trail['trail_id']} ({activity:<5}): {len(chain)} segments, "
                f"{total:.0f} m, {len(polyline)} points"
            )

    FIXTURE.write_text(json.dumps(trails, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {FIXTURE.relative_to(Path.cwd())}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(asyncio.run(main()))
