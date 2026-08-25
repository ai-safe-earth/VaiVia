-- Category columns on the review layers, so QGIS can style without expressions.
--
-- The layers so far carry raw measures: gradient as a float, matched_fraction
-- as a float, distance_m as a float. Every one of those needs a QGIS expression
-- and a hand-built class ramp before it shows anything, and a class ramp built
-- by hand in a dialog is a decision that lives in one .qgz file on one machine
-- rather than in the database that is the product.
--
-- So every measure that gets styled has a `*_class` twin beside it. Read the
-- number when the number matters; drag the class into Categorized and get a
-- legend that already means something.
--
-- THE NUMERIC PREFIX IS DELIBERATE. QGIS sorts categories by value, so
-- "gentle / moderate / steep / very steep / flat" comes out with flat between
-- gentle and moderate and the ramp reads backwards in the legend. A leading
-- digit fixes the order for every renderer, and it survives export to
-- GeoPackage, which an ordering defined in a style file does not.
--
-- EVERY VIEW HERE IS DROPPED AND CREATED, NEVER `CREATE OR REPLACE`, and so is
-- every view in 0005-0010 as of this migration. Two reasons, both found the
-- hard way while writing this file:
--
--   * CREATE OR REPLACE can only APPEND a column. Adding a class beside the
--     measure it classifies inserts one in the middle, which it refuses.
--   * migrate.py replays the WHOLE chain every run, and replay is the normal
--     case, not an error. So when a later migration widens a view, the earlier
--     migration that first defined it runs again on the NEXT replay and fails
--     with "cannot drop columns from view". Dropping first makes the chain
--     order-independent; leaving it would have made the store un-migratable
--     from its own history.
--
-- Boundaries come from the measurements already in docs/metadata-rules.md, not
-- from round numbers chosen here: the gradient bands are the ones the network
-- actually falls into (48.6% under 5%, 0.3% over 50%), and the place bands are
-- where the snap distributions separate (car parks p50 6.2 m, peaks p90 320 m).

-- ── Shared classifiers ───────────────────────────────────────────────────────

-- Steepness of a stretch, on the ABSOLUTE gradient: for review, "how steep is
-- this" is the question, and up or down is the `net_m` column beside it.
CREATE OR REPLACE FUNCTION qa.steepness_class(gradient double precision)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN gradient IS NULL          THEN '9 unknown'
        WHEN abs(gradient) < 0.05      THEN '1 flat (<5%)'
        WHEN abs(gradient) < 0.15      THEN '2 gentle (5-15%)'
        WHEN abs(gradient) < 0.30      THEN '3 moderate (15-30%)'
        WHEN abs(gradient) < 0.50      THEN '4 steep (30-50%)'
        ELSE                                '5 very steep (>50%)'
    END
$$;

