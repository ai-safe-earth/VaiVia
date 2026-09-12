// Favourite/share persistence for DRAWN routes (docs/route-design.md,
// "Ask time" step 5): nothing is written while a user merely looks; when
// they keep a route, its document goes to the store and ONE (:Route) — the
// same shape the catalogue loader writes, built from the same
// vaivia_routes.neo4j_rows mapping — lands here. These templates MUTATE,
// so they live outside queries.cypher (whose guard suite forbids writes)
// and run on the write path (db.run), never through run_named.

// name: save_route_node
MERGE (r:Route {route_id: $route_id})
SET r += $props,
    r.saved_at = datetime($saved_at),
    r.run_id = $run_id

// name: save_route_places
UNWIND $rows AS row
MERGE (p:Place {place_id: row.place_id})
ON CREATE SET p.kind = row.kind,
              p.name = row.name,
              p.ele_m = row.ele_m,
              p.location = point({longitude: row.lon, latitude: row.lat})

// name: save_route_passes
UNWIND $rows AS row
MATCH (r:Route {route_id: row.route_id})
MATCH (p:Place {place_id: row.place_id})
MERGE (r)-[e:PASSES {seq: row.seq}]->(p)
SET e.offset_m = row.offset_m,
    e.distance_along_m = row.distance_along_m,
    e.is_start = row.is_start

// name: save_route_start
MERGE (s:Start {vertex_id: $row.vertex_id})
ON CREATE SET s.car_free = $row.car_free,
              s.names = $row.names,
              s.start_classes = $row.start_classes,
              s.reachable_spring = $row.reachable_spring,
              s.reachable_summer = $row.reachable_summer,
              s.reachable_autumn = $row.reachable_autumn,
              s.reachable_winter = $row.reachable_winter,
              s.seasons_unverified = $row.seasons_unverified,
              s.location = point({longitude: $row.lon, latitude: $row.lat})

// name: save_route_starts_at
MATCH (r:Route {route_id: $route_id})
MATCH (s:Start {vertex_id: $vertex_id})
MERGE (r)-[e:STARTS_AT]->(s)
SET e.nearest_m = $nearest_m
