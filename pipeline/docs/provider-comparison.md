# Provider comparison: routes, difficulty and the MTB verdict from four sources

Spike, 2026-08-20, branch `spike/route-providers`. The question: run the pipeline
methodology (get the data → routes? enrich : draw, then enrich → POIs on the map and
route↔POI) against **OSM, OpenRouteService, TrailSplits and FreeRoute**, and find the
wisest combination of sources for a map of routes with difficulty and an MTB option.
Elevation deliberately out of scope (computable later, and already proven in the store).

Everything here is measured: the probes are cached under `pipeline/data/spike_cache/`,
the results are `review/spike-providers/results.json`, and the decision surface is
**`review/spike-providers/dashboard.html`** — open it, toggle providers, click a route.

## What each provider turned out to BE

| provider | what it actually is | path | access | route metadata of its own |
|---|---|---|---|---|
| **OSM** | 752 route relations + a noded segment network — both branches of the methodology, already built | 2a *and* 2b | local, ODbL | surface / sac_scale / mtb:scale / access per way |
| **TrailSplits** | **OSM route relations, repackaged.** `trails/v1/relation/{id}` returns the same `osm_relation_id`s our store holds — verified on the DOL (rel 1601198) | 2a | open, keyless, CORS | **none**: name/ref/network/osmc + a house `tier`; no surface, no SAC, no MTB. `type=mtb` is accepted and ignored |
| **OpenRouteService** | a routing **engine** over OSM segments — the "join segments into routes" step *is* their product. Profiles include `foot-hiking` and `cycling-mountain`; `round_trip` draws loops from one anchor | 2b | free API key, rate-limited | none in responses beyond geometry + distance/duration summary |
| **FreeRoute** | an ORS-shaped façade (`/v1/directions/{profile}`) over OSRM. It names its own profiles (driving-car, foot-walking, cycling-regular/-road/-mountain) — and then **500s every routing request**, driving-car in central Milan included. The façade is up; the engine behind it is down. No reachable docs | 2b (claimed) | broken today | unknown — never produced a route |

The deciding observation fell out of the very first probe: **nobody sells metadata.**
Every provider that works is OSM geometry in different clothes — TrailSplits serves OSM
relations, ORS routes over OSM ways — and none of them returns per-metre surface, SAC
grade or bike legality for our ground. Difficulty and the MTB option therefore cannot be
*bought from a source*; they have to be *derived against a network that carries the tags*,
which is exactly what `curated.edge` is.

## The methodology run

Every candidate — wherever its geometry came from — went through the **same enrichment**
(`spike_providers/enrich.py`):

1. Match the line onto `curated.edge`: an edge is *followed* when ≥ 50% of it lies within
   a 25 m corridor of the line. `matched_share` (matched metres / line metres) is reported
   per candidate, so the corridor choice is checkable rather than trusted.
2. **Difficulty** = the ratified ≥ 5% rule over the followed edges' `sac_scale`
   (`export/document.py`, not reimplemented).
3. **MTB verdict** = the access **conjunction** from `metadata-rules.md` (one forbidding
   edge forbids the route) + the ≥ 5% rule over `mtb:scale`. Nothing matched ⇒ *unknown*,
   never *yes*.
4. **POIs** = `curated.place` within 100 m of the line, positioned along it with
   `ST_LineLocatePoint` — the same route↔POI relationship the route documents use,
   provider-independent.

## The measured comparison

Run 2026-08-20 15:12 over Lecco (45.8,9.3 → 46.0,9.6); 12 candidates, every one through the same
enrichment. `matched` is the share of the candidate line our network accounts for.

