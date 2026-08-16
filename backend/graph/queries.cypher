// Named, parameterized query templates. Loaded by graph/query_loader.py.
//
// Format: each template starts with a "// name: <template_name>" line and ends
// at the next such line. Templates take ONLY parameters — never string
// interpolation — because the chat layer (Phase 4) selects a template by
// validated intent and supplies parameters. The LLM never writes Cypher.
//
// Optional filters use the ($param IS NULL OR ...) idiom so one template covers
// many intents; list filters are no-ops when the list is empty.
// All distances are metres, durations minutes (see schema.cypher).

// name: search_trails
MATCH (t:Trail)
WHERE ($activity IS NULL OR t.activity = $activity OR t.activity = 'mixed')
  AND ($max_difficulty_level IS NULL OR t.difficulty_level <= $max_difficulty_level)
  AND ($min_difficulty_level IS NULL OR t.difficulty_level >= $min_difficulty_level)
  AND ($min_distance_m IS NULL OR t.total_distance_m >= $min_distance_m)
  AND ($max_distance_m IS NULL OR t.total_distance_m <= $max_distance_m)
  AND ($min_elevation_gain_m IS NULL OR t.elevation_gain_m >= $min_elevation_gain_m)
  AND ($max_elevation_gain_m IS NULL OR t.elevation_gain_m <= $max_elevation_gain_m)
  AND ($season IS NULL OR $season IN t.best_seasons)
  AND (size($exclude_hazards) = 0
       OR none(h IN t.seasonal_hazards WHERE h IN $exclude_hazards))
  AND (size($surface_exclusions) = 0
       OR NOT EXISTS {
            MATCH (t)-[:COMPOSED_OF]->(x:Segment)
            WHERE x.surface IN $surface_exclusions
          })
  AND ($region IS NULL
       OR EXISTS { MATCH (t)-[:LOCATED_IN]->(:Region {name: $region}) })
CALL (t) {
  OPTIONAL MATCH (t)-[:COMPOSED_OF]->(:Segment)-[:PASSES_BY]->(p1:POI)
  WITH t, collect(DISTINCT p1) AS direct
  OPTIONAL MATCH (t)-[:NEAR_POI]->(p2:POI)
  WITH direct, collect(DISTINCT p2) AS near
  WITH direct + near AS all_pois
  UNWIND all_pois AS p
  WITH DISTINCT p
  WHERE size($poi_types) = 0 OR p.type IN $poi_types
  RETURN collect(p.type) AS found_types,
         collect({name: p.name, type: p.type}) AS pois
}
WITH t, found_types, pois
WHERE size($poi_types) = 0
   OR all(wanted IN $poi_types WHERE wanted IN found_types)
RETURN t.id AS id, t.name AS name, t.activity AS activity,
       t.difficulty AS difficulty, t.difficulty_level AS difficulty_level,
       t.difficulty_notes AS difficulty_notes,
       t.landscape_description AS landscape_description,
       t.total_distance_m AS total_distance_m,
       t.elevation_gain_m AS elevation_gain_m,
       t.elevation_loss_m AS elevation_loss_m,
       t.duration_hike_min AS duration_hike_min,
       t.duration_mtb_min AS duration_mtb_min,
       t.best_seasons AS best_seasons,
       t.seasonal_hazards AS seasonal_hazards,
       t.trailforks_url AS trailforks_url,
       pois AS pois
ORDER BY t.total_distance_m ASC
LIMIT $limit

