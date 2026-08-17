"""Trailforks -> Neo4j ingestion (idempotent: MERGE on trail id).

Run from backend/:
    uv run python -m ingestion.trailforks_ingest --mock          # fixtures, offline
    uv run python -m ingestion.trailforks_ingest --bbox ...      # live API (needs key)

Loads (:Trail) nodes with the full owner-validated ontology, computes
per-activity durations, spatially matches trails to already-ingested OSM
segments (ordered COMPOSED_OF), and links trails to their region. Elevation
totals come from the source record; recomputing them from matched segments
happens once the SRTM backfill lands (Phase 2). Run ingestion.osm_ingest first.
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from core.config import get_settings
from core.durations import difficulty_level, hike_duration_min, mtb_duration_min
from core.geo import in_bbox
from graph.neo4j_client import Neo4jClient
from ingestion.spatial_match import MatchCandidate, match_trail

logger = logging.getLogger(__name__)

MOCK_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "trailforks_mock.json"

MERGE_TRAIL = """
UNWIND $rows AS row
MERGE (t:Trail {id: row.id})
SET t.name = row.name,
    t.source = 'trailforks',
    t.activity = row.activity,
    t.difficulty = row.difficulty,
    t.difficulty_level = row.difficulty_level,
    t.difficulty_notes = row.difficulty_notes,
    t.description = row.description,
    t.landscape_description = row.landscape_description,
    t.total_distance_m = row.total_distance_m,
    t.elevation_gain_m = row.elevation_gain_m,
    t.elevation_loss_m = row.elevation_loss_m,
    t.duration_hike_min = row.duration_hike_min,
    t.duration_mtb_min = row.duration_mtb_min,
    t.best_seasons = row.best_seasons,
    t.seasonal_hazards = row.seasonal_hazards,
    t.hazards_spring = row.hazards_spring,
    t.hazards_summer = row.hazards_summer,
    t.hazards_autumn = row.hazards_autumn,
    t.hazards_winter = row.hazards_winter,
    t.trailforks_url = row.trailforks_url
"""

# Replace-then-recreate keeps COMPOSED_OF consistent when a re-run changes the
# match set; MERGE alone would leave stale links behind.
DELETE_COMPOSED_OF = """
MATCH (t:Trail {id: $trail_id})-[c:COMPOSED_OF]->() DELETE c
"""

CREATE_COMPOSED_OF = """
UNWIND $rows AS row
MATCH (t:Trail {id: $trail_id}), (s:Segment {osm_way_id: row.osm_way_id})
CREATE (t)-[:COMPOSED_OF {seq: row.seq, match_confidence: row.match_confidence}]->(s)
"""

# Trail-level POI proximity (NEAR_POI). Segment-level PASSES_BY is deliberately
# tight; this wider radius is what makes "past a lake / hut" filters usable.
# Delete-then-recreate for the same reason as COMPOSED_OF: a radius change or
# re-match must not leave stale edges behind.
DELETE_NEAR_POI = """
MATCH (t:Trail {id: $trail_id})-[r:NEAR_POI]->() DELETE r
"""

# Pre-filter on the segment centroid (point index) before paying for the exact
# min distance over the segment's coordinates.
CREATE_NEAR_POI = """
MATCH (t:Trail {id: $trail_id})-[:COMPOSED_OF]->(s:Segment)
MATCH (p:POI)
WHERE point.distance(p.location, s.location)
      < $radius_m + coalesce(s.length_m, 0.0) / 2.0
WITH t, p,
     reduce(best = 1.0e12, c IN s.coordinates |
            CASE WHEN point.distance(p.location, c) < best
                 THEN point.distance(p.location, c) ELSE best END) AS seg_min
WITH t, p, min(seg_min) AS distance_m
WHERE distance_m <= $radius_m
MERGE (t)-[r:NEAR_POI]->(p)
SET r.distance_m = distance_m
RETURN p.osm_id AS osm_id
"""

# Region links are recomputed from geometry each run; drop first so a trail
# whose regenerated geometry left a region does not keep a stale link.
DELETE_TRAIL_REGIONS = """
UNWIND $rows AS trail_id
MATCH (t:Trail {id: trail_id})-[r:LOCATED_IN]->(:Region)
DELETE r
"""

MERGE_TRAIL_REGION = """
UNWIND $rows AS trail_id
MATCH (t:Trail {id: trail_id}), (r:Region {name: $region})
MERGE (t)-[:LOCATED_IN]->(r)
"""

FETCH_CANDIDATES = """
MATCH (s:Segment)
RETURN s.osm_way_id AS osm_way_id, s.highway_type AS highway_type,
       [c IN s.coordinates | [c.latitude, c.longitude]] AS coordinates,
       [s.location.latitude, s.location.longitude] AS location
