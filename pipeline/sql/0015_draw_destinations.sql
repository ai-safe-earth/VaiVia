-- Destination routes: out to somewhere worth going, and back.
--
-- Owner (2026-08-20): routes are not just loops — some go from a start to an
-- INTERESTING POI (a view, a peak...) and come back. The anchors module
-- already knew the half of it ("a summit is a destination, not a trailhead");
-- this is the other half: the destination becomes the point of the route.
--
-- Two consequences the loop generator never had:
--
--   * Generated routes get NAMES. Trailhead naming is unsolved, but
--     destination naming is free — 219 of the 240 reachable peaks carry one.
--     "To Rifugio Elisa" is an answer; "generated-9f2c1ab4" is not.
--   * The catalogue is replaced per (activity, shape). Loops and destination
--     routes are siblings the same way foot and mtb are: regenerating one
--     family must not delete the other.

ALTER TABLE curated.route ADD COLUMN IF NOT EXISTS shape text NOT NULL DEFAULT 'loop';
ALTER TABLE curated.route ADD COLUMN IF NOT EXISTS destination_id text;
ALTER TABLE curated.route ADD COLUMN IF NOT EXISTS destination_kind text;
ALTER TABLE curated.route ADD COLUMN IF NOT EXISTS destination_name text;
CREATE INDEX IF NOT EXISTS route_shape_idx ON curated.route (activity, shape);

DROP VIEW IF EXISTS qa.v_draw;
CREATE VIEW qa.v_draw AS
SELECT r.route_id,
       r.activity,
       r.shape,
       r.name,
       r.destination_kind,
       r.destination_name,
       r.start_vertex,
       round((r.target_m / 1000)::numeric, 0)   AS target_km,
       round((r.distance_m / 1000)::numeric, 2) AS km,
       round(r.ascent_m::numeric, 0)            AS ascent_m,
       r.sac_scale,
       r.sac_max,
       r.graded_share,
       r.mtb_rideable,
       r.mtb_scale,
       r.bike_blocked_m,
       r.off_road_share,
       r.retrace_share,
       r.score,
       r.seed,
       qa.difficulty_class(r.sac_max)           AS difficulty_class,
       CASE WHEN r.shape = 'destination' THEN '0 to a destination'
            ELSE                              '1 loop' END AS route_shape_class,
       CASE WHEN r.graded_share IS NULL OR r.graded_share = 0
                                          THEN '3 nothing graded'
            WHEN r.graded_share < 0.3     THEN '2 sparsely graded (<30%)'
            WHEN r.graded_share < 0.7     THEN '1 partly graded (30-70%)'
            ELSE                               '0 well graded (>=70%)'
       END AS grading_class,
       CASE WHEN r.activity = 'mtb'      THEN '0 mtb (bike-legal by construction)'
            ELSE                              '1 foot' END AS activity_class,
       CASE WHEN r.ascent_m IS NULL      THEN '9 unknown'
            WHEN r.ascent_m < 200        THEN '1 flat (<200 m)'
            WHEN r.ascent_m < 600        THEN '2 rolling (200-600 m)'
            WHEN r.ascent_m < 1200       THEN '3 hilly (600-1200 m)'
            ELSE                              '4 mountain (>1200 m)' END AS climb_class,
       CASE WHEN r.off_road_share >= 0.6 THEN '1 trail (>=60% off-road)'
            WHEN r.off_road_share >= 0.3 THEN '2 mixed (30-60%)'
            ELSE                              '3 urban (<30%)' END AS offroad_class,
       CASE WHEN r.retrace_share <= 0.1  THEN '1 loop (<=10% retraced)'
            WHEN r.retrace_share <= 0.35 THEN '2 partial out-and-back'
            ELSE                              '3 out-and-back (>35%)' END AS shape_class,
       CASE WHEN r.mtb_rideable IS NULL  THEN '9 unknown'
            WHEN r.mtb_rideable          THEN '0 rideable'
            WHEN r.bike_blocked_m <= 100 THEN '1 blocked by <=100 m'
            ELSE                              '2 blocked by more' END AS mtb_class,
       r.geom
FROM curated.route r;
