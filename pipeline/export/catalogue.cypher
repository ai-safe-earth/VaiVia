// The catalogue in Neo4j: routes, places, and what connects them.
//
// Neo4j is a READER of the route documents (docs/route-document.md): this file
// loads what the app SELECTS on — identity, measures, difficulty, MTB, the
// route↔place relationships — and deliberately not the geometry or the
// profile. The document is the source for those, fetched by route_id; copying
// a 300 KB polyline into a node property would make Neo4j a second place
// geometry lives, and two places is how two truths start.
//
// The export owns three labels — :Route, :Place, :Start — and replaces them
// wholesale each run (replace-not-merge, like every derived store). The 250
// :Route nodes present before the first run came from the backend's own 2026-08-18
// catalogue generation, built on a branch that never merged: develop's backend
// has no template that reads them, and the pipeline catalogue supersedes them.
// Everything else in the graph (:Trail, :Segment, :Intersection, :POI,
// :Trailhead, :Region) is the backend's and is not touched here.
//
// Statements are named (// name: <x>) and run by export/neo4j_load.py with
// parameters only — the same discipline as backend/graph/queries.cypher.

// name: constraints_route
CREATE CONSTRAINT route_id_unique IF NOT EXISTS
FOR (r:Route) REQUIRE r.route_id IS UNIQUE

// name: constraints_place
CREATE CONSTRAINT place_id_unique IF NOT EXISTS
FOR (p:Place) REQUIRE p.place_id IS UNIQUE

// name: constraints_start
CREATE CONSTRAINT start_vertex_unique IF NOT EXISTS
FOR (s:Start) REQUIRE s.vertex_id IS UNIQUE

// name: count_owned
MATCH (n)
WHERE n:Route OR n:Place OR n:Start
RETURN count(n) AS owned

// name: wipe_owned
MATCH (n)
WHERE n:Route OR n:Place OR n:Start
CALL (n) {
  DETACH DELETE n
} IN TRANSACTIONS OF 1000 ROWS

// name: load_routes
UNWIND $rows AS row
MERGE (r:Route {route_id: row.route_id})
SET r += row.props,
    r.exported_at = datetime($exported_at),
    r.run_id = $run_id

// name: load_places
UNWIND $rows AS row
MERGE (p:Place {place_id: row.place_id})
SET p.kind = row.kind,
    p.name = row.name,
    p.ele_m = row.ele_m,
    p.location = point({longitude: row.lon, latitude: row.lat}),
    p.run_id = $run_id

// name: load_starts
UNWIND $rows AS row
MERGE (s:Start {vertex_id: row.vertex_id})
SET s.car_free = row.car_free,
    s.names = row.names,
    s.location = point({longitude: row.lon, latitude: row.lat}),
    s.run_id = $run_id

// name: link_passes
UNWIND $rows AS row
MATCH (r:Route {route_id: row.route_id})
MATCH (p:Place {place_id: row.place_id})
MERGE (r)-[e:PASSES {seq: row.seq}]->(p)
SET e.offset_m = row.offset_m,
    e.distance_along_m = row.distance_along_m,
    e.is_start = row.is_start

// name: link_starts
UNWIND $rows AS row
MATCH (r:Route {route_id: row.route_id})
MATCH (s:Start {vertex_id: row.vertex_id})
MERGE (r)-[e:STARTS_AT]->(s)
SET e.nearest_m = row.nearest_m

// name: verify_counts
MATCH (r:Route)
OPTIONAL MATCH (r)-[e:PASSES]->()
RETURN count(DISTINCT r) AS routes, count(e) AS passes,
       count(DISTINCT CASE WHEN r.kind = 'generated' THEN r END) AS generated,
       count(DISTINCT CASE WHEN r.name IS NOT NULL THEN r END) AS named

// name: sample_selection
// The query shape the whole design exists for (docs/route-pipeline.md): a chat
// turn SELECTING from the catalogue instead of computing. Run at the end of
// every export as the smoke test — and itself a lesson from the first export,
// where it surfaced 0.0 km fragments: the quality block is not decoration, and
// any consumer that ignores `warnings` will offer a famous name attached to
// 200 metres of route. Selection filters on it, exactly as the documents say.
MATCH (r:Route)-[:PASSES]->(p:Place {kind: 'peak'})
WHERE r.distance_m >= $min_m AND r.distance_m <= $max_m
  AND r.warnings = 0
  AND r.sac_max IS NOT NULL
RETURN r.name AS name, r.ref AS ref, round(r.distance_m / 100) / 10 AS km,
       round(r.ascent_m) AS ascent_m, r.sac_max AS sac_max, p.name AS peak
ORDER BY coalesce(r.score, 0.5) DESC, r.ascent_m DESC
LIMIT $limit
