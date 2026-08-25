-- The degree matview learns its regions, so QA is per-region expressible.
--
-- The 2 m weld tolerance was measured ONCE, over the combined two-province
-- network (metadata-rules.md, 2026-08-19). Bergamo work needs a Bergamo
-- histogram before any Bergamo repair, and nothing on the vertex side could
-- say which province a loose end is in: `regions` lives on edges. A vertex's
-- region IS derivable from its incident edges, and vertex_degree is exactly
-- the derived layer -- so the regions land here, not as a new column on
-- source_map.vertex that build_network and repair would each have to
-- backfill.
--
-- A materialized view cannot be widened in place, and three views read it,
-- so this file is the DROP+CREATE chain for all four -- the same shape v1's
-- 0011 used over 0005's views. The dependent views are recreated verbatim
-- from the baseline (v_start is widened again one file later, which is the
-- chain working as designed, not an accident).

DROP VIEW IF EXISTS qa.v_dangle;
DROP VIEW IF EXISTS qa.v_start;
DROP VIEW IF EXISTS qa.v_urban_exit;
DROP MATERIALIZED VIEW IF EXISTS source_map.vertex_degree;

CREATE MATERIALIZED VIEW source_map.vertex_degree AS
SELECT v.vertex_id,
       v.geom,
       v.component_id,
       count(DISTINCT e.edge_id) AS degree,
       -- DISTINCT over the unnested edge regions: a junction of a Lecco edge
       -- and a Bergamo edge belongs to both, exactly like a way crossing the
       -- region boundary does in staging.
       array_remove(array_agg(DISTINCT r.region), NULL) AS regions
FROM source_map.vertex v
LEFT JOIN source_map.edge e ON e.source = v.vertex_id OR e.target = v.vertex_id
LEFT JOIN LATERAL unnest(e.regions) AS r(region) ON true
GROUP BY v.vertex_id, v.geom, v.component_id;

CREATE UNIQUE INDEX IF NOT EXISTS vertex_degree_id_idx
    ON source_map.vertex_degree (vertex_id);
CREATE INDEX IF NOT EXISTS vertex_degree_geog_idx
    ON source_map.vertex_degree USING gist ((geom::geography));
CREATE INDEX IF NOT EXISTS vertex_degree_degree_idx
    ON source_map.vertex_degree (degree);
CREATE INDEX IF NOT EXISTS vertex_degree_regions_idx
    ON source_map.vertex_degree USING gin (regions);

-- ---- dependents, recreated verbatim from the baseline ----

CREATE VIEW qa.v_dangle AS
SELECT vertex_id, geom, component_id
FROM source_map.vertex_degree
WHERE degree = 1;

CREATE VIEW qa.v_start AS
SELECT p.vertex_id,
       v.component_id,
       vd.degree,
       count(*)                                       AS anchors,
       array_agg(DISTINCT p.kind ORDER BY p.kind)     AS kinds,
       array_remove(array_agg(DISTINCT p.name), NULL) AS names,
       round(min(p.distance_m)::numeric, 1)           AS nearest_m,
       sum(p.n_trips)                                 AS trips,
       bool_or(p.source = 'gtfs_stop' OR p.kind = 'station') AS car_free,
       CASE WHEN bool_or(p.source = 'gtfs_stop' OR p.kind = 'station')
            THEN '0 car-free (station or stop)' ELSE '1 needs a car' END AS access_class,
       -- Begin here and get nowhere: 86 of these.
       CASE WHEN v.component_id = (SELECT component_id FROM source_map.vertex
                                   GROUP BY component_id
                                   ORDER BY count(*) DESC LIMIT 1)
            THEN '0 main network' ELSE '1 island' END AS reachability_class,
       CASE WHEN array_length(array_remove(array_agg(DISTINCT p.name), NULL), 1) IS NULL
            THEN '1 unnamed' ELSE '0 named' END AS naming_class,
       v.geom
FROM source_map.place p
JOIN source_map.vertex v ON v.vertex_id = p.vertex_id
LEFT JOIN source_map.vertex_degree vd ON vd.vertex_id = p.vertex_id
WHERE p.is_start
GROUP BY p.vertex_id, v.component_id, vd.degree, v.geom;

CREATE VIEW qa.v_urban_exit AS
SELECT p.vertex_id,
       p.name,
       p.kind                            AS way_out,
       p.is_start,
       p.start_note,
       v.component_id,
       vd.degree,
       p.regions,
       CASE WHEN p.is_start THEN '0 onto a trail'
            ELSE                 '1 onto a lane (town continuing)' END AS exit_class,
       -- Begin here and get nowhere. The same island test qa.v_start applies,
       -- repeated because an exit is judged before anything is generated from
       -- it, and an exit off the main network is worth seeing at that point.
       CASE WHEN v.component_id = (SELECT component_id FROM source_map.vertex
                                   GROUP BY component_id
                                   ORDER BY count(*) DESC LIMIT 1)
            THEN '0 main network' ELSE '1 island' END AS reachability_class,
       -- 21 of 999 residential areas carry a name, so most exits are unnamed.
       -- Recorded rather than invented: naming one from a nearby feature is
       -- the same unsolved problem qa.v_start.names has for car parks.
       CASE WHEN p.name IS NULL THEN '1 unnamed' ELSE '0 named' END AS naming_class,
       -- How many ways meet here: a degree-2 exit is a way passing through the
       -- boundary, a higher one is a junction where a walker has a choice.
       CASE WHEN vd.degree IS NULL THEN '9 unknown'
            WHEN vd.degree <= 2    THEN '0 through (2 or fewer)'
            WHEN vd.degree = 3     THEN '1 fork (3)'
            ELSE                        '2 junction (4+)' END AS degree_class,
       v.geom
FROM source_map.place p
JOIN source_map.vertex v ON v.vertex_id = p.vertex_id
LEFT JOIN source_map.vertex_degree vd ON vd.vertex_id = p.vertex_id
WHERE p.source = 'urban_exit';
