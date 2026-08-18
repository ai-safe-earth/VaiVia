"""Spike: can Wikipedia/Wikidata supply the prose Trailforks cannot?

The embedding needs descriptive text. Trailforks is licensing-blocked
(docs/licensing.md) and OSM route relations carry `description` on only 10-21%
of routes. But OSM POIs often carry `wikidata` and `wikipedia` tags, and peaks,
lakes and ermitas are exactly the sort of thing that has an article.

Two sources with VERY different licences, measured separately on purpose:

  WIKIDATA   CC0 (public domain). Short one-line descriptions, plus structured
             claims like elevation. No attribution legally required.
  WIKIPEDIA  CC-BY-SA. Rich first-paragraph prose, but share-alike -- a
             heavier obligation than anything else we ingest, and worth a
             deliberate decision rather than a default.

Read-only. Reuses the cached Overpass response from ingestion, so coverage
costs nothing; only the sampled article fetches hit the network.

Run from backend/:
    uv run python -m scripts.spike_wiki_descriptions
    uv run python -m scripts.spike_wiki_descriptions --samples 6
"""

import argparse
import asyncio
import logging
from collections import Counter, defaultdict
from typing import Any

import httpx

from core.config import get_settings
from ingestion.osm_extract import poi_type_for
from ingestion.overpass_client import build_query, fetch

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
# Wikimedia's User-Agent policy REJECTS (403) any agent without a contact URL.
# Our Overpass UA has none, so reusing it silently returns nothing.
WIKI_USER_AGENT = "VaiVia/0.1 (https://github.com/ai-safe-earth/VaiVia) httpx"
DESTINATION_TYPES = ("peak", "saddle", "lake", "chapel", "castle", "waterfall", "hut")


def poi_elements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """POI elements only: nodes, plus areas which Overpass returns with center."""
    out = []
    for e in payload.get("elements", []):
        if e.get("type") == "way" and "geometry" in e:
            continue  # a routing way, not a POI
        if poi_type_for(e.get("tags", {})) is not None:
            out.append(e)
    return out


async def wikipedia_extract(client: httpx.AsyncClient, tag: str) -> str | None:
    """OSM `wikipedia` tags look like 'it:Grigna' — lang, colon, article title."""
    if ":" not in tag:
        return None
    lang, title = tag.split(":", 1)
    slug = title.replace(" ", "_")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{slug}"
    try:
        r = await client.get(url, timeout=20)
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("extract") or None
    except Exception:  # noqa: BLE001 — spike
        return None


async def wikidata_descriptions(
    client: httpx.AsyncClient, qids: list[str]
) -> dict[str, str]:
    """Wikidata is CC0. Descriptions are one line; prefer it, fall back to en."""
    out: dict[str, str] = {}
    for chunk_start in range(0, len(qids), 40):
        chunk = qids[chunk_start : chunk_start + 40]
        params = {
            "action": "wbgetentities",
            "ids": "|".join(chunk),
            "props": "descriptions",
            "languages": "it|en",
            "format": "json",
        }
        try:
            r = await client.get(WIKIDATA_API, params=params, timeout=30)
            entities = r.json().get("entities", {})
        except Exception:  # noqa: BLE001 — spike
            continue
        for qid, entity in entities.items():
            desc = entity.get("descriptions", {})
            value = (desc.get("it") or desc.get("en") or {}).get("value")
            if value:
                out[qid] = value
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=4)
    args = parser.parse_args()

    settings = get_settings()
    payload = await fetch(build_query(settings.bbox))
    pois = poi_elements(payload)
    print(f"POIs in the Lecco bbox: {len(pois)}\n")

    have_wd = defaultdict(int)
    have_wp = defaultdict(int)
    totals = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_qids: list[str] = []

    for e in pois:
        tags = e.get("tags", {})
        t = poi_type_for(tags)
        totals[t] += 1
        if tags.get("wikidata"):
            have_wd[t] += 1
            all_qids.append(tags["wikidata"])
        if tags.get("wikipedia"):
            have_wp[t] += 1
            if t in DESTINATION_TYPES and len(samples[t]) < args.samples:
                samples[t].append(tags)

    print(f"{'type':<14}{'total':>7}{'wikidata':>10}{'':>7}{'wikipedia':>11}")
    for t, n in totals.most_common():
        wd, wp = have_wd[t], have_wp[t]
        print(
            f"{t:<14}{n:>7}{wd:>10}{f'({wd / n:.0%})':>7}"
            f"{wp:>7}{f'({wp / n:.0%})':>8}"
        )

    named_dest = sum(totals[t] for t in DESTINATION_TYPES)
    wd_dest = sum(have_wd[t] for t in DESTINATION_TYPES)
    print(
        f"\ndestinations: {wd_dest}/{named_dest} carry wikidata "
        f"({wd_dest / named_dest:.0%})"
        if named_dest
        else ""
    )

    async with httpx.AsyncClient(
        headers={"User-Agent": WIKI_USER_AGENT}, follow_redirects=True
    ) as client:
        print("\n=== WIKIDATA (CC0) — one-line descriptions ===")
        wd_map = await wikidata_descriptions(client, all_qids[:60])
        for qid, desc in list(wd_map.items())[:6]:
            print(f"  {qid}: {desc}")

        print("\n=== WIKIPEDIA (CC-BY-SA) — first-paragraph prose ===")
        for t in DESTINATION_TYPES:
            for tags in samples.get(t, [])[:1]:
                extract = await wikipedia_extract(client, tags["wikipedia"])
                if not extract:
                    continue
                name = tags.get("name", "?")
                print(f"\n  [{t}] {name}  ({tags['wikipedia']})")
                print(f"    {extract[:320]}{'...' if len(extract) > 320 else ''}")

    print("\nRead-only: nothing written to the graph.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
