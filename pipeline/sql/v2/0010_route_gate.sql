-- The publication gate: a route is served only when it passes the product's
-- own limits, and is HELD until something has judged it.
--
-- Two filters exist and they are not the same thing. draw/generate.py's
-- `_rejected` is the CALLER's limits for one run — what that sweep asked
-- for, counted and reported as a coverage fact. This is the CATALOGUE's
-- limit on every route however it arrived: generated in a batch, or drawn
-- on demand for one conversation. A route that satisfies the asker and
-- still walks four fifths of its length through town is not a route this
-- catalogue offers.
--
-- A verdict is a column, not a second table. The constraints will move as
-- they are calibrated, and a re-judge must be able to DEMOTE a published
-- route as easily as it promotes a held one; between two tables that is a
-- migration of rows, geometry and route_edge children each time, and the
-- direction that matters (taking something back) is the expensive one.
-- Here it is an UPDATE, and `gate_version` records which ruleset spoke.

ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS gate_verdict text NOT NULL DEFAULT 'held';
ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS gate_reasons jsonb;
ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS gate_version text;

-- 'held' is the default and the honest one: never judged, or judged on a
-- fact that was not measured. It is not a soft fail — it means nobody has
-- said, and an unsaid route stays out of the served catalogue.
DO $$
BEGIN
    ALTER TABLE catalogue.route ADD CONSTRAINT route_gate_verdict_check
        CHECK (gate_verdict IN ('held', 'pass', 'fail'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS route_gate_idx ON catalogue.route (gate_verdict);

-- qa.v_draw, the 0008 definition kept whole (the review bundle selects its
-- columns by name), plus the verdict, its reasons flattened for a QGIS
-- attribute table, and the gate_class twin.
DROP VIEW IF EXISTS qa.v_draw;
CREATE VIEW qa.v_draw AS
SELECT r.route_id,
       r.activity,
       r.shape,
       r.direction,
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
       r.urban_share,
       r.score,
       r.seed,
       r.gate_verdict,
       r.gate_version,
       (SELECT string_agg(value, '; ')
          FROM jsonb_array_elements_text(r.gate_reasons) AS value) AS gate_reasons,
       qa.difficulty_class(r.sac_max)           AS difficulty_class,
       CASE WHEN r.shape = 'destination'  THEN '0 to a destination'
            WHEN r.shape = 'out_and_back' THEN '2 strict out-and-back'
            ELSE                               '1 loop' END AS route_shape_class,
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
       CASE WHEN r.urban_share IS NULL   THEN '9 not measured'
            WHEN r.urban_share = 0       THEN '0 open'
            WHEN r.urban_share < 0.5     THEN '1 touches town'
            WHEN r.urban_share < 0.99    THEN '2 mostly urban'
            ELSE                              '3 urban' END AS urban_class,
       CASE WHEN r.gate_verdict = 'pass' THEN '1 pass (published)'
            WHEN r.gate_verdict = 'held' THEN '2 held (unjudged or unmeasured)'
            ELSE                              '3 fail' END AS gate_class,
       r.geom
FROM catalogue.route r;
