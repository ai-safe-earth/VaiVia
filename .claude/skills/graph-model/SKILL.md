---
name: graph-model
description: Graph data-model and Cypher rules for the get-out-door Neo4j knowledge graph. Use when writing or reviewing Cypher queries, schema changes, ingestion code (OSM/Trailforks ETL), spatial matching, or routing/pathfinding logic.
---

Before writing Cypher or ingestion code, read the relevant doc:

- `docs/architecture.md` — full node/relationship model and schema summary
- `docs/data-sources.md` — OSM tag → property mapping, Trailforks mock format, the matching problem
- `docs/query-examples.md` — canonical Cypher patterns to imitate
- `docs/fragilities.md` — known failure modes; read before touching ingestion
- `docs/plan.md` — ratified architecture decisions and phase roadmap

## Non-negotiable rules

1. **Two sources, never merged.** OSM supplies geometry/infrastructure (`:Segment`, `:Intersection`, `:POI`); Trailforks supplies curated metadata (`:Trail`). The single link is the ordered `(:Trail)-[:COMPOSED_OF {seq, match_confidence}]->(:Segment)`, created when within `SPATIAL_MATCH_THRESHOLD_M` (default 20 m) AND `highway_type`/`surface` are compatible. Never `MERGE` a node from both sources, never match on coordinate equality. `MAPS_TO` does not exist (dropped as redundant with `COMPOSED_OF`).
2. **Routing graph = Intersection–Intersection.** `(:Intersection)-[:CONNECTS_TO {distance, elevation_change, osm_way_id, surface, highway_type}]->(:Intersection)`. Segments are not routing vertices; semantic edges (`PASSES_BY`, `COMPOSED_OF`, `LOCATED_IN`) never appear in path expressions. GDS projects Intersection/CONNECTS_TO with `distance` as cost.
3. **Trail name/difficulty only on `(:Trail)`.** Segments are fragmented, anonymous OSM ways. Query pattern: `(t:Trail {name: ...})-[:COMPOSED_OF]->(s:Segment)`.
4. **Ordered composition.** Distance-along-trail logic (midpoint hut, start/end station) must use `COMPOSED_OF.seq` (cumulative length of segments with `seq <=` the target's); unordered `sum(s.length)` silently returns wrong answers. Start segment: `seq = 0`; end: `seq = max`.
5. **Bounded traversals only.** Never `-[:CONNECTS_TO*]-` unbounded; cap hops (`*..100`) and spatially pre-filter candidates (snap POIs to nearest `(:Intersection)` via the point index). Production routing uses `gds.shortestPath.dijkstra`.
6. **Spatial anchors:** `Intersection.location`, `POI.location`, and `Segment.location` (representative midpoint) are POINT-indexed; `Segment.coordinates` (the polyline) is NOT indexable — never use it in distance predicates. Regions link directly: `(:Trail|:POI|:Intersection)-[:LOCATED_IN]->(:Region)` with numeric bbox properties.
7. **Idempotent ingestion.** `MERGE` on stable IDs (`osm_way_id`, `osm_node_id`, Trailforks `trail_id`); re-runs must update, not duplicate. Trailforks API calls need exponential backoff on 429 and the local cache in `fixtures/trailforks_cache/`; dev/CI uses `--mock`.
8. **Graceful degradation over wrong answers.** Missing links, null `elevation_change`, or an unpopulated vector index must never silently return wrong/empty results — semantic search returns `503` with a clear message until embeddings exist.
9. **Cypher in `.cypher` files** under `backend/graph/` (schema in `schema.cypher`, named parameterized templates in `queries.cypher`), not inline strings in Python. The LLM layer selects templates by intent — it never generates Cypher.
