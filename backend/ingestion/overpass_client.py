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
USER_AGENT = os.environ.get(
    "OVERPASS_USER_AGENT", "get-out-door/0.1 (trail data ingestion)"
)

OSM_QUERY_TEMPLATE = """
[out:json][timeout:{timeout}];
(
  way["highway"~"path|track|cycleway|footway"]({bbox});
  node["natural"="water"]({bbox});
  node["tourism"~"alpine_hut|wilderness_hut"]({bbox});
  node["tourism"="camp_site"]({bbox});
  node["tourism"="viewpoint"]({bbox});
  node["railway"="station"]({bbox});
  node["amenity"="swimming_area"]({bbox});
  node["leisure"="swimming_area"]({bbox});
);
out body geom;
"""


def build_query(bbox: tuple[float, float, float, float]) -> str:
    settings = get_settings()
    bbox_str = ",".join(f"{c}" for c in bbox)
    return OSM_QUERY_TEMPLATE.format(timeout=settings.overpass_timeout_s, bbox=bbox_str)


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
