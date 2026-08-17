# Architecture — Graph Data Model

This document describes the Neo4j schema, node/relationship design decisions, and the two-source data strategy behind **VaiVia**.

---

## Design Philosophy

The core principle is **separation of concerns**:

- **OSM** owns geometry — it provides the physical world: paths, road surfaces, GPS coordinates, and raw POIs.
- **Trailforks** owns human meaning — it provides curated loops, difficulty ratings, named trails, and community descriptions.

Neither source is complete on its own. The graph links them without forcing a lossy merge.

---

## Node Labels

### `(:Trail)`

A curated, named trail loop sourced from Trailforks.

| Property | Type | Description |
|---|---|---|
| `id` | `STRING` | Unique Trailforks ID |
| `name` | `STRING` | Human-readable name |
| `activity` | `STRING` | `mtb` \| `hike` \| `mixed` |
| `difficulty` | `STRING` | `Easy`, `Intermediate`, `Difficult`, `Pro` |
| `difficulty_level` | `INTEGER` | 1–4 numeric mirror of `difficulty`, for range filters |
| `difficulty_notes` | `STRING` | Free text: critical points, family-aptness, hazards prose |
| `description` | `STRING` | Trailforks description (embedding source) |
| `landscape_description` | `STRING` | Landscape and path character (embedding source) |
| `total_distance_m` | `FLOAT` | Total length in **metres** |
| `elevation_gain_m` / `elevation_loss_m` | `FLOAT` | Total ascent / descent (null until backfill where source lacks it) |
| `duration_hike_min` | `INTEGER` | DIN 33466 estimate; null if activity is mtb-only |
| `duration_mtb_min` | `INTEGER` | Speed-by-difficulty + climb estimate; null if hike-only |
| `best_seasons` | `LIST<STRING>` | e.g. `['spring','summer','autumn']` |
| `seasonal_hazards` | `LIST<STRING>` | e.g. `['snow','ice','mud_after_rain']` |
| `source` | `STRING` | Always `trailforks` for this label |
| `description_embedding` | `LIST<FLOAT>` | Embedding of description + landscape + difficulty notes (Phase 3) |

---

### `(:Segment)`

A physical piece of path or road, sourced from OSM. This is the atomic routing unit.

| Property | Type | Description |
|---|---|---|
| `osm_way_id` | `STRING` | Deterministic split-piece id `"<wayId>#<n>"` (ways are split at intersections) |
| `osm_parent_way_id` | `STRING` | The raw OSM way ID |
| `length_m` | `FLOAT` | Length in metres |
| `elevation_gain_m` / `elevation_loss_m` | `FLOAT` | Along the polyline in coordinate order (null until SRTM backfill) |
| `surface` | `STRING` | `unpaved`, `gravel`, `asphalt`, `dirt`, etc. |
| `highway_type` | `STRING` | OSM `highway` tag: `path`, `track`, `cycleway` |
| `coordinates` | `LIST<POINT>` | Ordered list of WGS84 points (polyline) |
| `location` | `POINT` | Representative midpoint (spatially indexed — point lists cannot be indexed, this can) |

---

### `(:Intersection)`

A node where two or more segments meet. Used as the routing graph's vertices.

| Property | Type | Description |
|---|---|---|
| `osm_node_id` | `STRING` | OSM node ID |
| `location` | `POINT` | WGS84 spatial point (for spatial queries) |

---

### `(:POI)`

A point of interest, sourced from OSM.

| Property | Type | Description |
|---|---|---|
| `osm_id` | `STRING` | OSM node or way ID |
| `name` | `STRING` | Display name |
| `type` | `STRING` | `lake`, `hut`, `campsite`, `station`, `bathing_water`, `viewpoint` |
| `location` | `POINT` | WGS84 spatial point |

---

### `(:Region)`

A spatial bounding area (city, park, municipality) used to group trails geographically.

| Property | Type | Description |
|---|---|---|
| `name` | `STRING` | Region name |
| `min_lat` / `min_lon` / `max_lat` / `max_lon` | `FLOAT` | Numeric bounding box (queryable; a comma string is not) |

---

## Relationships

