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
       OR none(h IN (CASE $season
                       WHEN 'spring' THEN coalesce(t.hazards_spring, t.seasonal_hazards)
                       WHEN 'summer' THEN coalesce(t.hazards_summer, t.seasonal_hazards)
                       WHEN 'autumn' THEN coalesce(t.hazards_autumn, t.seasonal_hazards)
                       WHEN 'winter' THEN coalesce(t.hazards_winter, t.seasonal_hazards)
                       ELSE t.seasonal_hazards END)
               WHERE h IN $exclude_hazards))
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
       OR none(h IN (CASE $season
                       WHEN 'spring' THEN coalesce(t.hazards_spring, t.seasonal_hazards)
                       WHEN 'summer' THEN coalesce(t.hazards_summer, t.seasonal_hazards)
                       WHEN 'autumn' THEN coalesce(t.hazards_autumn, t.seasonal_hazards)
                       WHEN 'winter' THEN coalesce(t.hazards_winter, t.seasonal_hazards)
                       ELSE t.seasonal_hazards END)
               WHERE h IN $exclude_hazards))
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
WITH i, c ORDER BY i, c.cost_m
WITH i, collect(c)[0] AS edge
RETURN i,
       edge.osm_way_id AS osm_way_id,
       edge.surface AS surface,
       edge.highway_type AS highway_type,
       edge.distance_m AS distance_m,
       coalesce(edge.elevation_gain_m, 0.0) AS gain_m
ORDER BY i

// name: route_gds_dijkstra
// Comfort-weighted routing over a named GDS projection. The projection is
// created by graph_project_routing (below) and dropped after use.
//
// Weighted on cost_m, NOT distance_m: roads are straighter than trails, so
// minimising raw distance returns road walks (docs/fragilities.md #10).
// totalCost is therefore a penalised cost in no real unit — it is returned as
// total_cost and must never be shown to a user or treated as a length. Real
// distance comes from summing distance_m over route_edge_details.
MATCH (src:Intersection {osm_node_id: $start_node}),
      (dst:Intersection {osm_node_id: $end_node})
CALL gds.shortestPath.dijkstra.stream($graph_name, {
  sourceNode: src,
  targetNode: dst,
  relationshipWeightProperty: 'cost_m'
})
YIELD totalCost, nodeIds
RETURN totalCost AS total_cost,
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
// cost_m is coalesced, not read raw: regions ingested before a property is
// added keep NULL for it, and neighbouring region bboxes overlap, so a
// re-ingested region's projection can still pick up stale edges from one that
// was not. A missing weight is worse than a wrong one — it can read as zero and
// make exactly the untreated edges look free. The fallback is a mid-range
// penalty, matching DEFAULT_HIGHWAY_PENALTY in core/comfort.py.
WITH gds.graph.project($graph_name, source, target,
  {relationshipProperties: r {
     .distance_m,
     cost_m: coalesce(r.cost_m, r.distance_m * 2.0)
   }}) AS g
RETURN g.graphName AS graph_name, g.nodeCount AS nodes, g.relationshipCount AS rels

// name: graph_drop_routing
CALL gds.graph.drop($graph_name, false) YIELD graphName
RETURN graphName

// name: intersections_in_ring
// Loop seeding: intersections in an annulus around a start point. A loop of
// perimeter L approximates a circle of radius L/(2*pi), so waypoints are drawn
// from a ring at roughly that radius and spread by bearing (Python side) to
// stop all three legs collapsing onto the same corridor. Point-indexed
// distance predicate; no traversal.
MATCH (i:Intersection)
WHERE point.distance(
        i.location, point({latitude: $lat, longitude: $lon})) >= $min_m
  AND point.distance(
        i.location, point({latitude: $lat, longitude: $lon})) <= $max_m
  // Same component as the start. component_id is written by
  // scripts.build_trailheads from a projection over the region bbox, so this
  // one predicate excludes both unreachable waypoints and — importantly —
  // waypoints outside the projection. A ring near the bbox edge otherwise
  // returns nodes GDS has never heard of, and Dijkstra throws
  // "targetNode nodes do not exist in the in-memory graph".
  AND ($component_id IS NULL OR i.component_id = $component_id)
RETURN i.osm_node_id AS osm_node_id,
       i.location.latitude AS lat,
       i.location.longitude AS lon
LIMIT $limit

// name: pois_near_points
// Map-back: what does a route polyline pass? A routing engine returns geometry
// and nothing else (GraphHopper does not even expose osm_way_id), so the join
// back to meaning is spatial.
//
// This is the BOUNDING half — cheap, index-backed, deliberately generous. The
// caller samples the polyline, asks for anything near those samples, then
// filters exactly with core/geo.min_distance_to_polyline_m. Same two-step as
// ingestion's PASSES_BY: a point index cannot measure distance to a line, so
// bound in Cypher and be exact in Python.
//
// The radius is a CONSTANT on purpose. Widening it per POI by that POI's own
// reach (`<= $radius_m + p.extent_m`, to catch lakes whose centroid sits out on
// the water) reads naturally and quietly destroys this query: the predicate then
// depends on the node being tested, so the point index cannot serve it and every
// sample point scans every POI. At 1,707 samples that took 14 s per route and
// turned a catalogue rebuild into a seven-hour job. Areas are handled by
// area_pois_near_point instead, which pays for one scan rather than 1,707.
UNWIND $points AS pt
MATCH (p:POI)
WHERE point.distance(
        p.location, point({latitude: pt[0], longitude: pt[1]})) <= $radius_m
