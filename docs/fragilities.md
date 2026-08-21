# Fragilities & Mitigations

This document is a candid record of known failure modes, edge cases, and the pragmatic decisions made to handle them. Read this before contributing to the ingestion pipeline.

---

## 1. The Geometry Matching Problem

**The issue:** Trailforks and OSM represent the same physical trail using different coordinate sets. GPS drift between the two sources is typically 5–30 metres but can exceed 100 metres in dense forest or steep canyon terrain where satellite signal is weak.

**What breaks if you ignore it:** Attempting to merge nodes by coordinate equality will result in near-zero matches. Attempting to merge by exact geometry overlap will produce incorrect links and phantom duplicate nodes.

**Our mitigation:** Spatial proximity matching with a configurable threshold (default: 20 m). The ordered `[:COMPOSED_OF {seq, match_confidence}]` relationship records the association without claiming the nodes are identical. The threshold is exposed as `SPATIAL_MATCH_THRESHOLD_M` in `.env` so operators can tune it per region.

**Residual risk:** At 20 m, parallel trails (e.g., a hiking path and a bike trail 15 m apart) may be incorrectly linked. Mitigation: combine proximity with `highway_type` and `surface` compatibility checks before creating `[:COMPOSED_OF]`; `match_confidence` records how strong each match was.

---

## 2. OSM Segment Fragmentation

**The issue:** A single named trail in the real world often appears as dozens or hundreds of short `way` objects in OSM, each with its own ID. There is no guaranteed OSM field that groups them into a named route (OSM `relation` objects exist for this, but coverage is inconsistent).

**What breaks if you ignore it:** Queries that filter by trail name on `(:Segment)` nodes will fail because OSM segments have no trail name property.

**Our mitigation:** Trail name and identity lives exclusively on `(:Trail)` nodes sourced from Trailforks. The `[:COMPOSED_OF]` relationship is the bridge. Never filter for a trail by name on `(:Segment)`.

**Residual risk:** Trails that exist in OSM but not Trailforks (e.g., obscure local paths) will appear as orphan segments with no `(:Trail)` parent. A periodic job to flag unlinked segments is planned for Phase 2.

---

## 3. Neo4j Routing Performance

**The issue:** Neo4j's `shortestPath()` function performs well for short paths but can time out on large unbounded traversals across the full segment graph (potentially millions of `(:Intersection)` nodes).

**What breaks if you ignore it:** Queries like *"route from Station A to Hut B"* with no hop limit will scan the entire connected component.

**Our mitigation (layered):**

1. Always add a hop limit: `shortestPath(... -[:CONNECTS_TO*..100]- ...)`.
2. Use spatial pre-filtering: restrict the starting `(:Intersection)` nodes to those within a bounding box of the start and end POI before running pathfinding.
3. For production workloads, project a GDS in-memory graph and run `gds.shortestPath.dijkstra`. GDS pathfinding is orders of magnitude faster than Cypher traversal on large graphs.
4. Pre-compute popular routes as `(:CuratedRoute)` nodes during off-peak hours.

---

## 4. Trailforks Access Is Licensing-Blocked, Not Just Rate-Limited

**The issue:** Trailforks data cannot be used at all without an approved API key and, because VaiVia is commercial and AI-driven, prior written consent from Outside. Their Data Use Policy allows access only via the API; the Outside Terms of Use restrict the Services to personal, noncommercial use and separately name "development of any software program" and AI use as requiring written consent. Rate limits are a real but *secondary* concern — they only start to matter after the licensing question is answered. See `docs/licensing.md` for the quoted terms and the options.

**Current state (verified 2026-08-17):** nothing has ever been fetched from Trailforks. `ingestion/trailforks_ingest.py::fetch_live()` raises `NotImplementedError`, there is no Trailforks HTTP client, and `fixtures/trailforks_cache/` does not exist. `fixtures/trailforks_mock.json` is synthetic — hand-authored prose over geometry traced from ingested OSM ways — so it carries no Trailforks content.

**Our mitigation:** `--mock` is the only working path and must stay that way until licensing is resolved. Note that the Outside ToU also prohibits automated collection, so scraping is not a fallback.

**Not yet built** (deliberately, pending that decision): rate-limit backoff, bbox-chunked region ingestion, and the response cache. Earlier revisions of this document described all three as existing mitigations; they do not exist.

---

## 5. OSM Data Staleness

**The issue:** OSM is community-edited and changes continuously. A trail that was open last month may be rerouted or closed this month.

**Our mitigation:**

- The ingestion pipeline is designed to be re-run incrementally. Re-running `osm_ingest.py` will `MERGE` on `osm_way_id`, updating changed properties without duplicating nodes.
- A scheduled re-ingestion job (cron/Airflow) is planned for Phase 2.
- Deleted OSM ways are not automatically removed from the graph. A reconciliation step that compares current OSM IDs against graph IDs and removes orphans is on the roadmap.