| candidate | source | km | matched | SAC (≥5% rule) | MTB | surface | POIs ≤100 m |
|---|---|---:|---:|---|---|---|---:|
| sentiero 14 | osm | 7.9 | 100% | demanding_mountain_hiking | no | paved | 8 |
| La Gardata - Rifugio Elisa | osm | 2.5 | 100% | mountain_hiking | yes | ground | 6 |
| Dorsale Orobica Lecchese (percorso basso) | osm | 41.1 | 100% | demanding_mountain_hiking | no | ground | 96 |
| Dorsale Orobica Lecchese (percorso basso) | trailsplits | 101.2 | 47% | demanding_mountain_hiking | no | ground | 96 |
| Dorsale Orobica Lecchese (percorso alto) | trailsplits | 100.1 | 49% | demanding_mountain_hiking | no | ground | 101 |
| Lecco - Sondrio | trailsplits | 34.6 | 22% | mountain_hiking | no | ground | 9 |
| CamminaForeste - Tappa 9C | trailsplits | 23.2 | 100% | mountain_hiking | no | asphalt | 84 |
| CamminaForeste - Tappa 7D | trailsplits | 18.4 | 100% | mountain_hiking | no | asphalt | 30 |
| Sentiero dei Laghi Basso - Tappa 1 | trailsplits | 16.9 | 100% | mountain_hiking | no | ground | 56 |
| ors foot-hiking p2p | ors | 9.3 | 100% | demanding_mountain_hiking | no | ground | 16 |
| ors cycling-mountain p2p | ors | 10.9 | 100% | alpine_hiking | no | rock | 24 |
| ors foot-hiking loop | ors | 12.6 | 100% | mountain_hiking | no | asphalt | 22 |

What the numbers say:

- **The OSM baseline matches 100% by construction** — its value here is the metadata
  column, which nothing else fills.
- **TrailSplits confirms it is our data**: its three fully-local trails match 100%;
  the two DOL variants arrive as ~101 km against our clipped 41 km and match 47–49% —
  the exact share `qa.v_route_coverage` predicts from the bbox clip. Same relation ids,
  same ground.
- **ORS's drawn routes match 100%** — foot-hiking, cycling-mountain and a generated
  loop all run on ways our store holds, so difficulty, MTB and POIs transfer onto
  engine-drawn routes without loss. This is the finding that makes `pipeline/draw/`
  engine-agnostic.
- **The MTB conjunction is strict**: 10 of 12 candidates fail on blocked matched
  metres — sentiero 14 on **6 m** of it. Real (steps and access exist on the ground)
  but amplified by corridor matching; see the caveat at the end.
- **POI enrichment worked for every provider** (8–101 places per route), because it
  never depended on the provider.

## Licences, which decide more than the technology

- **OSM** — ODbL. Attribution + share-alike on derivative databases. Already the position
  the whole product is in, already handled (`docs/licensing.md`, attribution inside every
  route document).
- **TrailSplits** — "free for hobby projects and non-commercial use with proper
  attribution; commercial use requires outreach." VaiVia is commercial, so this is the
  **Trailforks shape again**: unusable in production without a written agreement. The
  difference is that here the underlying trail data is ODbL OSM we already hold — an
  agreement would buy us an API to our own data.
- **ORS** — the *data* in the response is OSM (ODbL); the *service* has its own free-tier
  terms and rate limits (a self-hosted instance removes those — it is open source, as is
  our already-running GraphHopper, which is the same category).
- **FreeRoute** — moot until it returns a route; no terms were reachable.

## The verdict

**The wisest combination is a division of labour, not a choice of vendor:**

1. **OSM is the backbone** — the only source that carries the metadata difficulty and the
   MTB verdict are made of, and the source every other provider resells. Routes that
   *exist* (2a) come from its relations; both are already in the store.
2. **A routing engine is the pen, for routes that don't exist yet** (2b). ORS proved the
   category live — point-to-point and round-trip, hiking and MTB profiles — and its lines
   matched our network at ~100%, so everything we know about our ground transfers onto
   whatever it draws. Self-hosting (ORS or the GraphHopper already in compose) removes the
   key, the quota and the terms; `pipeline/draw/` should treat the engine as pluggable.
3. **Enrichment is ours and provider-independent** — the spike's one structural result.
   The same 25 m corridor match dressed OSM relations, TrailSplits lines and ORS routes
   with identical difficulty/MTB/POI answers. Geometry is the interchangeable part;
   `curated.edge` is the metadata backbone.
4. **TrailSplits: reject as a data source, keep as corroboration.** It adds no data we do
   not hold, and its terms need consent we would gain nothing by seeking. (Its elevation
   tiles and snow layer are a different question for a different day.)
5. **FreeRoute: reject.** Broken endpoint, no docs, no terms. Re-probe costs one cached
   request if it ever matures.

One honest caveat from the run: the MTB conjunction is **strict by construction over a
corridor match** — a 100 m slip of footway grabbed at a road crossing can flip a 10 km
ride to "not rideable". The blocked metres are reported per route so a reader can judge;
the production answer is to run the conjunction along the *routed edge sequence* (which
`pipeline/draw/` will have) instead of a corridor, where no such slip exists.