-- SAC hiking scale, grouped to what a walker distinguishes. `9 invalid tag`
-- is not defensive padding: four rows in staging carry junk in sac_scale (two
-- `T3`, one `2`, one containing a sentence about stone ruins), and a
-- classifier that folded them into "no grade" would hide them for good.
CREATE OR REPLACE FUNCTION qa.difficulty_class(sac_scale text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    -- Searched CASE, not `CASE sac_scale WHEN NULL`: that never matches,
    -- because NULL = NULL is unknown, and every ungraded edge would silently
    -- fall through to the invalid bucket.
    SELECT CASE
        WHEN sac_scale IS NULL                        THEN '0 ungraded'
        WHEN sac_scale = 'hiking'                     THEN '1 hiking (T1)'
        WHEN sac_scale = 'mountain_hiking'            THEN '2 mountain (T2)'
        WHEN sac_scale = 'demanding_mountain_hiking'  THEN '3 demanding mountain (T3)'
        WHEN sac_scale = 'alpine_hiking'              THEN '4 alpine (T4)'
        WHEN sac_scale = 'demanding_alpine_hiking'    THEN '5 demanding alpine (T5)'
        WHEN sac_scale = 'difficult_alpine_hiking'    THEN '6 difficult alpine (T6)'
        ELSE                                               '9 invalid tag'
    END
$$;

-- Underfoot, grouped from the ~62% of edges that carry a surface tag.
CREATE OR REPLACE FUNCTION qa.surface_class(surface text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN surface IS NULL THEN '0 untagged'
        WHEN surface IN ('asphalt','concrete','paved','paving_stones','sett',
                         'cobblestone','concrete:plates','metal','wood')
                             THEN '1 paved'
        WHEN surface IN ('compacted','fine_gravel','gravel','pebblestone',
                         'unpaved','ground','dirt','earth','grass','sand',
                         'mud','rock','stone','woodchips','grass_paver')
                             THEN '2 unpaved'
        ELSE                      '3 other'
    END
$$;

-- How far a place sits from the network. Bands, not a threshold: nothing is
-- filtered on this, it only makes the tail visible at a glance.
CREATE OR REPLACE FUNCTION qa.distance_band(distance_m double precision)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN distance_m IS NULL THEN '9 unknown'
        WHEN distance_m < 10    THEN '1 on the network (<10 m)'
        WHEN distance_m < 50    THEN '2 close (10-50 m)'
        WHEN distance_m < 100   THEN '3 near (50-100 m)'
        WHEN distance_m < 500   THEN '4 far (100-500 m)'
        ELSE                         '5 remote (>500 m)'
    END
$$;

-- ── The network, with everything on it ───────────────────────────────────────

-- The one layer to open if you only open one. Every styled field has a class
-- beside it, and `carries_route` says whether this edge has a name from a
-- relation even when its own `name` tag is empty (10,361 edges do).
DROP VIEW IF EXISTS qa.v_network;
CREATE VIEW qa.v_network AS
SELECT e.edge_id, e.way_id, e.geom, e.length_m,
       e.tags ->> 'highway'   AS highway,
       e.tags ->> 'surface'   AS surface,
       e.tags ->> 'sac_scale' AS sac_scale,
       e.tags ->> 'name'      AS name,
       e.routable_foot, e.routable_bike, e.regions,
       e.ascent_m, e.descent_m,
       e.ascent_m - e.descent_m AS net_m,
       CASE WHEN e.length_m > 0
            THEN (e.ascent_m - e.descent_m) / e.length_m END AS gradient,
       qa.steepness_class(
           CASE WHEN e.length_m > 0
                THEN (e.ascent_m - e.descent_m) / e.length_m END)  AS steepness_class,
       qa.difficulty_class(e.tags ->> 'sac_scale')                 AS difficulty_class,
       qa.surface_class(e.tags ->> 'surface')                      AS surface_class,
       CASE WHEN EXISTS (SELECT 1 FROM curated.edge_route er
                         WHERE er.edge_id = e.edge_id)
            THEN '1 carries a named route' ELSE '0 unnamed' END    AS route_class,
       CASE WHEN e.routable_foot AND e.routable_bike THEN '1 foot and bike'
            WHEN e.routable_foot                     THEN '2 foot only'
            WHEN e.routable_bike                     THEN '3 bike only'
            ELSE                                          '4 neither' END AS access_class,
       -- The profile columns live here too, so the review bundle can ship ONE
       -- layer of 101,951 geometries instead of two. qa.v_elevation stays for a
       -- direct QGIS connection, where a focused layer costs nothing.
       CASE WHEN e.profile_m IS NULL THEN '2 not sampled'
            WHEN e.ascent_m IS NULL  THEN '1 outside DEM coverage'
            ELSE                          '0 profiled' END AS profile_class,
       array_length(e.profile_m, 1)              AS profile_points,
       e.profile_m[1]                            AS start_m,
       e.profile_m[array_length(e.profile_m, 1)] AS end_m
FROM curated.edge e;

-- ── Elevation ────────────────────────────────────────────────────────────────

DROP VIEW IF EXISTS qa.v_elevation;
CREATE VIEW qa.v_elevation AS
SELECT e.edge_id, e.way_id, e.length_m, e.ascent_m, e.descent_m,
       e.ascent_m - e.descent_m                     AS net_m,
       CASE WHEN e.length_m > 0
            THEN (e.ascent_m - e.descent_m) / e.length_m END AS gradient,
       qa.steepness_class(
           CASE WHEN e.length_m > 0
                THEN (e.ascent_m - e.descent_m) / e.length_m END) AS steepness_class,
       CASE WHEN e.profile_m IS NULL           THEN '2 not sampled'
            WHEN e.ascent_m IS NULL            THEN '1 outside DEM coverage'
            ELSE                                    '0 profiled' END AS profile_class,
       array_length(e.profile_m, 1)                 AS profile_points,
       e.profile_m[1]                               AS start_m,
       e.profile_m[array_length(e.profile_m, 1)]    AS end_m,
       e.tags ->> 'highway'   AS highway,
       e.tags ->> 'sac_scale' AS sac_scale,
       e.tags ->> 'name'      AS name,
       e.geom
FROM curated.edge e;

-- ── Routes ───────────────────────────────────────────────────────────────────

DROP VIEW IF EXISTS qa.v_route;
CREATE VIEW qa.v_route AS
SELECT r.rel_id,
       r.tags ->> 'ref'         AS ref,
       r.tags ->> 'name'        AS name,
       r.tags ->> 'route'       AS route_kind,
       r.tags ->> 'network'     AS network,
       r.tags ->> 'osmc:symbol' AS osmc_symbol,
       r.regions,
       count(*)                                    AS edges,
       round((sum(e.length_m) / 1000)::numeric, 2) AS km,
       ST_NumGeometries(ST_Multi(ST_LineMerge(ST_Collect(e.geom)))) AS pieces,
       -- A route in one piece runs continuously through this network. In nine,
       -- it is either clipped at the bbox edge or has real gaps -- and that is
       -- a defect the topology rules cannot see, because they look at loose
       -- ends, not at whether a route runs through.
       CASE WHEN ST_NumGeometries(ST_Multi(ST_LineMerge(ST_Collect(e.geom)))) = 1
                 THEN '0 continuous'
            WHEN ST_NumGeometries(ST_Multi(ST_LineMerge(ST_Collect(e.geom)))) <= 3
                 THEN '1 broken (2-3 pieces)'
            ELSE      '2 scattered (4+ pieces)' END AS continuity_class,
       CASE WHEN sum(e.ascent_m) IS NULL   THEN '9 unknown'
            WHEN sum(e.ascent_m) < 200     THEN '1 flat (<200 m)'
            WHEN sum(e.ascent_m) < 600     THEN '2 rolling (200-600 m)'
            WHEN sum(e.ascent_m) < 1200    THEN '3 hilly (600-1200 m)'
            ELSE                                '4 mountain (>1200 m)' END AS climb_class,
       CASE WHEN sum(e.length_m) < 5000    THEN '1 short (<5 km)'
            WHEN sum(e.length_m) < 15000   THEN '2 half day (5-15 km)'
            WHEN sum(e.length_m) < 30000   THEN '3 long (15-30 km)'
            ELSE                                '4 multi-day (>30 km)' END AS length_class,
       CASE r.tags ->> 'network'
            WHEN 'iwn' THEN '4 international' WHEN 'icn' THEN '4 international'
            WHEN 'nwn' THEN '3 national'      WHEN 'ncn' THEN '3 national'
            WHEN 'rwn' THEN '2 regional'      WHEN 'rcn' THEN '2 regional'
            WHEN 'lwn' THEN '1 local'         WHEN 'lcn' THEN '1 local'
            ELSE            '0 unclassified' END AS scope_class,
       ST_Multi(ST_LineMerge(ST_Collect(e.geom))) AS geom
FROM staging.osm_relation r
JOIN (SELECT DISTINCT rel_id, edge_id FROM curated.edge_route) er ON er.rel_id = r.rel_id
JOIN curated.edge e ON e.edge_id = er.edge_id
GROUP BY r.rel_id, r.tags, r.regions;

DROP VIEW IF EXISTS qa.v_route_coverage;
CREATE VIEW qa.v_route_coverage AS
WITH members AS (
    SELECT r.rel_id,
           count(*) FILTER (WHERE m ->> 'type' = 'w') AS way_members,
           count(DISTINCT (m ->> 'ref')) FILTER (WHERE m ->> 'type' = 'w')
                                                      AS distinct_way_members,
           count(*) FILTER (WHERE m ->> 'type' = 'n') AS node_members,
           count(*) FILTER (WHERE m ->> 'type' = 'r') AS relation_members
    FROM staging.osm_relation r, LATERAL jsonb_array_elements(r.members) m
    GROUP BY r.rel_id
), matched AS (
    SELECT r.rel_id, r.tags, m.way_members, m.distinct_way_members,
           m.node_members, m.relation_members,
           count(DISTINCT e.way_id)   AS matched_ways,
           count(DISTINCT er.edge_id) AS matched_edges,
           CASE WHEN m.distinct_way_members = 0 THEN NULL
                ELSE round(count(DISTINCT e.way_id)::numeric
                           / m.distinct_way_members, 3) END AS matched_fraction
    FROM staging.osm_relation r
    JOIN members m ON m.rel_id = r.rel_id
    LEFT JOIN curated.edge_route er ON er.rel_id = r.rel_id
    LEFT JOIN curated.edge e ON e.edge_id = er.edge_id
    GROUP BY r.rel_id, r.tags, m.way_members, m.distinct_way_members,
             m.node_members, m.relation_members
)
SELECT rel_id,
       tags ->> 'ref'  AS ref,
       tags ->> 'name' AS name,
       way_members, distinct_way_members, node_members, relation_members,
       matched_ways, matched_edges, matched_fraction,
       -- A fragment is a route this network cannot hold: BI-12 Trieste-Savona
       -- matches 2 of its 646 ways. Route generation must filter on this.
       CASE WHEN matched_fraction IS NULL  THEN '9 no way members'
            WHEN matched_fraction >= 0.9   THEN '0 complete (>=90%)'
            WHEN matched_fraction >= 0.6   THEN '1 most (60-90%)'
            WHEN matched_fraction >= 0.2   THEN '2 partial (20-60%)'
            ELSE                                '3 fragment (<20%)' END AS coverage_class
FROM matched;

DROP VIEW IF EXISTS qa.v_route_elevation;
CREATE VIEW qa.v_route_elevation AS
SELECT r.rel_id,
       r.tags ->> 'ref'   AS ref,
       r.tags ->> 'name'  AS name,
       r.tags ->> 'route' AS route_kind,
       count(*)                                    AS edges,
       round((sum(e.length_m) / 1000)::numeric, 2) AS km,
       round(sum(e.ascent_m)::numeric, 0)          AS ascent_m,
       round(sum(e.descent_m)::numeric, 0)         AS descent_m,
       round(min(pz.lo)::numeric, 0)               AS lowest_m,
       round(max(pz.hi)::numeric, 0)               AS highest_m,
       count(*) FILTER (WHERE e.ascent_m IS NULL)  AS edges_without_profile,
       CASE WHEN sum(e.ascent_m) IS NULL  THEN '9 unknown'
            WHEN sum(e.ascent_m) < 200    THEN '1 flat (<200 m)'
            WHEN sum(e.ascent_m) < 600    THEN '2 rolling (200-600 m)'
            WHEN sum(e.ascent_m) < 1200   THEN '3 hilly (600-1200 m)'
            ELSE                               '4 mountain (>1200 m)' END AS climb_class,
       ST_Multi(ST_LineMerge(ST_Collect(e.geom)))  AS geom
FROM staging.osm_relation r
JOIN (SELECT DISTINCT rel_id, edge_id FROM curated.edge_route) er ON er.rel_id = r.rel_id
JOIN curated.edge e ON e.edge_id = er.edge_id
LEFT JOIN LATERAL (
    SELECT min(z) AS lo, max(z) AS hi FROM unnest(e.profile_m) AS z
) pz ON true
GROUP BY r.rel_id, r.tags;

-- ── Places and starts ────────────────────────────────────────────────────────

DROP VIEW IF EXISTS qa.v_place;
CREATE VIEW qa.v_place AS
SELECT p.source, p.source_id, p.kind, p.name, p.ele_m,
       p.vertex_id, p.distance_m, p.is_start, p.start_note, p.n_trips,
       p.regions, v.component_id, p.geom,
       qa.distance_band(p.distance_m) AS distance_band,
       CASE WHEN p.is_start THEN '0 can start a walk'
            ELSE                 '1 destination or passed' END AS role_class,
       -- A place snapped to an island is attached to a network that goes
       -- nowhere. Invisible in the distance, obvious here.
       CASE WHEN v.component_id = (SELECT component_id FROM curated.vertex
                                   GROUP BY component_id
                                   ORDER BY count(*) DESC LIMIT 1)
            THEN '0 main network' ELSE '1 island' END AS reachability_class
FROM curated.place p
JOIN curated.vertex v ON v.vertex_id = p.vertex_id;

DROP VIEW IF EXISTS qa.v_place_link;
CREATE VIEW qa.v_place_link AS
SELECT p.source, p.source_id, p.kind, p.name, p.distance_m, p.is_start,
       qa.distance_band(p.distance_m) AS distance_band,
       CASE WHEN p.is_start THEN '0 can start a walk'
            ELSE                 '1 destination or passed' END AS role_class,
       ST_MakeLine(p.geom, v.geom) AS geom
FROM curated.place p
JOIN curated.vertex v ON v.vertex_id = p.vertex_id
WHERE NOT ST_Equals(p.geom, v.geom);

DROP VIEW IF EXISTS qa.v_start;
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
       CASE WHEN v.component_id = (SELECT component_id FROM curated.vertex
                                   GROUP BY component_id
                                   ORDER BY count(*) DESC LIMIT 1)
            THEN '0 main network' ELSE '1 island' END AS reachability_class,
       CASE WHEN array_length(array_remove(array_agg(DISTINCT p.name), NULL), 1) IS NULL
            THEN '1 unnamed' ELSE '0 named' END AS naming_class,
       v.geom
FROM curated.place p
JOIN curated.vertex v ON v.vertex_id = p.vertex_id
LEFT JOIN curated.vertex_degree vd ON vd.vertex_id = p.vertex_id
WHERE p.is_start
GROUP BY p.vertex_id, v.component_id, vd.degree, v.geom;
