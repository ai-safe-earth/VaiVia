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

**Not yet fixed.** The shape of the fix is a comfort cost rather than raw distance: store a `cost_m` on `CONNECTS_TO` equal to `distance_m` multiplied by a per-`highway_type`/`surface` penalty (path and track cheap, residential dearer, secondary dearest), and point the GDS projection's `relationshipWeightProperty` at it. Everything needed is already on the edge — `highway_type` and `surface` are stored at ingestion. Reported distances must keep using `distance_m`; only the routing weight changes, or the app will quote inflated lengths to users.

**Related and still open:** `elevation_gain_m` came back as 0 on every edge in both spike runs, so climb-aware routing and any "how hard is this loop" answer cannot work until fragility #6 (elevation) is addressed. That also means the difficulty tags OSM does carry (`sac_scale`, `mtb:scale` — see `docs/licensing.md`) are not yet reaching the routing graph at all.

---

## 8. Over-Constrained Filters From Unstated Preferences

**The issue:** Strict structured outputs require every intent field to be present, which pressures the model into inventing a value where the user implied none. `activity` is the sharp edge: the search template treats `mixed` trails as matching *any* activity (`$activity IS NULL OR t.activity = $activity OR t.activity = 'mixed'`), so a `mixed` **filter** is strictly narrower than null — it matches only trails tagged both, while null matches those and everything else. A model reaching for `mixed` to mean "no preference" therefore gets *fewer* results than no filter at all, often zero. Found by the golden eval: "stroller friendly path open year round" returned nothing, and "chestnut forest and gravel by the water" excluded the one trail whose description names chestnut forest, because an unstated activity had been guessed as `hike`.

**Our mitigation:** Two layers. The plan prompt states when to leave `activity` null and that `mixed` is not a stand-in for an unstated activity. Independently, `chat/composer.py::sanitize()` maps `activity="mixed"` to `None` in Python, alongside the existing scrub of vacuous `0` bounds — the boundary must not depend on model compliance. A genuine "suitable for both" ask searches slightly wider as a result, which degrades gracefully where the alternative returned nothing. The same failure shape should be suspected for any future enum filter whose "both/either" value is not a wildcard in the template.
