# Route pipeline: build geometry offline, serve meaning at runtime

Proposed 2026-08-18. **Design, not yet built.**

## The inversion

Today Neo4j *is* the routing engine: a chat turn triggers a live GDS projection
and Dijkstra. This design moves geometry upstream and offline, and leaves Neo4j
as the enriched, embedded catalogue the chat selects from.

```
  OFFLINE (batch, slow, thorough)          RUNTIME (fast, cheap)
  ─────────────────────────────────        ─────────────────────
  1 anchors    parking / stations
  2 generate   candidate routes            6 chat -> intents
  3 score      keep the good ones             |
  4 enrich     POIs, difficulty, climb,    7 Cypher filter
               surface, waymarks,             + vector search
               composed description          over (:Route)
  5 persist    MERGE (:Route) + embed     8 answer cites what it selected
```

Why this is better than computing on demand: offline you can generate 200
candidates and keep 5, apply expensive scoring, and *review what the system will
offer before a user sees it*. Runtime becomes a filter plus a vector search,
which is what Neo4j is actually good at.

## Correction: Trailforks descriptions are still not available

The plan says "add descriptions from Trailforks". That remains blocked — see
`docs/licensing.md`. Their data is API-only, needs a granted key, and the
Outside terms require prior written consent for commercial, in-software and AI
use, all three of which describe VaiVia. Nothing has changed. Planning the
pipeline around their prose would build a dependency we cannot legally satisfy.

Open substitutes for the description layer, in order of value:

1. **Composed from facts we hold.** Name and CAI ref, waymark symbol, difficulty
   from `sac_scale`/`mtb:scale`, elevation profile, surface mix, POIs passed,
   region. Factual rather than evocative, but embeddable — and the current
   embedding input already appends POI names, so the pattern exists.
2. **Wikipedia / Wikidata — measured 2026-08-18, and it is a supplement, not a
   foundation.** `scripts/spike_wiki_descriptions.py`, run over the 3,095 POIs
   in the Lecco bbox.

   | type | total | has `wikidata` | has `wikipedia` |
   |---|---|---|---|
   | peak | 281 | 27% | 8% |
   | saddle | 127 | 10% | 2% |
   | chapel | 569 | 6% | 0% |
   | lake | 155 | 4% | 3% |
   | castle | 5 | 60% | 40% |
   | station | 8 | 62% | 100% |
   | **all destinations** | **1,179** | **12%** | — |

   The prose where it exists is genuinely good — real, evocative Italian about
   real places ("Il Culmine di San Pietro è un valico situato a 1.258 m di
   altitudine... utilizzato dai pastori della Val Taleggio"). But it exists for
   roughly **one destination in eight**, and for the ermitas that motivated this
   — 569 chapels — Wikipedia coverage is effectively zero.

   So this does NOT solve the description problem. It enriches the marquee
   destinations (famous peaks, castles, notable lakes) and leaves the long tail
   untouched. Treat it as a bonus layer over composed-from-facts, never as the
   primary source.

   **Licence asymmetry matters here.** Wikidata is CC0 — no obligations, but
   only one-line descriptions ("montagna delle Prealpi Lombarde"), too thin to
   embed usefully. Wikipedia is CC-BY-SA — real prose, but share-alike is a
   heavier obligation than anything else we ingest, and storing it, embedding it
   and serving it are three separate questions. Decide deliberately; do not
   drift into it.

   **Operational gotcha:** Wikimedia returns **403** to any User-Agent without a
   contact URL. Our Overpass UA has none, so the first run silently produced
   nothing rather than failing loudly.

   **Built and run 2026-08-18** (`scripts/enrich_pois_wiki.py`). Of 3,195 Lecco
   POIs, 152 carry a wiki tag and 116 resolved to text:

   | source | count | avg length | verdict |
   |---|---|---|---|
   | Wikipedia (CC-BY-SA) | 48 | 160-475 chars | real prose, worth embedding |
   | Wikidata (CC0) | 68 | 24-33 chars | a label, not a description |
   | unresolved | 36 | — | no article, no description |

   The Wikipedia text lands where you would expect: 27 peaks, 8 stations, 4
   saddles, 4 lakes, 3 castles. That is the marquee layer working as intended.

   **The Wikidata one-liners should NOT go into the embedding.** At ~27
   characters they are category labels — "montagna delle Prealpi Lombarde" —
   and embedding them would add noise a vector search then has to overcome,
   while making a POI look described when it is not. Keep them for display and
   disambiguation; embed only `description_source = 'wikipedia'`.

   Resolving Wikidata entities to their Wikipedia sitelinks was expected to
   multiply coverage and did not: it added 7 articles over the 41 direct
   `wikipedia` tags. Most tagged entities simply have no it/en article.
3. **OSM `description` on route relations** — real but thin (21% Lecco, 11%
   Bergamo).
4. **Our own curation**, which is the only one that compounds into an asset
   nobody can revoke.

## The decision this design still needs

**"Draw all the possible paths" has no bounded answer** — the number of distinct
paths in a graph of 30k intersections is effectively infinite, and most are
worthless (a loop round a car park). The pipeline is only well-defined once
generation is bounded. Proposed bounding:

    for each anchor (trailhead near parking or a station)
      x each target distance (5, 10, 15, 20, 30 km)
        x N seeds
          -> generate, score, keep the best few

This makes the catalogue size predictable: `anchors x distances x kept`. It also
makes coverage measurable — we can say which anchors have good routes and which
do not, instead of hoping.

Point-to-point routes are generated the same way, anchor to destination, where a
destination is a POI worth reaching: peak, lake, ermita, hut, viewpoint, sea.

## Stage detail

**1. Anchors.** Intersections within ~200 m of `amenity=parking` or
`railway=station`, restricted to the main connected component so a route can
never be seeded on an island. Precomputed, stored on the node.

**2. Generate.** Round trips and point-to-point. Whether this is our own
seed-and-stitch or GraphHopper is the open question in `docs/routing-engine.md`;
the pipeline does not care, it consumes geometry either way.

**3. Score and dedup.** Length accuracy, retrace, off-road share, climb, POIs
passed, overlap with waymarked CAI routes. Candidates overlap heavily, so dedup
by geometric similarity before keeping the top few per (anchor, distance).

**4. Enrich.** Spatially join the polyline back to Neo4j: which POIs it passes,
which named trails it follows, which region it is in. Add elevation profile,
difficulty, surface mix. Compose the description from all of it.

**5. Persist and embed.** `MERGE (:Route {route_id})` with geometry and
properties, plus `PASSES_BY`/`NEAR_POI`-style edges to the POIs and trails it
touches, then embed the composed description.

**6-8. Runtime.** Unchanged in shape from today: intents -> composer -> named
Cypher templates, except the templates now select over `(:Route)` rather than
assembling one.

## What this makes possible that today's design cannot

- "A 15 km loop from somewhere I can park, moderate, past a hut" becomes a
  filter, not a computation.
- Coverage is auditable: we can list anchors with no good routes.
- Bad routes can be found and removed before users see them.
- Route quality work stops competing with request latency.

## Consistency checking across sources

The plan mentions cross-checking sources. Worth being precise: matching one
provider's geometry against another's is the conflation problem already
documented in `docs/fragilities.md` #1, and it is genuinely hard. The existing
`spatial_match.py` does a constrained version (proximity plus highway
compatibility) and took real effort. Any multi-source geometry plan should
budget for that rather than assume a join.
