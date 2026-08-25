-- The urban measure gets its review twin, and the factory gets its edges.
--
-- urban_class boundaries come from the measured distribution, like every
-- boundary here. Measured 2026-08-25 over 101,951 edges: the share of an
-- edge inside residential fabric is BIMODAL -- 59,428 at exactly 0, 35,300
-- at >=99.9%, and only ~7,200 anywhere between. An edge is in town or it is
-- not; the class cuts sit in the valleys (0 / 50% / 99%), where they
-- separate populations instead of splitting one.

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
       CASE WHEN EXISTS (SELECT 1 FROM source_map.edge_route er
                         WHERE er.edge_id = e.edge_id)
            THEN '1 carries a named route' ELSE '0 unnamed' END    AS route_class,
       CASE WHEN e.routable_foot AND e.routable_bike THEN '1 foot and bike'
            WHEN e.routable_foot                     THEN '2 foot only'
            WHEN e.routable_bike                     THEN '3 bike only'
            ELSE                                          '4 neither' END AS access_class,
       e.urban_m,
       CASE WHEN e.length_m > 0 THEN e.urban_m / e.length_m END AS urban_share,
       CASE WHEN e.urban_m IS NULL             THEN '9 not measured'
            WHEN e.urban_m = 0                 THEN '0 open'
            WHEN e.urban_m / nullif(e.length_m, 0) < 0.5  THEN '1 touches town'
            WHEN e.urban_m / nullif(e.length_m, 0) < 0.99 THEN '2 mostly urban'
            ELSE                                    '3 urban' END AS urban_class,
       -- The profile columns live here too, so the review bundle can ship ONE
       -- layer of 101,951 geometries instead of two. qa.v_elevation stays for a
       -- direct QGIS connection, where a focused layer costs nothing.
       CASE WHEN e.profile_m IS NULL THEN '2 not sampled'
            WHEN e.ascent_m IS NULL  THEN '1 outside DEM coverage'
            ELSE                          '0 profiled' END AS profile_class,
       array_length(e.profile_m, 1)              AS profile_points,
       e.profile_m[1]                            AS start_m,
       e.profile_m[array_length(e.profile_m, 1)] AS end_m
FROM source_map.edge e;

-- The factory's edges, oneway made real. pgr_dijkstra was called with
-- directed := false, which is a claim (`oneway is never read`) wearing a
-- default's clothes. These views give it cost/reverse_cost pairs instead:
-- a way a cyclist may ride only one direction costs -1 backwards (pgRouting's
-- "no such arc"), and the generator flips to directed := true reading them.
-- Foot ignores vehicular oneway (a walker walks a one-way street both ways);
-- oneway:foot exists in the wild but not in these two provinces today, so
-- the day it appears this view is where it lands.

DROP VIEW IF EXISTS catalogue.v_edges_foot;
CREATE VIEW catalogue.v_edges_foot AS
SELECT edge_id  AS id,
       source, target,
       length_m AS cost,
       length_m AS reverse_cost,
       urban_m
FROM source_map.edge
WHERE routable_foot;

DROP VIEW IF EXISTS catalogue.v_edges_bike;
CREATE VIEW catalogue.v_edges_bike AS
SELECT edge_id  AS id,
       source, target,
       -- oneway=yes forbids riding AGAINST the stored direction;
       -- oneway=-1 forbids riding WITH it. oneway:bicycle overrides either
       -- way (cycleways contraflow one-way streets all over these towns).
       CASE WHEN coalesce(tags ->> 'oneway:bicycle', tags ->> 'oneway', 'no')
                 = '-1'
            THEN -1.0
            ELSE length_m END AS cost,
       CASE WHEN coalesce(tags ->> 'oneway:bicycle', tags ->> 'oneway', 'no')
                 IN ('yes', 'true', '1')
            THEN -1.0
            ELSE length_m END AS reverse_cost,
       urban_m
FROM source_map.edge
WHERE routable_bike;