RETURN DISTINCT p.osm_id AS osm_id, p.name AS name, p.type AS type,
       p.location.latitude AS lat, p.location.longitude AS lon,
       [c IN coalesce(p.boundary, []) | [c.latitude, c.longitude]] AS boundary,
       p.description AS description,
       p.description_source AS description_source,
       p.description_license AS description_license,
       p.description_url AS description_url

// name: area_pois_near_point
// The other half of the map-back's bounding step: areas big enough that their
// centroid says nothing useful. Lago di Como's sits 5,122 m out on the water, so
// a centroid radius rules out every shoreline route before the exact check ever
// sees one -- and no fixed radius fixes it, because 5 km would sweep in half the
// region.
//
// So the reach is per-POI, which means this cannot use the point index. That is
// affordable exactly once: the caller passes ONE point (the centre of the
// route's bounding box) and `$reach_m` covers the whole route, so this is a
// single scan of the POIs that have an extent at all. It must stay that way --
// per-sample-point, it is the query that cost 14 s a route.
MATCH (p:POI)
WHERE coalesce(p.extent_m, 0.0) > 0
  AND point.distance(
        p.location, point({latitude: $lat, longitude: $lon}))
      <= $reach_m + p.extent_m
RETURN p.osm_id AS osm_id, p.name AS name, p.type AS type,
       p.location.latitude AS lat, p.location.longitude AS lon,
       [c IN coalesce(p.boundary, []) | [c.latitude, c.longitude]] AS boundary,
       p.description AS description,
       p.description_source AS description_source,
       p.description_license AS description_license,
       p.description_url AS description_url

// name: search_loops
// Stage 7: the chat layer SELECTS from the precomputed catalogue instead of
// computing a route per request (docs/route-pipeline.md). Everything expensive
// -- generation, scoring, dedup, the POI map-back -- already happened offline,
// so a turn is a filter and an ORDER BY.
//
// Ordered by the stored score, which already folds in length accuracy, off-road
// share, variety and climb. A caller must not re-rank on one of those alone or
// the offline scoring is silently overridden.
MATCH (r:Route)-[:STARTS_FROM]->(th:Trailhead)
// Activity is a hard filter, never a preference: a foot loop over steps and a
// T4 scramble is not a bike route, it is impassable. Catalogues are generated
// per profile (infra/graphhopper/config.yml) precisely so this can be exact.
// A route with no activity cannot be served: activity is a hard filter, and an
// unlabelled route also predates elevation, so it would answer a walking
// question with no climb figure. The 502 such rows from before the
// per-activity catalogues were deleted on 2026-08-18; this guard stays as
// defence, since any future schema change that adds a route without an
// activity would otherwise leak it into results.
WHERE r.activity IS NOT NULL
  AND ($activity IS NULL OR r.activity = $activity)
  AND ($min_distance_m IS NULL OR r.distance_m >= $min_distance_m)
  AND ($max_distance_m IS NULL OR r.distance_m <= $max_distance_m)
  AND ($min_off_road IS NULL OR r.off_road_share >= $min_off_road)
  // sac_scale / mtb:scale as GraphHopper decoded them. A route with no rating
  // is NOT excluded by a ceiling: unrated is unknown, and dropping it would
  // hide most of the catalogue behind a filter the data cannot answer.
  AND ($max_hike_rating IS NULL OR coalesce(r.hike_rating, 0) <= $max_hike_rating)
  AND ($max_mtb_rating IS NULL OR coalesce(r.mtb_rating, 0) <= $max_mtb_rating)
  AND ($max_ascent_m IS NULL OR coalesce(r.ascent_m, 0) <= $max_ascent_m)
  // Duration for the activity being asked about. With no activity stated the
  // walking figure is used: it is the slower of the two, so a route that fits
  // the time on foot fits it on a bike, and the answer never overpromises.
  AND ($max_duration_min IS NULL
       OR coalesce(
            CASE $activity WHEN 'mtb' THEN r.duration_mtb_min
                           ELSE r.duration_hike_min END,
            0) <= $max_duration_min)
  AND ($near_lat IS NULL
       OR point.distance(th.location,
                         point({latitude: $near_lat, longitude: $near_lon}))
          <= $near_radius_m)
CALL (r) {
  OPTIONAL MATCH (r)-[:PASSES]->(p:POI)
  WITH DISTINCT p
  WHERE p IS NOT NULL AND (size($poi_types) = 0 OR p.type IN $poi_types)
  RETURN collect(p.type) AS found_types,
         collect({name: p.name, type: p.type})[0..8] AS pois
}
WITH r, th, found_types, pois
// Every requested feature must be present, not merely one of them: "past a hut
// AND a lake" is a conjunction to a walker.
WHERE size($poi_types) = 0
   OR all(wanted IN $poi_types WHERE wanted IN found_types)
RETURN r.route_id AS id,
       r.activity AS activity,
       // Named after the best feature it passes (scripts/name_routes.py).
       // Null where there is nothing worth naming it after; the client shows
       // the distance instead rather than inventing something.
       r.name AS name,
       r.distance_m AS distance_m,
       r.ascent_m AS ascent_m,
       r.duration_hike_min AS duration_hike_min,
       r.duration_mtb_min AS duration_mtb_min,
       r.hike_rating AS hike_rating,
       r.mtb_rating AS mtb_rating,
       r.off_road_share AS off_road_share,
       r.score AS score,
       r.named_pois AS named_pois,
       th.trailhead_id AS trailhead_id,
       th.name AS trailhead_name,
       th.location.latitude AS start_lat,
       th.location.longitude AS start_lon,
       pois AS pois
ORDER BY r.score DESC
LIMIT $limit

// name: route_geometry
// The map payload for a catalogue route.
MATCH (r:Route {route_id: $route_id})
RETURN r.route_id AS id,
       [p IN r.geometry | [p.longitude, p.latitude]] AS coordinates

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
