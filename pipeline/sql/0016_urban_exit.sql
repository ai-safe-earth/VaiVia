-- Urban exits: where the network leaves a residential area, and whether that
-- is a place a walk begins.
--
-- The 999 residential polygons are still not starts -- "a residential area is
-- a polygon, not a point a walk begins at" is right about the polygon. Its
-- EXITS are points, they are few, and they are what "start from the village"
-- means on the ground. curate/places.py writes them into curated.place with
-- source = 'urban_exit' and kind = the way that leads out, so qa.v_start picks
-- them up with every other start and nothing here duplicates that.
--
-- This view is the review surface for the new rule: colour by exit_class and
-- the question "is this really where a walk starts" is one look, not an
-- expression. Same shape as every other qa.v_*, categories numbered so the
-- QGIS legend sorts.

DROP VIEW IF EXISTS qa.v_urban_exit;
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
       CASE WHEN v.component_id = (SELECT component_id FROM curated.vertex
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
FROM curated.place p
JOIN curated.vertex v ON v.vertex_id = p.vertex_id
LEFT JOIN curated.vertex_degree vd ON vd.vertex_id = p.vertex_id
WHERE p.source = 'urban_exit';
