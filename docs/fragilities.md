# Fragilities & Mitigations

This document is a candid record of known failure modes, edge cases, and the pragmatic decisions made to handle them. Read this before contributing to the ingestion pipeline.

---

## 1. The Geometry Matching Problem

**The issue:** Trailforks and OSM represent the same physical trail using different coordinate sets. GPS drift between the two sources is typically 5–30 metres but can exceed 100 metres in dense forest or steep canyon terrain where satellite signal is weak.

**What breaks if you ignore it:** Attempting to merge nodes by coordinate equality will result in near-zero matches. Attempting to merge by exact geometry overlap will produce incorrect links and phantom duplicate nodes.

**Our mitigation:** Spatial proximity matching with a configurable threshold (default: 20 m). The `[:MAPS_TO]` relationship records the association without claiming the nodes are identical. The threshold is exposed as `SPATIAL_MATCH_THRESHOLD_M` in `.env` so operators can tune it per region.

**Residual risk:** At 20 m, parallel trails (e.g., a hiking path and a bike trail 15 m apart) may be incorrectly linked. Mitigation: combine proximity with `highway_type` and `surface` compatibility checks before creating `[:MAPS_TO]`.

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

## 4. Trailforks API Rate Limits

**The issue:** The Trailforks API enforces rate limits. Bulk ingestion of a large region (e.g., all of Northern Italy) can exhaust the daily quota in a single run.

**Our mitigation:**

- The ingestion script uses exponential backoff on 429 responses.
- Region ingestion is chunked by bounding box grid cells with configurable cell size.
- A local cache layer (`fixtures/trailforks_cache/`) stores raw API responses so re-runs do not re-fetch unchanged data.
- The `--mock` flag bypasses the API entirely for development.

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