---

## 6. Elevation Data Gap

**The issue:** OSM segments contain elevation data (`ele` tag) inconsistently. Many segments have no elevation at all.

**What breaks:** The `elevation_change` property on `[:CONNECTS_TO]` relationships will be `null` for segments sourced from areas with poor OSM elevation coverage, making elevation-based filtering unreliable.

**Our mitigation (planned):** Backfill elevation using the Open-Elevation API or a local SRTM DEM (Digital Elevation Model) raster, queried by segment endpoint coordinates. This is tracked as a Phase 2 task.

---

## 7. Vector Index Cold Start (Phase 3)

**The issue:** Semantic search requires that `Trail.description_embedding` is populated for all `(:Trail)` nodes. On a fresh ingestion, embeddings are absent.

**Our mitigation:** Embedding generation is a separate async job (`scripts/embed_trails.py`, Phase 3). The API will gracefully degrade — semantic search endpoints return a `503` with a clear message when the vector index is not yet populated, rather than returning empty or incorrect results.

## 9. The Routing Network Is Fragmented (ingestion excludes roads)

**The issue:** The Overpass ingestion query matches only `highway~path|track|cycleway|footway|bridleway`. Real trail networks connect *through* roads — a path ends at a lane, you walk 200 m, the next path starts. Without those ways the graph shatters into islands.

Measured on Lecco, 2026-08-17 (`scripts/check_graph_connectivity.py`):

- 15,438 intersections in **1,627 connected components**
- Largest component holds **31.7%** of the network
- The Lecco waterfront — where the frontend map opens — sits on an island of **14 intersections**
- Ingested: 11,292 ways. Excluded by the filter: **11,326 ways** (`service` 4,097, `residential` 2,839, `unclassified` 2,112, `steps` 639, `secondary` 617, `tertiary` 597, `pedestrian` 344). Roughly half the network is missing, and it is the connective half.

**Why it went unnoticed:** point-to-point routing was verified on a POI pair that happened to share a component (a 223 m route), and every trail in the mock fixture was *built* by tracing existing ways, so it was connected by construction. Nothing exercised two arbitrary points.

**Consequences:** routing between arbitrary points usually fails; loop construction fails almost entirely (`scripts/spike_loop_routes.py` routed 0/10 candidates from the Lecco waterfront, and 3/10 from inside the largest component, all at ~50% retraced — out-and-backs rather than loops). A user reading "no route found" is being told their request was unreasonable when the truth is the graph is missing its middle.

**FIXED 2026-08-17.** `WALKABLE_HIGHWAYS` in `ingestion/overpass_client.py` now also ingests `steps|pedestrian|living_street|residential|unclassified|service|tertiary|secondary`, anchored so `service` cannot match `services` nor `secondary` match `secondary_link`. `motorway|trunk|primary` stay excluded — routing a walker onto those is wrong and often illegal. Re-ingesting Lecco:

| | before | after |
|---|---|---|
| Routing edges | 31,676 | 71,593 |
| Connected components | 1,627 | **171** |
| Largest component | 31.7% | **98.1%** |
| Loops routed (10 km, from the waterfront) | 0/10 | **10/10** |
| Retraced fraction of best loop | n/a (none) | **9.3%** |

Trail-to-segment matching is unaffected: `COMPATIBLE_HIGHWAYS` in `spatial_match.py` still refuses to compose a `(:Trail)` out of residential streets.

---

## 10. Routing Optimises For Distance, So It Prefers Roads

