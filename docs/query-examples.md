# Query Examples — Cypher Cookbook

All queries run against the schema defined in [`graph/schema.cypher`](../graph/schema.cypher).

---

## Level 1 — Simple Discovery

### All trails in a region

```cypher
MATCH (t:Trail)-[:COMPOSED_OF]->(s:Segment)-[:CONNECTS_TO]->(i:Intersection)-[:LOCATED_IN]->(r:Region {name: 'Lecco'})
RETURN DISTINCT t.name, t.difficulty, t.total_distance
ORDER BY t.total_distance ASC
```

### Trail details by name

```cypher
MATCH (t:Trail {name: 'Lago Loop'})
RETURN t.name, t.difficulty, t.total_distance, t.source
```

### All easy trails

```cypher
MATCH (t:Trail {difficulty: 'Easy'})
RETURN t.name, t.total_distance
ORDER BY t.total_distance ASC
```

---

## Level 2 — Compound Constraints

### Easy trail that passes a lake or swimming spot

```cypher
MATCH (t:Trail {difficulty: 'Easy'})-[:COMPOSED_OF]->(s:Segment)-[:PASSES_BY]->(p:POI)
WHERE p.type IN ['lake', 'water', 'bathing_water']
RETURN t.name, t.total_distance, collect(DISTINCT p.name) AS water_pois
ORDER BY t.total_distance ASC
```

### Trail that passes a swimming area AND has a hut nearby

```cypher
MATCH (t:Trail)-[:COMPOSED_OF]->(s1:Segment)-[:PASSES_BY]->(swim:POI {type: 'bathing_water'})
MATCH (t)-[:COMPOSED_OF]->(s2:Segment)-[:PASSES_BY]->(hut:POI {type: 'hut'})
RETURN t.name, t.difficulty, swim.name AS swim_spot, hut.name AS overnight_hut
```

### Trails near a specific POI (within 5 km)

```cypher
MATCH (p:POI {name: 'Lake Como'})
MATCH (t:Trail)-[:COMPOSED_OF]->(s:Segment)-[:CONNECTS_TO]->(i:Intersection)
WHERE point.distance(i.location, p.location) < 5000
RETURN DISTINCT t.name, t.difficulty, t.total_distance
ORDER BY t.total_distance ASC
```

### Approximate 2-hour mountain bike route

Assumes average MTB speed of ~15 km/h. A 2-hour ride ≈ 30 km.

```cypher
MATCH (t:Trail)
WHERE t.difficulty IN ['Intermediate', 'Difficult']
  AND t.total_distance >= 25
  AND t.total_distance <= 35
RETURN t.name, t.difficulty, t.total_distance,
       round(t.total_distance / 15.0, 1) AS estimated_hours
ORDER BY abs(t.total_distance - 30) ASC
LIMIT 10
```

---

## Level 3 — Complex Routing & Multi-Day

### Route between two POIs under 20 km (shortest path)

```cypher
MATCH (start:POI {name: 'Station A'}), (end:POI {name: 'Hut B'})
MATCH path = shortestPath(
  (start)-[:CONNECTS_TO|PASSES_BY*..50]-(end)
)
WITH path, reduce(d = 0.0, r IN relationships(path) | d + coalesce(r.distance, 0)) AS total_m
WHERE total_m < 20000
RETURN path, round(total_m / 1000, 2) AS total_km
ORDER BY total_km ASC
LIMIT 5
```

### Two-day route with a hut at the midpoint

Find trails where a hut POI appears near the halfway distance.

```cypher
MATCH (t:Trail)-[:COMPOSED_OF]->(s:Segment)-[:PASSES_BY]->(hut:POI {type: 'hut'})
WITH t, hut, sum(s.length) AS distance_to_hut
WHERE distance_to_hut > (t.total_distance * 1000 * 0.4)
  AND distance_to_hut < (t.total_distance * 1000 * 0.6)
  AND t.total_distance > 20
RETURN t.name, t.difficulty, t.total_distance,
       hut.name AS midpoint_hut,
       round(distance_to_hut / 1000, 1) AS km_to_hut
ORDER BY t.total_distance DESC
LIMIT 10
```

### Avoid paved roads (surface filter)

```cypher
MATCH (t:Trail)-[:COMPOSED_OF]->(s:Segment)
WITH t, collect(s.surface) AS surfaces
WHERE NONE(surf IN surfaces WHERE surf IN ['asphalt', 'paved', 'concrete'])
RETURN t.name, t.difficulty, t.total_distance
```

### Trail with a train station at start or end

```cypher
MATCH (t:Trail)-[:COMPOSED_OF]->(s:Segment)-[:CONNECTS_TO]->(i:Intersection)
MATCH (station:POI {type: 'station'})
WHERE point.distance(i.location, station.location) < 500
WITH t, collect(DISTINCT station.name) AS nearby_stations, i
// Only keep trails where the intersection is close to the start or end of the trail
RETURN DISTINCT t.name, t.difficulty, t.total_distance, nearby_stations
```

---

## Using Neo4j GDS for Pathfinding

For large-scale routing, project a GDS in-memory graph and run Dijkstra.

```cypher
// 1. Create the in-memory projection
CALL gds.graph.project(
  'trail-routing',
  'Intersection',
  {
    CONNECTS_TO: {
      type: 'CONNECTS_TO',
      orientation: 'UNDIRECTED',
      properties: ['distance']
    }
  }
)

// 2. Run shortest path
MATCH (source:Intersection {osm_node_id: '12345'}),
      (target:Intersection {osm_node_id: '67890'})
CALL gds.shortestPath.dijkstra.stream('trail-routing', {
  sourceNode: source,
  targetNode: target,
  relationshipWeightProperty: 'distance'
})
YIELD index, sourceNode, targetNode, totalCost, nodeIds, costs
RETURN
  [nodeId IN nodeIds | gds.util.asNode(nodeId).osm_node_id] AS path_node_ids,
  totalCost AS total_distance_m
```

---

## Semantic Search (Phase 3 — Vector)

Once description embeddings are populated:

```cypher
WITH genai.vector.encode('muddy after rain, technical roots', 'OpenAI', {token: $openai_key}) AS query_embedding
CALL db.index.vector.queryNodes('trail-embeddings', 5, query_embedding)
YIELD node AS t, score
RETURN t.name, t.difficulty, t.total_distance, score
ORDER BY score DESC
```
