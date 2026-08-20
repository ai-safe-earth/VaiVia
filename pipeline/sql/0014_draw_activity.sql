-- The exigent join, made visible; and per-activity catalogues.
--
-- Owner feedback on the first catalogue (2026-08-20): the loops LOOKED
-- un-joined from their segment data, and both causes were real. The median
-- route carries a SAC grade on only 16% of its metres, so the >=5% character
-- rule often reads "ungraded" while the map visibly shows graded trail; and 52
-- routes said mtb=false with the reason hidden -- one is blocked by 6 metres
-- of steps, another by 1.6 km, and they read identically.
--
-- The rule, stated by the owner: when segments disagree, a joined attribute
-- takes the MOST DEMANDING value. A loop with one segment not for bikes is not
-- for bikes. So:
--
--   * sac_max is the hardest metre walked, any length -- the EXIGENT grade,
--     what you must be able to handle. sac_scale stays beside it as the
--     CHARACTER (>=5% rule): "a T2 walk with a T4 move in it" is sac_scale T2,
--     sac_max T4, and both facts are true.
--   * graded_share says how much of the route carries ANY grade, so an
--     "ungraded" no longer looks like a missing join.
--   * bike_blocked_m says WHY a route is not rideable, in metres.
--
-- And the second half of the owner's observation -- "another loop that shares
-- many of the segments could be bike friendly if all the segments are" -- is
-- an ACTIVITY, not a filter: mtb routes are drawn over routable_bike edges
-- only, so every mtb loop is bike-legal BY CONSTRUCTION, sharing whatever
-- foot-loop segments happen to be legal and detouring around the ones that
-- are not. The two catalogues coexist, keyed by activity.

ALTER TABLE curated.route ADD COLUMN IF NOT EXISTS activity text NOT NULL DEFAULT 'foot';
ALTER TABLE curated.route ADD COLUMN IF NOT EXISTS sac_max text;
ALTER TABLE curated.route ADD COLUMN IF NOT EXISTS graded_share double precision;
ALTER TABLE curated.route ADD COLUMN IF NOT EXISTS bike_blocked_m double precision;
CREATE INDEX IF NOT EXISTS route_activity_idx ON curated.route (activity);

DROP VIEW IF EXISTS qa.v_draw;
CREATE VIEW qa.v_draw AS
SELECT r.route_id,
       r.activity,
       r.start_vertex,
       round((r.target_m / 1000)::numeric, 0)   AS target_km,
       round((r.distance_m / 1000)::numeric, 2) AS km,
       round(r.ascent_m::numeric, 0)            AS ascent_m,
       r.sac_scale,                              -- character (>=5%)
       r.sac_max,                                -- exigent (hardest metre)
       r.graded_share,
       r.mtb_rideable,
       r.mtb_scale,
       r.bike_blocked_m,
       r.off_road_share,
       r.retrace_share,
       r.score,
       r.seed,
       -- The class a reviewer judges by is the EXIGENT one (owner rule); the
       -- character grade stays as a column for the label question.
       qa.difficulty_class(r.sac_max)           AS difficulty_class,
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