**The issue:** With the network repaired (#9), `route_gds_dijkstra` weights purely on `distance_m`. Roads are straighter than trails, so they win almost every time. The loop spike now returns loops of the requested length whose surface mix is roughly **83% asphalt** (10 km loop: `asphalt=171` against ~205 edges). A trail app that answers "a 10 km loop" with a road walk is worse than one that answers "no route found" — the failure is now silent and plausible instead of loud.

**FIXED 2026-08-17.** `CONNECTS_TO` now carries `cost_m = distance_m * highway_penalty * surface_penalty` (`core/comfort.py`), and `route_gds_dijkstra` weights on it. Measured on the same Lecco loops:

| | distance-weighted | comfort-weighted |
|---|---|---|
| Mean off-road share (10 km) | ~17% | **61.0%** |
| Mean off-road share (20 km) | — | **64.1%** |
| Loops on target (15 km) | 6/8 | **8/8** |
| Best 20 km loop composition | mostly asphalt | path 5.2 km, footway 3.3 km, track 2.8 km, residential 2.7 km |

Route quality improved without costing success rate. Retrace rose (9.3% → ~22% on the best 10 km loop) because avoiding roads leaves fewer distinct options, but stays well under the 50% that means out-and-back.

**The trap this creates:** GDS's `totalCost` is now a penalised figure in no real unit. Every distance shown to a user must be summed from `distance_m` over `route_edge_details`. `test_reported_distance_never_comes_from_the_comfort_weight` guards this with a fixture whose cost and distance differ by 2.6x. The same change invalidated the old `smoke_routing` assertion that "Dijkstra beats shortestPath on metres" — deliberately false now — and `/routes`' over-cap branch, which used to assume the comfort route was also the shortest.

**Calibration is unfinished.** `footway` at 1.4 still dominates short loops from a town start (5.5 km of the best 10 km loop), because urban pavement is cheap and plentiful. That may be right for a lakeside stroll and wrong for a mountain ride; it wants tuning against real routes, and eventually per-activity penalties (`steps` should be near-prohibitive for MTB, not 1.8).

**Related and still open:** `elevation_gain_m` is 0 on every edge, so climb-aware routing and any "how hard is this loop" answer cannot work until fragility #6 (elevation) is addressed. That also means the difficulty tags OSM does carry (`sac_scale`, `mtb:scale` — see `docs/licensing.md`) are not yet reaching the routing graph at all.

---

## 11. Re-segmentation Orphans CONNECTS_TO Edges

**The issue:** Segment ids are `{way_id}#{piece_num}`, and pieces are cut wherever a node is shared by two or more ways. Widening the ingestion filter (#9) changed those cut points, so a way previously stored as one edge `123#0` spanning A→C became `123#0` (A→B) plus `123#1` (B→C). `MERGE_CONNECTS_TO` matches on both endpoints *and* `osm_way_id`, so it created the new A→B edge rather than updating the old A→C one — which survived, unreferenced and never rewritten.

Re-ingesting both regions left **5,489 stale edges**. They were invisible until `cost_m` was added, because a stale edge looks exactly like a fresh one apart from the properties it never received. Given the projection coalesces a missing `cost_m` to a mid-range penalty, those stale *path* edges were being priced at 2.0 instead of 1.0 — biasing routing against precisely the trails the app exists to find.

**Cleaned, not fixed.** The stale edges were deleted (`cost_m IS NULL` identified them exactly) and connectivity was unchanged before and after — 171 components, 98.1% largest — confirming they were pure redundancy. But ingestion is supposed to be idempotent, and this makes it so only while the filter is stable. A proper fix removes a way's existing `CONNECTS_TO` edges before writing its new ones. Until then, **any change to the ingestion filter or the segmentation rule must be followed by deleting orphaned edges**, and the general lesson holds for any property added to `CONNECTS_TO`: regions not re-ingested keep NULL for it, and because region bboxes overlap, a re-ingested region's projection still picks up its neighbour's stale edges.

---

## 8. Over-Constrained Filters From Unstated Preferences

**The issue:** Strict structured outputs require every intent field to be present, which pressures the model into inventing a value where the user implied none. `activity` is the sharp edge: the search template treats `mixed` trails as matching *any* activity (`$activity IS NULL OR t.activity = $activity OR t.activity = 'mixed'`), so a `mixed` **filter** is strictly narrower than null — it matches only trails tagged both, while null matches those and everything else. A model reaching for `mixed` to mean "no preference" therefore gets *fewer* results than no filter at all, often zero. Found by the golden eval: "stroller friendly path open year round" returned nothing, and "chestnut forest and gravel by the water" excluded the one trail whose description names chestnut forest, because an unstated activity had been guessed as `hike`.

**Our mitigation:** Two layers. The plan prompt states when to leave `activity` null and that `mixed` is not a stand-in for an unstated activity. Independently, `chat/composer.py::sanitize()` maps `activity="mixed"` to `None` in Python, alongside the existing scrub of vacuous `0` bounds — the boundary must not depend on model compliance. A genuine "suitable for both" ask searches slightly wider as a result, which degrades gracefully where the alternative returned nothing. The same failure shape should be suspected for any future enum filter whose "both/either" value is not a wildcard in the template.

---

## 12. A Defaulted `highway` Tag Put Lake Shores In The Routing Graph

**The issue:** POIs are now fetched with `out geom` rather than `out center`,
because an area needs its outline (see `(:POI)` in `docs/architecture.md`).
With `out geom` a lake or car-park outline arrives as a way carrying **geometry and a
node list — exactly like a path**. `osm_extract.extract` told routing ways from
POIs by shape (`"geometry" in e`), so the outlines fell through into the
routing branch, where

    highway_type=tags.get("highway", "path")

silently called them paths. **1,673 lake and parking outlines entered the
routing graph as walkable ways**, and the router was free to send a walker
across open water.

No test caught it. Every unit test passed throughout, because none fed a POI
way and a routing way through `extract` together; there is now one that does.
It surfaced only because the boundary count came back as 12 where ~1,686 was
expected, and chasing that discrepancy found it.

**Our mitigation:** The two are now told apart by **tags, not shape** — a
routing way must have a `highway` tag, an area POI must not. The mirror-image
trap is worth naming: testing for the *absence* of a node list would have
excluded every POI way, which is exactly why only the 12 relations got
boundaries on the first attempt.

Cleanup was surgical rather than a wipe. Of 2,242 suspect segments, 2,237
claimed `"path"` from the 1,673 outlines and 5 claimed `"service"` from a
single way — a genuine parking aisle that is legitimately both a road and a
POI. Deleting all 2,242 would have removed real road; only the 2,237 were
deleted, and segments returned to 104,812.

**The lesson to keep:** `tags.get("highway", "path")` is a dangerous default.
Silently naming an untagged way a path is what turned a filter bug into
routable water instead of a loud failure at ingestion. The default is now gone:
a way with no `highway` tag is logged and skipped, so the line that decides
what a segment *is* can no longer invent it. A test pins that too.

## 13. RLS Policies Without Grants Read As Working, Because The Platform Was Granting For Us

**Risk:** The browser reads `conversations` and `messages` directly from
Supabase under the select-only policies in migration `0001`. That migration
enables RLS and writes one policy per table — and never grants `select` on any
of them. It worked against the hosted project because Supabase used to expose
entities created in `public` to `anon`, `authenticated` and `service_role`
automatically.

**That default is gone.** New projects revoke by default, and the CLI's
`auto_expose_new_tables` escape hatch is documented as removed on **2026-10-30**.
So a migration that has been verified live against one project would have failed
against the next one created — most likely the production project, on the day it
was created, with:

    42501  permission denied for table conversations

which reads like a policy bug and is not one. **A policy can only narrow a
privilege that already exists**; with no grant there is nothing for it to
narrow, and the two failure modes look nothing alike from the client: a missing
policy returns an empty list, a missing grant returns an error.

It surfaced on the first run of the local stack, where the new default is
already in force — which is the argument for developing against a real local
instance rather than a bypass. `GATEWAY_DEV_NO_AUTH` would never have found it,
because with auth off the browser never reads as `authenticated` at all.

**Our mitigation:** Migration `0002_data_api_grants.sql` grants `select` to
`anon` and `authenticated` explicitly, so the privilege no longer depends on a
platform default. `anon` is included deliberately: the policies are
`auth.uid() = user_id`, so an anonymous caller matches no row and reads zero of
them — the behaviour verified against the hosted project. Confidentiality is
RLS's job; the grant only decides whether the table is addressable. Writes are
unaffected — they go through the backend, which connects as the owner.

**The lesson to keep:** when a security control is verified live and passes,
check *which layer* actually granted the access it was narrowing. A platform
default that silently does half the work is indistinguishable from a migration
that does all of it, right up until the default changes.

## 14. A Prompt Rule Naming A Forbidden Source Is An Instruction To Use It

**Risk:** The answer prompt carried a linking rule from the Trailforks era:
*"When a trail has a `trailforks_url`, cite it as a markdown link on the trail's
name. Never link a trail that has no `trailforks_url`."* Catalogue routes carry
no such field, so by the rule's own terms none of them should have been linked.

The first live smoke against the pipeline catalogue (2026-08-21) produced this:

> The loop to [Corno dell'Arco](https://www.trailforks.com) is approximately
> 11.0 km with an ascent of 1,049 m…

Every one of the five routes, linked to a bare `trailforks.com`. The rule's
negative half did not hold; naming the domain was enough to make the model
reach for it. And the routes are OSM-derived and ODbL — sending a walker to a
commercial site we have no agreement with, over data that is not theirs, is a
misattribution as well as a dead link.

**Why it matters beyond the one domain:** an invented URL about a real mountain
is worse than no URL. It looks authoritative, it survives being screenshotted,
and nothing downstream checks it.

**Our mitigation:** two layers, and the code is the one that counts.
`chat/sanitize.py` strips links from the answer *stream* — markdown links keep
their label, bare URLs go — and the prompt rule now forbids links outright
instead of describing when to write one. Stripping mid-stream is the fiddly
part, because `[Name](url)` arrives token by token and no single chunk holds
the whole link: `strip_links_stream` holds back the tail that could still grow
into one and releases it as soon as it cannot, so the answer still streams.

**The lesson to keep:** this is the Cypher boundary's rule applied to prose. A
constraint that matters is enforced in Python, not requested in a prompt — and
a prompt that mentions a forbidden thing at all is a prompt that suggests it.
