# Architecture — Graph Data Model

This document describes the Neo4j schema, node/relationship design decisions, and the two-source data strategy behind **get-out-door**.

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
| `total_distance` | `FLOAT` | Total length in km |
| `difficulty` | `STRING` | `Easy`, `Intermediate`, `Difficult`, `Pro` |
| `source` | `STRING` | Always `trailforks` for this label |
| `description_embedding` | `LIST<FLOAT>` | Vector embedding of the trail description (Phase 3) |

---

### `(:Segment)`

A physical piece of path or road, sourced from OSM. This is the atomic routing unit.

| Property | Type | Description |
|---|---|---|
| `osm_way_id` | `STRING` | OSM way ID |
| `length` | `FLOAT` | Length in metres |
| `surface` | `STRING` | `unpaved`, `gravel`, `asphalt`, `dirt`, etc. |
| `highway_type` | `STRING` | OSM `highway` tag: `path`, `track`, `cycleway` |
| `coordinates` | `LIST<POINT>` | Ordered list of WGS84 points (polyline) |

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
| `bbox` | `STRING` | `"minLat,minLon,maxLat,maxLon"` |

---

## Relationships

```
(:Trail)-[:COMPOSED_OF]->(:Segment)
```
Links a Trailforks trail to the OSM segments that make up its route.
Created during spatial matching (proximity ≤ 20 m).

---

```
(:Segment)-[:CONNECTS_TO {distance: FLOAT, elevation_change: FLOAT}]->(:Intersection)
```
Directional edge from a segment to an intersection node.
`distance` is in metres; `elevation_change` is signed (positive = uphill).

---

```
(:Segment)-[:PASSES_BY]->(:POI)
```
Created when a segment's geometry comes within a configurable threshold (default: 50 m) of a POI's location.

---

```
(:Intersection)-[:LOCATED_IN]->(:Region)
```
Assigns an intersection to its containing region, enabling regional filtering.

---

```
(:Trail)-[:MAPS_TO]->(:Segment)
```
The pragmatic link. When Trailforks geometry cannot be cleanly merged with OSM geometry, this relationship records the spatial association rather than forcing a merge.

---

## Full Schema Diagram

```
                     ┌──────────────┐
                     │   :Region    │
                     └──────┬───────┘
                            │ LOCATED_IN ▲
                     ┌──────┴───────┐
                     │ :Intersection│
                     └──────┬───────┘
              CONNECTS_TO ▲ │ ▼ CONNECTS_TO
                     ┌──────┴───────┐
              ┌──────│  :Segment    │──────┐
              │      └──────────────┘      │
  COMPOSED_OF │             │ PASSES_BY    │ MAPS_TO
              │      ┌──────┴───────┐      │
              │      │    :POI      │      │
              │      └──────────────┘      │
              ▼                            ▼
        ┌──────────┐               ┌──────────┐
        │  :Trail  │               │  :Trail  │
        └──────────┘               └──────────┘
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
| `Trail.description_embedding` | Vector index (1536-dim) | Semantic search (Phase 3) |
| `Trail.difficulty` | B-tree index | Filter queries |
| `POI.type` | B-tree index | Filter queries |

---

## Routing Strategy

Neo4j's graph traversal excels at semantic multi-hop queries (Trail → Segment → POI). For pure shortest-path routing on the full segment graph, two approaches are available:

1. **Neo4j GDS (Graph Data Science)** — `gds.shortestPath.dijkstra` projected over `(:Intersection)-[:CONNECTS_TO]->(:Intersection)` with `distance` as cost property. Best for on-demand queries.

2. **Pre-computed `(:CuratedRoute)` nodes** — For common loops, run GDS offline and store results as a node. Query becomes a simple lookup. Best for performance-critical endpoints.

See [`docs/query-examples.md`](query-examples.md) for GDS Cypher patterns.
