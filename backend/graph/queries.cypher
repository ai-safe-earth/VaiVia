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
// appear in the path expression. Bounded, and the only A-to-B walk left (R7)
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
       pois AS pois,
       score AS score
ORDER BY score DESC
LIMIT $limit

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

// fragment: route_card
// The row a card is drawn from, shared by the search answer and the
// favorites list so the two cannot show different routes differently.
// It was copied rather than shared once, and had already drifted: the
// copy grew a stray relationship variable, and a column would have gone
// missing next. Expects r and s in scope (s may be null) and ends at the
// RETURN, so each reader adds only its own ORDER BY / LIMIT.
// The display POI list, capped -- any conjunction filter has already run.
CALL (r) {
  MATCH (r)-[:PASSES]->(p:Place)
  WITH DISTINCT p
  RETURN collect({name: p.name, type: p.kind})[0..8] AS pois
}
WITH r, s, pois
RETURN r.route_id AS id,
       r.activity AS activity,
       r.kind AS kind,
       r.shape AS shape,
       // Destination routes are named after where they go ("To Rifugio
       // Elisa"); OSM relations after themselves. Null where nothing earned a
       // name -- the client shows the distance rather than inventing one.
       r.name AS name,
       r.ref AS ref,
       r.destination_name AS destination_name,
       r.distance_m AS distance_m,
       r.ascent_m AS ascent_m,
       // The expanded card's figures. Already on the node (the export copies
       // the document's measures), so returning them costs nothing.
       r.descent_m AS descent_m,
       r.lowest_m AS lowest_m,
       r.highest_m AS highest_m,
       r.surface_dominant AS surface_dominant,
       r.pieces AS pieces,
       r.continuous AS continuous,
       r.sac_scale AS sac_scale,
       r.sac_max AS sac_max,
       r.graded_share AS graded_share,
       r.mtb_rideable AS mtb_rideable,
       r.mtb_scale AS mtb_scale,
       // A "no" says why, in metres: 6 m of steps and 1.6 km of private
       // road must not read identically (metadata-rules.md). Null on mapped
       // relations, which carry no conjunction run.
       r.bike_blocked_m AS bike_blocked_m,
       r.off_road_share AS off_road_share,
       r.score AS score,
       s.vertex_id AS start_vertex_id,
       s.names AS start_names,
       s.car_free AS car_free,
       s.location.latitude AS start_lat,
       s.location.longitude AS start_lon,
       pois AS pois

// name: route_exists
// The geometry itself lives in the route DOCUMENT (docs/route-document.md),
// served from the documents store by the API -- never copied into the graph,
// where a second home for it is how two truths start. This template only
// answers "is this a catalogue route", so the endpoint can 404 honestly
// before touching the filesystem.
//
// warnings = 0 quarantines a bad route here too: a quarantined
// row is qa's business, not an answer. Without it, POSTing any route_id makes
// favorites the one surface where a 0.0 km OSM fragment wearing a famous name
// reaches the screen as a full card, past the filter every search applies.
MATCH (r:Route {route_id: $route_id})
WHERE r.warnings = 0
RETURN r.route_id AS id,
       // Which export's document this node was loaded from, so the API can
       // refuse a file from another build instead of serving it silently.
       // Null on a graph loaded before the field existed; the check skips.
       r.doc_run_id AS doc_run_id

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

// name: routes_by_ids
// Hydrate favorite routes through the shared card fragment -- literally
// the same: both end in the route_card fragment, so a favorites card and a
// search card cannot come to differ. No ORDER BY:
// the caller re-sorts to the favorites' own saved order (Postgres created_at),
// which the graph does not know. An id no longer in the catalogue simply
// yields no row — the API reports it as missing rather than dropping it
// silently, because :Route nodes are replaced wholesale per export and only
// the geometry-derived id persists.
// A quarantined route yields no row here either, so a
// favorite that grew warnings on a later export reads as missing rather than
// rendering as a card search would never show.
MATCH (r:Route)
WHERE r.route_id IN $route_ids AND r.warnings = 0
OPTIONAL MATCH (r)-[:STARTS_AT]->(s:Start)
// include: route_card

// name: graph_extent
// The ingested graph's own bounding box, which is what "the whole network"
// means to anything that projects it into GDS.
//
// This exists because settings.default_bbox kept being used for it. That box is
// one Lecco-shaped rectangle holding 31,514 of the graph's 84,137 intersections
// once Bergamo was ingested, so every caller that projected it was silently
// analysing 37% of the network -- see docs/fragilities.md #16. A projection
// bbox is the QUERY's or the GRAPH's; it is never the app's configured one, and
// keeping the extent here rather than as a string in three scripts is what stops
// the fourth copy drifting.
//
// An aggregate over every :Intersection, no traversal: ~0.2 s over 84,137 nodes,
// well inside db.transaction.timeout (measured 2026-08-23).
MATCH (i:Intersection)
WHERE i.location IS NOT NULL
RETURN min(i.location.latitude) AS min_lat,
       min(i.location.longitude) AS min_lon,
       max(i.location.latitude) AS max_lat,
       max(i.location.longitude) AS max_lon
