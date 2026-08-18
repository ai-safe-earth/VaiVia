"""Overpass API client: exponential backoff on throttling, on-disk response cache.

The cache (fixtures/overpass_cache/, gitignored) makes re-runs offline and keeps
dev friendly to the public Overpass servers.
"""

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from core.config import get_settings

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "overpass_cache"
RETRYABLE_STATUS = {429, 502, 504}
MAX_RETRIES = 5

# Overpass rejects generic library User-Agents with 406 Not Acceptable: the
# default `python-httpx/x.y.z` gets 406 where this string gets 200. Identifying
# the client is also what the OSM usage policy asks for. Override via env to add
# a contact address before running anything high-volume.
USER_AGENT = os.environ.get("OVERPASS_USER_AGENT", "VaiVia/0.1 (trail data ingestion)")

# Trail ways alone do not form a connected network: a path ends at a lane, you
# walk 200 m, the next path starts. Ingesting only path/track/cycleway/footway
# shattered Lecco into 1,627 components with the largest holding 31.7% of
# intersections (docs/fragilities.md #9), which made routing between arbitrary
# points fail and loop construction impossible.
#
# So the walkable connective types are included too. motorway/trunk/primary and
# their _link ramps are deliberately excluded: routing a walker or rider onto
# those is wrong, and often illegal. Anchored so `service` cannot also match
# `services` (motorway service areas) or `secondary_link` ramps.
#
# Caveat this does NOT fix: Dijkstra still weights by raw distance, so a
# straight road can now beat a winding trail. Comfort weighting is the
# follow-up — highway_type is already stored on every CONNECTS_TO edge.
# Trail-to-segment matching is unaffected: COMPATIBLE_HIGHWAYS in
# spatial_match.py still refuses to compose a trail out of residential streets.
WALKABLE_HIGHWAYS = (
    "path|track|cycleway|footway|bridleway|steps|pedestrian"
    "|living_street|residential|unclassified|service|tertiary|secondary"
)

# Two output statements. Routing ways need full `geom` to be split at
# intersections; POIs need only a point, and many of them (car parks, lakes,
# picnic sites) are areas rather than nodes, so `out center` collapses each to
# one coordinate. osm_extract tells the two apart by geometry-vs-center.
#
# The POI set covers both roles the route pipeline needs: ANCHORS to start from
# (parking, station) and DESTINATIONS worth reaching (peak, saddle, lake, beach,
# waterfall, chapel/ermita, castle). See docs/route-pipeline.md.
OSM_QUERY_TEMPLATE = """
[out:json][timeout:{timeout}];
(
  way["highway"~"^({highways})$"]({bbox});
);
out body geom;
(
  node["natural"~"^(water|peak|saddle|beach|spring|cave_entrance)$"]({bbox});
  way["natural"~"^(water|beach)$"]({bbox});
  relation["natural"~"^(water|beach)$"]({bbox});
  node["tourism"~"^(alpine_hut|wilderness_hut|camp_site|viewpoint|picnic_site)$"]({bbox});
  way["tourism"~"^(camp_site|picnic_site)$"]({bbox});
  node["railway"="station"]({bbox});
  node["amenity"~"^(swimming_area|parking)$"]({bbox});
  way["amenity"~"^(swimming_area|parking)$"]({bbox});
  node["leisure"="swimming_area"]({bbox});
  way["leisure"="swimming_area"]({bbox});
  node["waterway"="waterfall"]({bbox});
  node["building"="chapel"]({bbox});
  way["building"="chapel"]({bbox});
  node["historic"~"^(wayside_shrine|wayside_cross|castle|ruins)$"]({bbox});
  way["historic"~"^(castle|ruins)$"]({bbox});
);
out center;
"""


def build_query(bbox: tuple[float, float, float, float]) -> str:
    settings = get_settings()
    bbox_str = ",".join(f"{c}" for c in bbox)
    return OSM_QUERY_TEMPLATE.format(
        timeout=settings.overpass_timeout_s,
        bbox=bbox_str,
        highways=WALKABLE_HIGHWAYS,
    )


async def fetch(query: str, use_cache: bool = True) -> dict[str, Any]:
    """POST an Overpass QL query; return the parsed JSON response."""
    cache_file = CACHE_DIR / f"{hashlib.sha256(query.encode()).hexdigest()}.json"
    if use_cache and cache_file.exists():
        logger.info("overpass cache hit: %s", cache_file.name)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    settings = get_settings()
    delay = 2.0
    async with httpx.AsyncClient(
        timeout=settings.overpass_timeout_s + 10,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for attempt in range(MAX_RETRIES):
            response = await client.post(settings.overpass_url, data={"data": query})
            if response.status_code == 200:
                payload = response.json()
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(payload), encoding="utf-8")
                return payload
            if response.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES - 1:
                logger.warning(
                    "overpass %d, retry %d/%d in %.0fs",
                    response.status_code,
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                delay *= 2
                continue
            response.raise_for_status()
    raise RuntimeError("overpass retries exhausted")
