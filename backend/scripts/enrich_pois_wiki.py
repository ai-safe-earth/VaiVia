"""Attach open-licensed descriptions to POIs from Wikipedia and Wikidata.

Why this exists: the embedding needs prose, Trailforks cannot supply it
(docs/licensing.md), and OSM's own `description` tag is thin. Where a POI
carries a `wikidata` or `wikipedia` tag there is real, well-written text about
the place, under a licence that actually permits use.

Coverage is partial by nature — about one destination in eight (see
docs/route-pipeline.md). This enriches the marquee places and leaves the long
tail to composed-from-facts. That is the intended division of labour, not a
shortfall to fix.

Resolution order per POI, best text first:

  1. `wikipedia` tag ("it:Grigna settentrionale") -> article summary.
  2. `wikidata` tag -> the entity's Wikipedia sitelink -> article summary.
     This matters: 27% of peaks carry wikidata but only 8% carry wikipedia, so
     going through the entity reaches articles the POI never named.
  3. `wikidata` tag -> the entity's one-line description. Thin, but CC0.

Attribution is stored per POI, not assumed. Wikipedia text is CC-BY-SA and must
be credited wherever it is shown; Wikidata is CC0 and need not be, though we
record it anyway so the provenance of every sentence is answerable.

Idempotent: a POI that already has a description is skipped unless --refresh.

Run from backend/ with Neo4j up:
    uv run python -m scripts.enrich_pois_wiki
    uv run python -m scripts.enrich_pois_wiki --limit 50 --refresh
"""

import argparse
import asyncio
import logging
from typing import Any

import httpx

from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
# Wikimedia 403s any User-Agent without a contact URL, and does it silently
# enough to look like "no articles exist". Keep the URL in here.
USER_AGENT = "VaiVia/0.1 (https://github.com/ai-safe-earth/VaiVia) httpx"
# Italian first: these are Italian places, and the it.wikipedia article is
# usually longer and more specific than the English stub.
LANGS = ("it", "en")
WIKIDATA_BATCH = 40

FETCH_POIS = """
MATCH (p:POI)
WHERE (p.wikidata IS NOT NULL OR p.wikipedia IS NOT NULL)
  AND ($refresh OR p.description IS NULL)
RETURN p.osm_id AS osm_id, p.name AS name, p.type AS type,
       p.wikidata AS wikidata, p.wikipedia AS wikipedia
ORDER BY p.type, p.osm_id
"""

STORE_DESCRIPTION = """
UNWIND $rows AS row
MATCH (p:POI {osm_id: row.osm_id})
SET p.description = row.description,
    p.description_source = row.source,
    p.description_license = row.license,
    p.description_url = row.url,
    p.description_lang = row.lang
"""


async def article_summary(
    client: httpx.AsyncClient, lang: str, title: str
) -> dict[str, Any] | None:
    slug = title.replace(" ", "_")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{slug}"
    try:
        response = await client.get(url, timeout=20)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    data = response.json() or {}
    extract = (data.get("extract") or "").strip()
    # A disambiguation page carries no prose about a place, only a list of
    # unrelated meanings, which would poison the embedding.
    if not extract or data.get("type") == "disambiguation":
        return None
    page_url = (data.get("content_urls", {}).get("desktop", {}) or {}).get("page")
    return {
        "description": extract,
        "source": "wikipedia",
        "license": "CC-BY-SA-4.0",
        "url": page_url or f"https://{lang}.wikipedia.org/wiki/{slug}",
        "lang": lang,
    }


async def wikidata_entities(
    client: httpx.AsyncClient, qids: list[str]
) -> dict[str, dict[str, Any]]:
    """Sitelinks and descriptions for a batch of entities."""
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(qids), WIKIDATA_BATCH):
        chunk = qids[start : start + WIKIDATA_BATCH]
        params = {
            "action": "wbgetentities",
            "ids": "|".join(chunk),
            "props": "descriptions|sitelinks",
            "languages": "|".join(LANGS),
            "sitefilter": "|".join(f"{lang}wiki" for lang in LANGS),
            "format": "json",
        }
        try:
            response = await client.get(WIKIDATA_API, params=params, timeout=30)
            out.update((response.json() or {}).get("entities", {}))
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("wikidata batch failed: %s", str(error)[:120])
    return out


def from_wikidata_description(
    entity: dict[str, Any], qid: str
) -> dict[str, Any] | None:
    descriptions = entity.get("descriptions", {})
    for lang in LANGS:
        value = (descriptions.get(lang) or {}).get("value")
        if value:
            return {
                "description": value,
                "source": "wikidata",
                "license": "CC0-1.0",
                "url": f"https://www.wikidata.org/wiki/{qid}",
                "lang": lang,
            }
    return None


async def describe(
    client: httpx.AsyncClient, poi: dict[str, Any], entity: dict[str, Any]
) -> dict[str, Any] | None:
    """Best available text for one POI, richest source first."""
    tag = poi.get("wikipedia")
    if tag and ":" in tag:
        lang, title = tag.split(":", 1)
        found = await article_summary(client, lang, title)
        if found:
            return found

    if entity:
        # The entity's sitelinks reach articles the POI itself never named,
        # which is where most of the coverage comes from.
        sitelinks = entity.get("sitelinks", {})
        for lang in LANGS:
            link = sitelinks.get(f"{lang}wiki") or {}
            if link.get("title"):
                found = await article_summary(client, lang, link["title"])
                if found:
                    return found

    qid = poi.get("wikidata")
    if entity and qid:
        return from_wikidata_description(entity, qid)
    return None


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="stop after N POIs (trial run)")
    parser.add_argument(
        "--refresh", action="store_true", help="re-fetch POIs that already have one"
    )
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    stats = {"wikipedia": 0, "wikidata": 0, "none": 0}

    async with Neo4jClient() as db:
        pois = await db.run(FETCH_POIS, refresh=bool(args.refresh))
        if args.limit:
            pois = pois[: args.limit]
        print(f"POIs with a wiki tag needing a description: {len(pois)}")
        if not pois:
            return

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            qids = [p["wikidata"] for p in pois if p.get("wikidata")]
            entities = await wikidata_entities(client, qids)
            print(f"resolved {len(entities)} wikidata entities")

            for poi in pois:
                entity = entities.get(poi.get("wikidata") or "", {})
                found = await describe(client, poi, entity)
                if found:
                    stats[found["source"]] += 1
                    rows.append({"osm_id": poi["osm_id"], **found})
                else:
                    stats["none"] += 1

        if rows:
            await db.run_batched(STORE_DESCRIPTION, rows, batch_size=500)

    print(
        f"\nstored {len(rows)} descriptions: "
        f"{stats['wikipedia']} wikipedia (CC-BY-SA), "
        f"{stats['wikidata']} wikidata (CC0), "
        f"{stats['none']} unresolved"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
