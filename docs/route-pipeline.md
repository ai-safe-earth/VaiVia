# Route pipeline: build geometry offline, serve meaning at runtime

Proposed 2026-08-18. **Built 2026-08-20 in `pipeline/draw/`** — over the curated
PostGIS network with pgRouting, not over the backend graph this document originally
assumed (the backend catalogue built 2026-08-18 was the proof of shape; the pipeline
build is the one the product uses, per docs/route-document.md). What follows is the
original design; where the two differ, `pipeline/draw/` and
`pipeline/docs/metadata-rules.md` are authoritative. Three differences matter:

- **Route ids derive from geometry** (`draw/route_id.py`), never from sequence or run,
  so photos and comments cannot orphan on a rebuild (docs/social-layer.md).
- **The MTB verdict and all metadata run along the walked edge sequence** — direction
  explicit per edge, ascent/descent swapping on reversed edges — not a spatial match.
- **The engine is pgRouting over our own edges** (provider spike verdict: every engine
  draws on the same OSM ways; ours returns edge ids natively).


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

**1. Anchors — BUILT 2026-08-18** (`scripts/build_trailheads.py`).

Anchors snap to the network within 200 m and must sit in the largest connected
component, so a route can never be seeded on an island — the failure that
returned 0/10 loops before fragility #9 was found. GDS WCC now writes
`component_id` onto every `(:Intersection)`, which turns "can a route exist from
here" into a property check rather than an algorithm per candidate.

Lecco: 1,519 of 1,547 parking/station POIs snapped, clustering to **266
distinct `(:Trailhead)` nodes**. That collapse is the point — a row of car parks
along one road is one place to start, and 1,511 candidates would have produced a
catalogue nobody could review.

Each trailhead carries the off-road share of the network within 750 m, which
separates a mountain trailhead from a supermarket car park that happens to touch
a footpath:

| off-road share | trailheads |
|---|---|
| trail (>60%) | 46 |
| mixed (30-60%) | 145 |
| urban (<30%) | 75 |

The score is **descriptive, not a filter**. What counts as "enough trail" is a
product decision, and dropping candidates inside the build step would hide it.

Only 37 of 266 have a name, because car parks are rarely named in OSM. Naming
trailheads from a nearby named feature is unsolved and worth doing before any of
this reaches a user — "start from the car park at Vò di Moncodeno" is an answer,
"start from trailhead 4312828180" is not.

Trailheads are their own nodes, not labels on `(:Intersection)`, so re-running
OSM ingestion cannot clobber derived data. `(:Trailhead)-[:STARTS_AT]->(:Intersection)`
gives routing its entry point and `-[:SERVED_BY]->(:POI)` records which car parks
it represents.

**Catalogue size is now predictable:** 266 trailheads x 5 distances x ~3 kept is
roughly 4,000 routes for Lecco, or ~2,900 if the urban trailheads are excluded.
Reviewable.

**2-5. Generate, score, dedup, enrich, persist — BUILT 2026-08-18**
(`graph/route_generation.py`, `graph/route_scoring.py`, `scripts/build_routes.py`).

First real catalogue, over the 46 trailheads at or above 60% off-road, four
target distances, 6 seeds each, keeping the best 3:

| | |
|---|---|
| Routes | **502** |
| Trailheads that produced nothing | **0** |
| Mean off-road share | **87%** |
| Mean retrace | 25% |
| Mean score | 0.72 |
| POIs per route (mean) | 25 |
| Routes with at least one named POI | 425 / 502 |
| `PASSES` edges | 12,363 |

And the thing the whole design exists for — a chat turn *selecting* instead of
computing. "Under 16 km, mostly off-road, passing a peak" is now a filter:

```cypher
MATCH (r:Route)-[:PASSES]->(p:POI {type:'peak'})
WHERE r.distance_m <= 16000 AND r.off_road_share > 0.8
RETURN r, p ORDER BY r.score DESC
```

returning real answers — Corno Zuccone, Monte Castello, Zucco di Pralongone.

**Rebuilt on GraphHopper, 2026-08-18.** Elevation and per-activity profiles
(`infra/graphhopper/config.yml`, `graph/graphhopper.py`), with a length-fit gate
at persistence:

| | hike | mtb |
|---|---|---|
| Routes | 255 | 218 |
| Mean score | 0.77 | 0.74 |
| Off-road | 74% | 66% |
| Retrace | **4%** | **5%** |
| Mean ascent | 1,719 m | 1,631 m |
| Rated (sac_scale / mtb:scale) | 255 | 218 |