"""


SEASONS = ("spring", "summer", "autumn", "winter")


def hazards_by_season(raw: dict[str, Any]) -> dict[str, list[str]]:
    """Per-season hazard lists, keyed 'hazards_<season>'.

    A record may scope hazards with `hazards_by_season`; the flat
    `seasonal_hazards` stays the union for display. Records without scoping
    get the union in EVERY season — the conservative reading of unscoped data
    (a hazard we cannot place in time is assumed always possible).
    """
    scoped = raw.get("hazards_by_season")
    if scoped is not None:
        return {f"hazards_{s}": list(scoped.get(s, [])) for s in SEASONS}
    union = list(raw.get("seasonal_hazards", []))
    return {f"hazards_{s}": union for s in SEASONS}


def hazards_union(raw: dict[str, Any]) -> list[str]:
    """Flat display list: explicit seasonal_hazards, else the scoped union."""
    if raw.get("seasonal_hazards"):
        return list(raw["seasonal_hazards"])
    scoped = raw.get("hazards_by_season") or {}
    union: list[str] = []
    for season in SEASONS:
        for h in scoped.get(season, []):
            if h not in union:
                union.append(h)
    return union


def trail_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize one Trailforks trail record to the graph ontology (metres/minutes)."""
    level = difficulty_level(raw["difficulty"])
    distance_m = float(raw["total_distance_km"]) * 1000
    gain = raw.get("elevation_gain_m")
    loss = raw.get("elevation_loss_m")
    activity = raw.get("activity", "mtb")
    return {
        "id": raw["trail_id"],
        "name": raw["name"],
        "activity": activity,
        "difficulty": raw["difficulty"],
        "difficulty_level": level,
        "difficulty_notes": raw.get("difficulty_notes"),
        "description": raw.get("description"),
        "landscape_description": raw.get("landscape_description"),
        "total_distance_m": distance_m,
        "elevation_gain_m": gain,
        "elevation_loss_m": loss,
        "duration_hike_min": (
            hike_duration_min(distance_m, gain, loss)
            if activity in ("hike", "mixed")
            else None
        ),
        "duration_mtb_min": (
            mtb_duration_min(distance_m, gain, level)
            if activity in ("mtb", "mixed")
            else None
        ),
        "best_seasons": raw.get("best_seasons", []),
        "seasonal_hazards": hazards_union(raw),
        **hazards_by_season(raw),
        "trailforks_url": trailforks_url(raw),
    }


def trailforks_url(raw: dict[str, Any]) -> str | None:
    """Public Trailforks page for a source record, when one can be named.

    The live API supplies `alias` (the URL slug); an explicit `trailforks_url`
    wins if present. Records with neither get no link — never guess a URL.
    """
    explicit = raw.get("trailforks_url")
    if explicit:
        return str(explicit)
    alias = raw.get("alias")
    if alias:
        return f"https://www.trailforks.com/trails/{alias}/"
    return None


def trail_polyline(raw: dict[str, Any]) -> list[tuple[float, float]]:
    """GeoJSON LineString [[lon, lat], ...] -> [(lat, lon), ...]."""
    return [(lat, lon) for lon, lat in raw["geometry"]["coordinates"]]


def load_mock() -> list[dict[str, Any]]:
    return json.loads(MOCK_PATH.read_text(encoding="utf-8"))


async def fetch_live(bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    raise NotImplementedError(
        "Live Trailforks API ingestion lands when API access is confirmed "
        "(licensing review pending — docs/fragilities.md #4). Use --mock."
    )


async def ingest(raw_trails: list[dict[str, Any]]) -> None:
    settings = get_settings()
    rows = [trail_row(raw) for raw in raw_trails]

    async with Neo4jClient() as db:
        await db.run_batched(MERGE_TRAIL, rows)

        candidate_records = await db.run(FETCH_CANDIDATES)
        candidates = [
            MatchCandidate(
                osm_way_id=r["osm_way_id"],
                highway_type=r["highway_type"],
                coordinates=[(lat, lon) for lat, lon in r["coordinates"]],
                location=(r["location"][0], r["location"][1]),
            )
            for r in candidate_records
        ]
        logger.info(
            "matching %d trails against %d segments", len(rows), len(candidates)
        )

        for raw, row in zip(raw_trails, rows, strict=True):
            matches = match_trail(
                trail_polyline(raw),
                row["activity"],
                candidates,
                settings.spatial_match_threshold_m,
            )
            await db.run(DELETE_COMPOSED_OF, trail_id=row["id"])
            if matches:
                await db.run(
                    CREATE_COMPOSED_OF,
                    trail_id=row["id"],
                    rows=[vars(m) for m in matches],
                )
            await db.run(DELETE_NEAR_POI, trail_id=row["id"])
            near = await db.run(
                CREATE_NEAR_POI,
                trail_id=row["id"],
                radius_m=settings.poi_near_radius_m,
            )
            logger.info(
                "trail %s: %d segments matched, %d POIs within %.0f m",
                row["id"],
                len(matches),
                len(near),
                settings.poi_near_radius_m,
            )

        await db.run(DELETE_TRAIL_REGIONS, rows=[row["id"] for row in rows])
        for region_name, region_bbox in settings.region_list:
            in_region = [
                row["id"]
                for raw, row in zip(raw_trails, rows, strict=True)
                if any(in_bbox(p, region_bbox) for p in trail_polyline(raw))
            ]
            if in_region:
                await db.run(MERGE_TRAIL_REGION, region=region_name, rows=in_region)
                logger.info("region %s: %d trails", region_name, len(in_region))
    logger.info("Trailforks ingestion complete (%d trails)", len(rows))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--mock", action="store_true", help="use fixtures/trailforks_mock.json"
    )
    source.add_argument("--bbox", help="live API for minLat,minLon,maxLat,maxLon")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.mock:
        asyncio.run(ingest(load_mock()))
    else:
        parts = [float(p) for p in args.bbox.split(",")]

        async def live(bbox: tuple[float, float, float, float]) -> None:
            await ingest(await fetch_live(bbox))

        asyncio.run(live((parts[0], parts[1], parts[2], parts[3])))


if __name__ == "__main__":
    main()
