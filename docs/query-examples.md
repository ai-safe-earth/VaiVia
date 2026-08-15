# Query Examples — Cypher Cookbook

All queries run against the schema defined in [`graph/schema.cypher`](../graph/schema.cypher).

---

## Level 1 — Simple Discovery

### All trails in a region

```cypher
MATCH (t:Trail)-[:LOCATED_IN]->(:Region {name: 'Lecco'})
RETURN t.name, t.difficulty, t.total_distance
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
MATCH (t:Trail)-[:COMPOSED_OF]->(s:Segment)
WHERE point.distance(s.location, p.location) < 5000
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

Routing runs on the Intersection graph only — semantic edges like `PASSES_BY`
must never appear in a path expression. First snap each POI to its nearest
intersection, then route:

```cypher
MATCH (start:POI {name: 'Station A'}), (end:POI {name: 'Hut B'})
CALL (start) {
  MATCH (i:Intersection)
  WHERE point.distance(i.location, start.location) < 500
  RETURN i AS src ORDER BY point.distance(i.location, start.location) LIMIT 1
}
CALL (end) {
  MATCH (i:Intersection)
  WHERE point.distance(i.location, end.location) < 500
  RETURN i AS dst ORDER BY point.distance(i.location, end.location) LIMIT 1
}
MATCH path = shortestPath((src)-[:CONNECTS_TO*..100]-(dst))
WITH path, reduce(d = 0.0, r IN relationships(path) | d + r.distance) AS total_m
WHERE total_m < 20000
RETURN path, round(total_m / 1000, 2) AS total_km
```

For anything beyond small graphs, prefer the GDS Dijkstra pattern below.

### Two-day route with a hut at the midpoint

Find trails where a hut POI appears near the halfway distance. This depends on
`COMPOSED_OF.seq` — distance to the hut is the cumulative length of the
segments that *precede* the hut's segment along the trail (an unordered
`sum(s.length)` gives wrong answers).

```cypher
MATCH (t:Trail)-[c:COMPOSED_OF]->(s:Segment)-[:PASSES_BY]->(hut:POI {type: 'hut'})
WHERE t.total_distance > 20
CALL (t, c) {
  MATCH (t)-[before:COMPOSED_OF]->(prev:Segment)
  WHERE before.seq <= c.seq
  RETURN sum(prev.length) AS distance_to_hut
}
WITH t, hut, distance_to_hut
WHERE distance_to_hut > (t.total_distance * 1000 * 0.4)
  AND distance_to_hut < (t.total_distance * 1000 * 0.6)
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

Uses `COMPOSED_OF.seq` to identify the first and last segments of each trail.

```cypher
MATCH (t:Trail)-[c:COMPOSED_OF]->(:Segment)
WITH t, max(c.seq) AS last_seq
MATCH (t)-[c:COMPOSED_OF]->(s:Segment)
WHERE c.seq = 0 OR c.seq = last_seq
MATCH (station:POI {type: 'station'})
WHERE point.distance(s.location, station.location) < 500
RETURN DISTINCT t.name, t.difficulty, t.total_distance,
       collect(DISTINCT station.name) AS nearby_stations
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
