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

## 8. Over-Constrained Filters From Unstated Preferences

**The issue:** Strict structured outputs require every intent field to be present, which pressures the model into inventing a value where the user implied none. `activity` is the sharp edge: the search template treats `mixed` trails as matching *any* activity (`$activity IS NULL OR t.activity = $activity OR t.activity = 'mixed'`), so a `mixed` **filter** is strictly narrower than null — it matches only trails tagged both, while null matches those and everything else. A model reaching for `mixed` to mean "no preference" therefore gets *fewer* results than no filter at all, often zero. Found by the golden eval: "stroller friendly path open year round" returned nothing, and "chestnut forest and gravel by the water" excluded the one trail whose description names chestnut forest, because an unstated activity had been guessed as `hike`.

**Our mitigation:** Two layers. The plan prompt states when to leave `activity` null and that `mixed` is not a stand-in for an unstated activity. Independently, `chat/composer.py::sanitize()` maps `activity="mixed"` to `None` in Python, alongside the existing scrub of vacuous `0` bounds — the boundary must not depend on model compliance. A genuine "suitable for both" ask searches slightly wider as a result, which degrades gracefully where the alternative returned nothing. The same failure shape should be suspected for any future enum filter whose "both/either" value is not a wildcard in the template.