// name: trail_by_id
MATCH (t:Trail {id: $trail_id})
CALL (t) {
  OPTIONAL MATCH (t)-[:COMPOSED_OF]->(:Segment)-[:PASSES_BY]->(p1:POI)
  WITH t, collect(DISTINCT p1) AS direct
  OPTIONAL MATCH (t)-[:NEAR_POI]->(p2:POI)
  WITH direct, collect(DISTINCT p2) AS near
  WITH direct + near AS all_pois
  UNWIND all_pois AS p
  WITH DISTINCT p
  RETURN collect({name: p.name, type: p.type}) AS pois
}
RETURN t.id AS id, t.name AS name, t.activity AS activity,
       t.difficulty AS difficulty, t.difficulty_level AS difficulty_level,
       t.difficulty_notes AS difficulty_notes,
       t.description AS description,
       t.landscape_description AS landscape_description,
       t.total_distance_m AS total_distance_m,
       t.elevation_gain_m AS elevation_gain_m,
       t.elevation_loss_m AS elevation_loss_m,
       t.duration_hike_min AS duration_hike_min,
       t.duration_mtb_min AS duration_mtb_min,
       t.best_seasons AS best_seasons,
       t.seasonal_hazards AS seasonal_hazards,
       t.trailforks_url AS trailforks_url,
       pois AS pois

// name: trail_geometry
// Segment polylines in trail order — the map payload for a trail.
MATCH (t:Trail {id: $trail_id})-[c:COMPOSED_OF]->(s:Segment)
RETURN c.seq AS seq,
       c.match_confidence AS match_confidence,
       s.osm_way_id AS osm_way_id,
       s.surface AS surface,
       s.length_m AS length_m,
       [p IN s.coordinates | [p.longitude, p.latitude]] AS coordinates
ORDER BY c.seq ASC

// name: nearest_intersection
// Snap a coordinate to the routing graph. Spatially pre-filtered by the point
// index so it never scans the full intersection set.
MATCH (i:Intersection)
WHERE point.distance(i.location, point({latitude: $lat, longitude: $lon}))
      < $radius_m
RETURN i.osm_node_id AS osm_node_id,
       point.distance(i.location, point({latitude: $lat, longitude: $lon}))
         AS distance_m
ORDER BY distance_m ASC
LIMIT 1

// name: poi_by_name_fulltext
// Preferred place-name lookup: Lucene-backed, ranked by relevance. The caller
// escapes Lucene syntax (core/text.py) so user text is only ever search terms.
// Falls back to poi_by_name (CONTAINS) when this yields nothing.
CALL db.index.fulltext.queryNodes('poi_name_fulltext', $query)
YIELD node AS p, score
RETURN p.osm_id AS osm_id, p.name AS name, p.type AS type,
       p.location.latitude AS lat, p.location.longitude AS lon
ORDER BY score DESC
LIMIT $limit

// name: poi_by_name
MATCH (p:POI)
WHERE toLower(p.name) CONTAINS toLower($name)
RETURN p.osm_id AS osm_id, p.name AS name, p.type AS type,
       p.location.latitude AS lat, p.location.longitude AS lon
ORDER BY size(p.name) ASC
LIMIT $limit

// name: route_between_intersections
// Bounded shortest path on the Intersection routing graph. Semantic edges never
// appear in the path expression. For large graphs use route_gds_dijkstra below
// (needs a live GDS instance to verify — see docs/plan.md Phase 2).
MATCH (src:Intersection {osm_node_id: $start_node}),
      (dst:Intersection {osm_node_id: $end_node})
MATCH path = shortestPath((src)-[:CONNECTS_TO*..100]-(dst))
WITH path,
     reduce(d = 0.0, r IN relationships(path) | d + r.distance_m) AS total_m,
     reduce(g = 0.0, r IN relationships(path) |
            g + coalesce(r.elevation_gain_m, 0.0)) AS gain_m
WHERE $max_distance_m IS NULL OR total_m <= $max_distance_m
RETURN total_m,
       gain_m,
       [n IN nodes(path) |
         [n.location.longitude, n.location.latitude]] AS coordinates,
       [r IN relationships(path) | r.osm_way_id] AS osm_way_ids,
       [r IN relationships(path) | r.surface] AS surfaces

// name: count_embedded_trails
// Gate for semantic search: the endpoint returns 503 while this is zero
// (unpopulated index must never masquerade as "no results" — CLAUDE.md).
MATCH (t:Trail)
RETURN count(t) AS trails,
       count(t.description_embedding) AS embedded