Retrace 25% -> 4% is the headline, and every route now carries real climb where
our own generator reported none.

The gate halved the catalogue and raised the mean score from 0.65 to 0.77.
`round_trip.distance` is a hint that overshoots, badly in steep terrain where the
only paths out are long, so roughly half of what was generated answered a
different question than the one it was filed under. Dropping those at
persistence — not in the scorer, which stays honest — is what bought the quality.
Drops are reported per target, because a target that mostly fails is a coverage
fact worth seeing.

**A misreading worth recording.** The first look at the rebuilt hike catalogue
appeared to show no 5 km routes at all, and that was written up as a finding
about alpine terrain. It was a truncated table: there are 44 hike and 43 mtb
5 km loops, averaging 5.4 km against target. Check the whole output before
turning an absence into a claim.

**The 25% mean retrace is the known weakness of our own generator**, and it is
the one number GraphHopper would transform (0.0-3.2% measured, see
`docs/routing-engine.md`). The catalogue is good enough to build the rest of the
product against; regenerating it from a better engine changes no schema and no
query, which was the point of putting generation behind a seam.

**3. Score and dedup.** Length accuracy, retrace, off-road share, climb, POIs
passed, overlap with waymarked CAI routes. Candidates overlap heavily, so dedup
by geometric similarity before keeping the top few per (anchor, distance).

**4. Enrich.** Spatially join the polyline back to Neo4j: which POIs it passes,
which named trails it follows, which region it is in. Add elevation profile,
difficulty, surface mix. Compose the description from all of it.

**5. Persist and embed.** `MERGE (:Route {route_id})` with geometry and
properties, plus `PASSES_BY`/`NEAR_POI`-style edges to the POIs and trails it
touches, then embed the composed description.

**6-8. Runtime — BUILT 2026-08-18.** `loop_search` is a new atomic intent at
the LLM boundary, alongside trail_search / route / semantic_theme / clarify. It
carries only what a walker says out loud — a distance range, features to pass, a
place to start near, and whether to keep off roads — and maps onto the
`search_loops` template, which filters `(:Route)` and orders by the score
computed offline.

Verified live: *"a 15 km loop on trails past a peak near Lecco"* returns five
catalogue loops of 14.2-17.9 km at 83-98% off-road, over Monte Ocone, Punta
Cermenati (Monte Resegone) and Zucco di Teral. No routing happens in the turn.

`check_intents_live` still passes 17/17 with the adversarial half at 7/7, so
adding an intent did not weaken containment.

Two bugs this shook out, both worth remembering:

- **A stated distance is a point estimate, not an interval.** The model returns
  "a 15 km loop" as `min=max=15000`, an exact-equality filter. Real routes are
  15,771 m, so it matched nothing and the user was told no such loop existed
  while 500 sat in the catalogue. `widen_narrow_band` turns a band narrower than
  15% of itself into +/-20%, deterministically in Python rather than as another
  prompt rule the model may not follow — the same reasoning as the 0-bound
  scrub.
- **Composer tests are not orchestrator tests.** The first version passed every
  composer test and 500'd on every real request, because `_loops` referenced
  `self.db` where the attribute is `self._db`. There is now a test that
  executes the orchestrator path against a fake db.

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

**Runtime, both kinds — BUILT 2026-08-21 (owner rule).** A `trail_search` no
longer answers from the trail graph alone: `chat/composer.py::catalogue_view`
derives the same ask against the route catalogue whenever every stated
constraint can be honoured there, and the turn returns `trails` and `loops` as
two distinguishable blocks — the cards carry a kind label (Loop / Out & back /
Named route / Named trail) and the answer prose is told to keep them apart. A
constraint the catalogue cannot express (season, hazards, surfaces, difficulty
or climb floors) kills the view rather than being dropped — with one ratified
exception: a duration cap is dropped loudly, exactly as the explicit loop path
drops it, until DIN 33466 is calibrated.

The other half of the rule is agentic guiding: an ask that is only an activity
("I want to go hiking") composes to a deterministic clarify — what shape of
outing, roughly how far — with tappable suggestions that each land on a
different shape. Python decides when to ask, never the model. Two intent-prompt
rules back it: a bare invitation sets activity and nothing else, and history
feeds follow-ups only — a self-contained ask is decomposed on its own.
