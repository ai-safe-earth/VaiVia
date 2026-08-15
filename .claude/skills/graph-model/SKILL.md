---
name: graph-model
description: Graph data-model and Cypher rules for the get-out-door Neo4j knowledge graph. Use when writing or reviewing Cypher queries, schema changes, ingestion code (OSM/Trailforks ETL), spatial matching, or routing/pathfinding logic.
---

Before writing Cypher or ingestion code, read the relevant doc:

- `docs/architecture.md` — full node/relationship model and schema summary
- `docs/data-sources.md` — OSM tag → property mapping, Trailforks mock format, the matching problem
- `docs/query-examples.md` — canonical Cypher patterns to imitate
- `docs/fragilities.md` — known failure modes; read before touching ingestion

## Non-negotiable rules

1. **Two sources, never merged.** OSM supplies geometry/infrastructure (`:Segment`, `:Intersection`, `:POI`); Trailforks supplies curated metadata (`:Trail`). Link them with `[:MAPS_TO]` when within `SPATIAL_MATCH_THRESHOLD_M` (default 20 m) — never `MERGE` a node from both sources, never match on coordinate equality.
2. **Trail name/difficulty only on `(:Trail)`.** Segments are fragmented, anonymous OSM ways. Query pattern: `(t:Trail {name: ...})-[:COMPOSED_OF]->(s:Segment)`.
3. **Bounded traversals only.** Never write `-[:CONNECTS_TO*]-` unbounded; cap hops (`*..100`) and spatially pre-filter start/end candidates by bounding box. For production routing, project a GDS in-memory graph and use `gds.shortestPath.dijkstra`.
4. **Idempotent ingestion.** `MERGE` on stable IDs (`osm_way_id`, Trailforks `trail_id`); re-runs must update, not duplicate. Trailforks API calls need exponential backoff on 429 and the local cache in `fixtures/trailforks_cache/`; dev/CI uses `--mock`.
5. **Graceful degradation over wrong answers.** Missing `[:MAPS_TO]` links, null `elevation_change`, or an unpopulated vector index must never silently return wrong/empty results — semantic search returns `503` with a clear message until embeddings exist.
6. **Cypher in `.cypher` files** under `graph/` (schema in `schema.cypher`, named queries in `queries.cypher`), not inline strings in Python.

When proximity matching, reduce false links between parallel trails by combining the distance threshold with `highway_type`/`surface` compatibility checks.