// name: semantic_search_trails
// Vector similarity over the trail_embeddings index. The embedding comes from
// the caller (the endpoint embeds the user's query); nothing here builds
// Cypher from user text. Same summary shape as search_trails, plus score.
CALL db.index.vector.queryNodes('trail_embeddings', $limit, $embedding)
YIELD node AS t, score
CALL (t) {
  OPTIONAL MATCH (t)-[:COMPOSED_OF]->(:Segment)-[:PASSES_BY]->(p1:POI)
  WITH t, collect(DISTINCT p1) AS direct
  OPTIONAL MATCH (t)-[:NEAR_POI]->(p2:POI)
  WITH direct, collect(DISTINCT p2) AS near
  WITH direct + near AS all_pois
  UNWIND all_pois AS p
  WITH DISTINCT p
  RETURN collect({name: p.name, type: p.type}) AS pois
}
RETURN t.id AS id, t.name AS name, t.activity AS activity,
       t.difficulty AS difficulty, t.difficulty_level AS difficulty_level,
       t.difficulty_notes AS difficulty_notes,
       t.landscape_description AS landscape_description,
       t.total_distance_m AS total_distance_m,
       t.elevation_gain_m AS elevation_gain_m,
       t.elevation_loss_m AS elevation_loss_m,
       t.duration_hike_min AS duration_hike_min,
       t.duration_mtb_min AS duration_mtb_min,
       t.best_seasons AS best_seasons,
       t.seasonal_hazards AS seasonal_hazards,
       t.trailforks_url AS trailforks_url,
       pois AS pois,
       score AS score
ORDER BY score DESC

// name: semantic_search_trails_filtered
// The composer's combined query: vector similarity ranks a candidate pool,
// then the same NULL-idiom structured filters as search_trails cut it down.
// The embedding arrives as a parameter (the chat layer embeds the theme);
// nothing here builds Cypher from user text.
CALL db.index.vector.queryNodes('trail_embeddings', $candidate_pool, $embedding)
YIELD node AS t, score
WHERE ($activity IS NULL OR t.activity = $activity OR t.activity = 'mixed')
  AND ($max_difficulty_level IS NULL OR t.difficulty_level <= $max_difficulty_level)
  AND ($min_difficulty_level IS NULL OR t.difficulty_level >= $min_difficulty_level)
  AND ($min_distance_m IS NULL OR t.total_distance_m >= $min_distance_m)
  AND ($max_distance_m IS NULL OR t.total_distance_m <= $max_distance_m)
  AND ($min_elevation_gain_m IS NULL OR t.elevation_gain_m >= $min_elevation_gain_m)
  AND ($max_elevation_gain_m IS NULL OR t.elevation_gain_m <= $max_elevation_gain_m)
  AND ($season IS NULL OR $season IN t.best_seasons)
  AND (size($exclude_hazards) = 0
       OR none(h IN t.seasonal_hazards WHERE h IN $exclude_hazards))
  AND (size($surface_exclusions) = 0
       OR NOT EXISTS {
            MATCH (t)-[:COMPOSED_OF]->(x:Segment)
            WHERE x.surface IN $surface_exclusions
          })
  AND ($region IS NULL
       OR EXISTS { MATCH (t)-[:LOCATED_IN]->(:Region {name: $region}) })
CALL (t) {
  OPTIONAL MATCH (t)-[:COMPOSED_OF]->(:Segment)-[:PASSES_BY]->(p1:POI)
  WITH t, collect(DISTINCT p1) AS direct
  OPTIONAL MATCH (t)-[:NEAR_POI]->(p2:POI)
  WITH direct, collect(DISTINCT p2) AS near
  WITH direct + near AS all_pois
  UNWIND all_pois AS p
  WITH DISTINCT p
  WHERE size($poi_types) = 0 OR p.type IN $poi_types
  RETURN collect(p.type) AS found_types,
         collect({name: p.name, type: p.type}) AS pois
}
WITH t, score, found_types, pois
WHERE size($poi_types) = 0
   OR all(wanted IN $poi_types WHERE wanted IN found_types)
