"""OSM -> Neo4j ingestion (idempotent: MERGE on stable ids).

Run from backend/:
    uv run python -m ingestion.osm_ingest --bbox 45.8,9.3,46.0,9.6
Omitting --bbox uses DEFAULT_BBOX from .env. Responses are cached on disk, so
re-runs are offline; pass --no-cache to force a fresh Overpass fetch.
"""

import argparse
import asyncio
import logging

from core.config import get_settings
from graph.neo4j_client import Neo4jClient
from ingestion import osm_extract, overpass_client

logger = logging.getLogger(__name__)

MERGE_INTERSECTIONS = """
UNWIND $rows AS row
MERGE (i:Intersection {osm_node_id: row.osm_node_id})
SET i.location = point({latitude: row.lat, longitude: row.lon})
"""

MERGE_SEGMENTS = """
UNWIND $rows AS row
MERGE (s:Segment {osm_way_id: row.osm_way_id})
SET s.osm_parent_way_id = row.osm_parent_way_id,
    s.length_m = row.length_m,
    s.surface = row.surface,
    s.highway_type = row.highway_type,
    s.location = point({latitude: row.mid_lat, longitude: row.mid_lon}),
    s.coordinates = [c IN row.coords | point({latitude: c[0], longitude: c[1]})]
"""

MERGE_POIS = """
UNWIND $rows AS row
MERGE (p:POI {osm_id: row.osm_id})
SET p.name = row.name,
    p.type = row.type,
    p.location = point({latitude: row.lat, longitude: row.lon})
"""

MERGE_CONNECTS_TO = """
UNWIND $rows AS row
MATCH (a:Intersection {osm_node_id: row.from}), (b:Intersection {osm_node_id: row.to})
MERGE (a)-[c:CONNECTS_TO {osm_way_id: row.osm_way_id}]->(b)
SET c.distance_m = row.distance_m,
    c.surface = row.surface,
    c.highway_type = row.highway_type,
    c.elevation_gain_m = row.elevation_gain_m,
    c.elevation_loss_m = row.elevation_loss_m
"""

MERGE_PASSES_BY = """
UNWIND $rows AS row
MATCH (s:Segment {osm_way_id: row.osm_way_id}), (p:POI {osm_id: row.poi_osm_id})
MERGE (s)-[:PASSES_BY]->(p)
"""

MERGE_LOCATED_IN_INTERSECTIONS = """
MATCH (r:Region {name: $region})
UNWIND $rows AS node_id
MATCH (i:Intersection {osm_node_id: node_id})
MERGE (i)-[:LOCATED_IN]->(r)
"""

MERGE_LOCATED_IN_POIS = """
MATCH (r:Region {name: $region})
UNWIND $rows AS poi_id
MATCH (p:POI {osm_id: poi_id})
MERGE (p)-[:LOCATED_IN]->(r)
"""


async def ingest(
    bbox: tuple[float, float, float, float], use_cache: bool = True
) -> None:
    settings = get_settings()
    raw = await overpass_client.fetch(
        overpass_client.build_query(bbox), use_cache=use_cache
    )
    result = osm_extract.extract(raw)
    logger.info(
        "extracted %d intersections, %d segments, %d POIs",
        len(result.intersections),
        len(result.segments),
        len(result.pois),
    )

    intersection_rows = [
        {"osm_node_id": nid, "lat": loc[0], "lon": loc[1]}
        for nid, loc in result.intersections.items()
    ]
    segment_rows = [
        {
            "osm_way_id": s.osm_way_id,
            "osm_parent_way_id": s.osm_parent_way_id,
            "length_m": s.length_m,
            "surface": s.surface,
            "highway_type": s.highway_type,
            "mid_lat": s.location[0],
            "mid_lon": s.location[1],
            "coords": [[lat, lon] for lat, lon in s.coordinates],
        }
        for s in result.segments
    ]
    edge_rows = osm_extract.connects_to_rows(result.segments)
    passes_rows = osm_extract.passes_by_rows(
        result.segments, result.pois, settings.passes_by_threshold_m
    )
    region_rows = osm_extract.located_in_rows(
        result, bbox, settings.default_region_name
    )

    async with Neo4jClient() as db:
        await db.run_batched(MERGE_INTERSECTIONS, intersection_rows)
        await db.run_batched(MERGE_SEGMENTS, segment_rows, batch_size=500)
        await db.run_batched(MERGE_POIS, result.pois)
        await db.run_batched(MERGE_CONNECTS_TO, edge_rows)
        await db.run_batched(MERGE_PASSES_BY, passes_rows)
        await db.run(
            MERGE_LOCATED_IN_INTERSECTIONS,
            region=settings.default_region_name,
            rows=region_rows["intersections"],
        )
        await db.run(
            MERGE_LOCATED_IN_POIS,
            region=settings.default_region_name,
            rows=region_rows["pois"],
        )
    logger.info("OSM ingestion complete (%d routing edges)", len(edge_rows))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox", help="minLat,minLon,maxLat,maxLon (default: DEFAULT_BBOX)"
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="bypass the Overpass cache"
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.bbox:
        parts = [float(p) for p in args.bbox.split(",")]
        bbox = (parts[0], parts[1], parts[2], parts[3])
    else:
        bbox = settings.bbox

    logging.basicConfig(level=logging.INFO)
    asyncio.run(ingest(bbox, use_cache=not args.no_cache))


if __name__ == "__main__":
    main()
