"""Write the OSM extract GraphHopper imports.

GraphHopper builds its own graph from a raw OSM file rather than reading ours,
so it needs the same ways our ingestion sees. Using the SAME filter
(overpass_client.WALKABLE_HIGHWAYS) matters: if the two disagree about which
ways exist, a route GraphHopper returns can traverse ground our graph has never
heard of, and the map-back then has nothing to join it to.

Output is gitignored (*.osm) and regenerated rather than committed.

Run from backend/:
    uv run python -m scripts.export_osm_extract --region Lecco
    uv run python -m scripts.export_osm_extract --out ../infra/graphhopper/data/x.osm
"""

import argparse
import asyncio
import logging
from pathlib import Path

import httpx

from core.config import get_settings
from ingestion.overpass_client import USER_AGENT, WALKABLE_HIGHWAYS

logger = logging.getLogger(__name__)

# `>;` pulls in every node the matched ways reference. Without it the file has
# ways whose geometry cannot be resolved and GraphHopper imports an empty graph.
QUERY = """
[out:xml][timeout:{timeout}];
(way["highway"~"^({highways})$"]({bbox}););
(._;>;);
out meta;
"""

# The main server rejects heavy exports under load; kumi is a mirror that
# tolerates them. Tried in order.
ENDPOINTS = (
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
)
MIN_PLAUSIBLE_BYTES = 1_000_000


async def fetch_extract(
    bbox: tuple[float, float, float, float], timeout_s: int
) -> bytes:
    query = QUERY.format(
        timeout=timeout_s,
        highways=WALKABLE_HIGHWAYS,
        bbox=",".join(str(c) for c in bbox),
    )
    async with httpx.AsyncClient(
        timeout=timeout_s + 60, headers={"User-Agent": USER_AGENT}
    ) as client:
        for endpoint in ENDPOINTS:
            logger.info("requesting extract from %s", endpoint)
            try:
                response = await client.post(endpoint, data={"data": query})
            except httpx.HTTPError as error:
                logger.warning("%s failed: %s", endpoint, str(error)[:120])
                continue
            # Overpass answers "too busy" with a 200 and an HTML error body, so
            # a status check alone would happily write a 700-byte web page.
            if (
                response.status_code == 200
                and len(response.content) > MIN_PLAUSIBLE_BYTES
            ):
                return response.content
            logger.warning(
                "%s returned %d, %d bytes — trying the next mirror",
                endpoint,
                response.status_code,
                len(response.content),
            )
    raise RuntimeError("every Overpass endpoint failed or returned an error page")


async def build() -> tuple[Path, bytes]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", help="named region from REGIONS (default: bbox)")
    parser.add_argument(
        "--out",
        default="../infra/graphhopper/data/region.osm",
        help="where to write the extract",
    )
    args = parser.parse_args()

    settings = get_settings()
    bbox = settings.bbox
    if args.region:
        by_name = dict(settings.region_list)
        if args.region not in by_name:
            parser.error(
                f"unknown region {args.region!r}; configured: {sorted(by_name)}"
            )
        bbox = by_name[args.region]

    print(f"bbox: {bbox}")
    data = await fetch_extract(bbox, settings.overpass_timeout_s)

    # Returned rather than written here: the write is blocking and the caller
    # is sync, so it belongs outside the event loop.
    return Path(args.out), data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    path, payload = asyncio.run(build())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    print(f"wrote {path} ({len(payload) / 1_000_000:.1f} MB)")
    print(
        "GraphHopper reimports on start when this file changes; delete its "
        "graph-cache/ if it refuses with 'Profile does not match'."
    )