```
(:Trail)-[:COMPOSED_OF {seq: INTEGER, match_confidence: FLOAT}]->(:Segment)
```
Links a Trailforks trail to the OSM segments that make up its route, **in order**.
Created during spatial matching (proximity ≤ 20 m, plus `highway_type`/`surface`
compatibility). `seq` (0-based) makes distance-along-trail queries (e.g. "hut at
the halfway point") computable; `match_confidence` records match quality. This is
the single Trail→Segment relationship — the sources stay linked, never merged.

---

```
(:Intersection)-[:CONNECTS_TO {distance_m: FLOAT,
                               elevation_gain_m: FLOAT, elevation_loss_m: FLOAT,
                               osm_way_id: STRING, surface: STRING,
                               highway_type: STRING}]->(:Intersection)
```
The routing graph: intersections are the vertices, and each edge carries the
segment data needed for cost-based pathfinding. Both directions are
materialized (unless OSM `oneway`), and each direction carries its own
`elevation_gain_m`/`elevation_loss_m` — A→B's climb is B→A's descent — so
routing can cost real climbing effort. This is the graph GDS
projects for Dijkstra — `(:Segment)` nodes are NOT part of the routing
traversal; they exist for trail composition (`COMPOSED_OF`) and POI proximity
(`PASSES_BY`).

---

```
(:Segment)-[:PASSES_BY]->(:POI)
```
Created when a segment's geometry comes within a configurable threshold (default: 50 m) of a POI's location.

---

```
(:Trail)-[:LOCATED_IN]->(:Region)
(:POI)-[:LOCATED_IN]->(:Region)
(:Intersection)-[:LOCATED_IN]->(:Region)
```
Created at ingestion time so regional filtering is a direct hop from the nodes
users actually filter on (trails, POIs) — not a 4-hop traversal.

---

> **Note:** an earlier draft had a separate `(:Trail)-[:MAPS_TO]->(:Segment)`
> relationship alongside `COMPOSED_OF`. It was dropped: both had identical
> endpoints and creation rules. `COMPOSED_OF {seq, match_confidence}` is the
> single, ordered spatial-match link.

## Full Schema Diagram

```
              ┌──────────────┐
              │   :Region    │◀── LOCATED_IN ── (:Trail) (:POI) (:Intersection)
              └──────────────┘

  ┌──────────┐  COMPOSED_OF {seq}   ┌──────────────┐  PASSES_BY   ┌──────────┐
  │  :Trail  │ ───────────────────▶ │  :Segment    │ ───────────▶ │   :POI   │
  └──────────┘                      └──────────────┘              └──────────┘

  Routing graph (GDS projection):
  ┌──────────────┐  CONNECTS_TO {distance, elevation_change, osm_way_id}
  │:Intersection │ ─────────────────────────────────────────▶ ┌──────────────┐
  └──────────────┘                                            │:Intersection │
                                                              └──────────────┘
```

---

## Indexes & Constraints

Defined in [`graph/schema.cypher`](../graph/schema.cypher). Summary:

| Target | Type | Purpose |
|---|---|---|
| `Trail.id` | Uniqueness constraint | Deduplication on re-ingestion |
| `Segment.osm_way_id` | Uniqueness constraint | Deduplication |
| `Intersection.osm_node_id` | Uniqueness constraint | Deduplication |
| `POI.osm_id` | Uniqueness constraint | Deduplication |
| `Intersection.location` | Spatial point index | `distance()` queries |
| `POI.location` | Spatial point index | `distance()` queries |
| `Segment.location` | Spatial point index | "trails near X" queries |
| `Trail.description_embedding` | Vector index (1536-dim) | Semantic search (Phase 3) |
| `Trail.difficulty_level`, `Trail.activity` | Range index | Filter queries |
| `Trail.total_distance_m`, `Trail.duration_hike_min`, `Trail.duration_mtb_min`, `Trail.elevation_gain_m` | Range index | Distance/time/effort range filters |
| `POI.type` | Range index | Filter queries |

---

## Routing Strategy

Neo4j's graph traversal excels at semantic multi-hop queries (Trail → Segment → POI). For pure shortest-path routing on the full segment graph, two approaches are available:

1. **Neo4j GDS (Graph Data Science)** — `gds.shortestPath.dijkstra` projected over `(:Intersection)-[:CONNECTS_TO]->(:Intersection)` with `distance` as cost property. Best for on-demand queries.

2. **Pre-computed `(:CuratedRoute)` nodes** — For common loops, run GDS offline and store results as a node. Query becomes a simple lookup. Best for performance-critical endpoints.

See [`docs/query-examples.md`](query-examples.md) for GDS Cypher patterns.
