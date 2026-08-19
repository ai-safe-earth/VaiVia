-- QGIS-facing views: one per QA rule, latest run only.
--
-- QGIS loads a PostGIS layer per table or view. Filtering qa.finding by hand in
-- the layer dialog works but has to be redone every session, and the run_id of
-- "the latest run" changes each time — so the views do it. Each carries a
-- single geometry type where the rule produces one, because QGIS styles a
-- mixed-geometry layer poorly.
--
-- Every view is read-only by construction (no INSTEAD OF triggers): inspection
-- must not become a way to silently edit findings. Repairs go through
-- topology/repair.py, which records qa.fix.

CREATE OR REPLACE VIEW qa.latest_run AS
SELECT run_id
FROM build_run
WHERE stage = 'topology' AND parameters ? 'detector'
ORDER BY started_at DESC
LIMIT 1;

CREATE OR REPLACE VIEW qa.v_gap_dangle_pair AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'distance_m')::float AS distance_m,
       (f.note::json ->> 'a')::bigint AS vertex_a,
       (f.note::json ->> 'b')::bigint AS vertex_b
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'gap_dangle_pair' AND f.run_id = r.run_id;

CREATE OR REPLACE VIEW qa.v_gap_dangle_edge AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'distance_m')::float AS distance_m,
       (f.note::json ->> 'vertex')::bigint AS vertex_id,
       (f.note::json ->> 'edge')::bigint AS edge_id
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'gap_dangle_edge' AND f.run_id = r.run_id;

CREATE OR REPLACE VIEW qa.v_overlap AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'shared_m')::float AS shared_m,
       (f.note::json ->> 'a')::bigint AS edge_a,
       (f.note::json ->> 'b')::bigint AS edge_b
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'overlap' AND f.run_id = r.run_id;

CREATE OR REPLACE VIEW qa.v_degenerate AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'length_m')::float AS length_m,
       (f.note::json ->> 'self_loop')::boolean AS self_loop
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'degenerate' AND f.run_id = r.run_id;

CREATE OR REPLACE VIEW qa.v_island AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'vertices')::int AS vertices,
       (f.note::json ->> 'component_id')::bigint AS component_id
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'island' AND f.run_id = r.run_id;

-- The network itself, for context beneath the findings: without a basemap of
-- what the edges are, a gap layer is nine unexplained lines on white.
CREATE OR REPLACE VIEW qa.v_network AS
SELECT e.edge_id, e.way_id, e.geom, e.length_m,
       e.tags ->> 'highway' AS highway,
       e.tags ->> 'surface' AS surface,
       e.tags ->> 'sac_scale' AS sac_scale,
       e.tags ->> 'name' AS name,
       e.routable_foot, e.routable_bike, e.regions
FROM curated.edge e;

-- Loose ends, the input to the gap rules: useful on its own when judging
-- whether a dangle is a defect or a real dead end.
CREATE OR REPLACE VIEW qa.v_dangle AS
SELECT vertex_id, geom, component_id
FROM curated.vertex_degree
WHERE degree = 1;