RETURN t.id AS id, t.name AS name, t.activity AS activity,
       t.difficulty AS difficulty, t.difficulty_level AS difficulty_level,
       t.difficulty_notes AS difficulty_notes,
       t.landscape_description AS landscape_description,
       t.total_distance_m AS total_distance_m,
       t.elevation_gain_m AS elevation_gain_m,
       t.elevation_loss_m AS elevation_loss_m,
       t.duration_hike_min AS duration_hike_min,
       t.duration_mtb_min AS duration_mtb_min,
       t.best_seasons AS best_seasons,
       t.seasonal_hazards AS seasonal_hazards,
       t.trailforks_url AS trailforks_url,
       pois AS pois,
       score AS score
ORDER BY score DESC
LIMIT $limit

// name: route_edge_details
// Map a computed path's consecutive node pairs back onto CONNECTS_TO edges to
// recover per-edge properties — GDS streams node ids only. Parallel edges
// between the same pair (two ways joining the same intersections) resolve to
// the shortest, matching what Dijkstra weighted by.
UNWIND range(0, size($node_ids) - 2) AS i
MATCH (a:Intersection)-[c:CONNECTS_TO]->(b:Intersection)
WHERE a.osm_node_id = $node_ids[i] AND b.osm_node_id = $node_ids[i + 1]
WITH i, c ORDER BY i, c.distance_m
WITH i, collect(c)[0] AS edge
RETURN i,
       edge.osm_way_id AS osm_way_id,
       edge.surface AS surface,
       coalesce(edge.elevation_gain_m, 0.0) AS gain_m
ORDER BY i

// name: route_gds_dijkstra
// Cost-weighted routing over a named GDS projection. The projection is created
// by graph_project_routing (below) and dropped after use.
MATCH (src:Intersection {osm_node_id: $start_node}),
      (dst:Intersection {osm_node_id: $end_node})
CALL gds.shortestPath.dijkstra.stream($graph_name, {
  sourceNode: src,
  targetNode: dst,
  relationshipWeightProperty: 'distance_m'
})
YIELD totalCost, nodeIds
RETURN totalCost AS total_m,
       [nodeId IN nodeIds |
         [gds.util.asNode(nodeId).location.longitude,
          gds.util.asNode(nodeId).location.latitude]] AS coordinates,
       [nodeId IN nodeIds | gds.util.asNode(nodeId).osm_node_id] AS node_ids

// name: graph_project_routing
// Bounded projection: only intersections inside the query bbox are projected,
// so Dijkstra never sees the whole country.
MATCH (source:Intersection)-[r:CONNECTS_TO]->(target:Intersection)
WHERE source.location.latitude >= $min_lat
  AND source.location.latitude <= $max_lat
  AND source.location.longitude >= $min_lon
  AND source.location.longitude <= $max_lon
WITH gds.graph.project($graph_name, source, target,
  {relationshipProperties: r {.distance_m}}) AS g
RETURN g.graphName AS graph_name, g.nodeCount AS nodes, g.relationshipCount AS rels

// name: graph_drop_routing
CALL gds.graph.drop($graph_name, false) YIELD graphName
RETURN graphName

// name: healthcheck
RETURN 1 AS ok

// name: graph_counts
// Ingestion smoke check: re-running ingestion must not change these numbers.
MATCH (t:Trail) WITH count(t) AS trails
MATCH (s:Segment) WITH trails, count(s) AS segments
MATCH (i:Intersection) WITH trails, segments, count(i) AS intersections
MATCH (p:POI) WITH trails, segments, intersections, count(p) AS pois
MATCH ()-[c:CONNECTS_TO]->() WITH trails, segments, intersections, pois,
     count(c) AS connects_to
MATCH ()-[co:COMPOSED_OF]->()
RETURN trails, segments, intersections, pois, connects_to,
       count(co) AS composed_of
