"""Spike: how much of what Trailforks gives us does OSM already carry?

Read-only. Answers one question before we design an OSM-only VaiVia: in OUR
regions, does OSM hold enough to replace the curated layer?

Two things are measured, because the curated layer is really two things:

  NAMED TRAILS   Trailforks `(:Trail)` is a named, curated collection of paths.
                 The OSM analog is a route relation (route=hiking|foot|mtb|
                 bicycle) — named, often numbered (CAI sentiero), grouping many
                 ways. Counted per region, with how many are named and which
                 networks they belong to.

  DIFFICULTY     Trailforks supplies a difficulty rating and surface. OSM has
                 sac_scale (T1-T6 hiking), mtb:scale (0-6), plus surface and
                 trail_visibility — but only where a mapper added them, which
                 is exactly what varies by region and needs measuring.

Run from backend/:
    uv run python -m scripts.spike_osm_coverage

Hits the live Overpass API (cached under fixtures/overpass_cache/, so a re-run
is offline and free). Writes nothing to the graph.
"""

import asyncio
import logging
from collections import Counter
from typing import Any

from core.config import get_settings
from ingestion.overpass_client import fetch

# `out tags` returns id + tags only — no geometry. Far lighter than the
# ingestion query, which is what makes measuring the whole bbox cheap.
WAYS_QUERY = """
[out:json][timeout:{timeout}];
way["highway"~"path|track|cycleway|footway|bridleway"]({bbox});
out tags;
"""

RELATIONS_QUERY = """
[out:json][timeout:{timeout}];
relation["route"~"hiking|foot|mtb|bicycle"]({bbox});
out tags;
"""

# The tags that would have to stand in for Trailforks metadata.
WAY_TAGS_OF_INTEREST = (
    "name",
    "sac_scale",
    "mtb:scale",
    "mtb:scale:uphill",
    "surface",
    "trail_visibility",
    "incline",
    "width",
)

# On route relations: is there anything to embed, and any elevation/length?
RELATION_TAGS_OF_INTEREST = (
    "description",
    "note",
    "distance",
    "ascent",
    "descent",
    "roundtrip",
    "symbol",
    "osmc:symbol",
    "website",
)


def pct(count: int, total: int) -> str:
    return f"{(100 * count / total):5.1f}%" if total else "    -"


def summarize_ways(elements: list[dict[str, Any]]) -> None:
    total = len(elements)
    print(f"  paths/tracks found: {total}")
    if not total:
        return

    present = Counter()
    for element in elements:
        tags = element.get("tags", {})
        for tag in WAY_TAGS_OF_INTEREST:
            if tags.get(tag):
                present[tag] += 1

    print("  tag coverage:")
    for tag in WAY_TAGS_OF_INTEREST:
        count = present[tag]
        print(f"    {tag:<20} {count:>6}  {pct(count, total)}")

    for tag in ("sac_scale", "mtb:scale", "surface"):
        values = Counter(
            element.get("tags", {}).get(tag)
            for element in elements
            if element.get("tags", {}).get(tag)
        )
        if values:
            top = ", ".join(f"{v}={c}" for v, c in values.most_common(6))
            print(f"  {tag} values: {top}")


def summarize_relations(elements: list[dict[str, Any]]) -> None:
    total = len(elements)
    print(f"  route relations: {total}")
    if not total:
        print("  -> no named-route layer here; trails would have to come from")
        print("     somewhere else (generated, or our own curation)")
        return

    named = [e for e in elements if e.get("tags", {}).get("name")]
    by_route = Counter(e.get("tags", {}).get("route") for e in elements)
    networks = Counter(
        e.get("tags", {}).get("network")
        for e in elements
        if e.get("tags", {}).get("network")
    )

    print(f"    named:            {len(named):>6}  {pct(len(named), total)}")
    print(f"    by route type:    {dict(by_route)}")
    if networks:
        print(f"    networks:         {dict(networks)}")

    # The crux for semantic search: our embedding is built from Trailforks
    # prose. If relations carry no description, that layer needs a new input.
    print("    tag coverage on relations:")
    for tag in RELATION_TAGS_OF_INTEREST:
        count = sum(1 for e in elements if e.get("tags", {}).get(tag))
        print(f"      {tag:<18} {count:>6}  {pct(count, total)}")

    print("    sample names:")
    for element in named[:8]:
        tags = element["tags"]
        ref = f" [{tags['ref']}]" if tags.get("ref") else ""
        print(f"      - {tags['name']}{ref}  ({tags.get('route')})")

    sample = next((e for e in elements if e.get("tags", {}).get("description")), None)
    if sample:
        text = sample["tags"]["description"]
        print(f"    sample description: {text[:200]!r}")


async def main() -> None:
    settings = get_settings()
    print("OSM coverage spike — can OSM replace the Trailforks curated layer?\n")

    for name, bbox in settings.region_list:
        bbox_str = ",".join(str(c) for c in bbox)
        print(f"=== {name} ({bbox_str}) ===")

        ways = await fetch(
            WAYS_QUERY.format(timeout=settings.overpass_timeout_s, bbox=bbox_str)
        )
        summarize_ways(ways.get("elements", []))

        print()
        relations = await fetch(
            RELATIONS_QUERY.format(timeout=settings.overpass_timeout_s, bbox=bbox_str)
        )
        summarize_relations(relations.get("elements", []))
        print()

    print("Read-only: nothing was written to the graph.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
