-- The urban rule's column on the catalogue: share of the walked metres
-- inside residential fabric, from the walked sequence (reversal-invariant).
-- NULL = not measured for this build (absent is not zero). The factory
-- rejects candidates over the measured threshold at persistence.
--
-- The threshold's measurement (2026-08-25, 228 routes, car-free-start
-- corpus): median 0.29, p80 0.55, p90 0.62, p95 0.66, then a thin tail to
-- 0.92 — twelve routes above p95, five above 0.8, all unnamed town walks.
-- U = 0.8 prunes the wholly-in-town tail ("more than four fifths of the
-- walk inside fabric") without touching the mixed valley approaches, and
-- the generator REPORTS every drop, so the rule can never silently narrow.

ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS urban_share double precision;

-- Backfill for rows generated before the column: one statement over the
-- walked sequences, exactly what the generator computes per candidate.
UPDATE catalogue.route r
SET urban_share = sub.share
FROM (
    SELECT re.route_id,
           CASE WHEN bool_or(e.urban_m IS NULL) THEN NULL
                ELSE sum(e.urban_m) / nullif(sum(e.length_m), 0) END AS share
    FROM catalogue.route_edge re
    JOIN source_map.edge e ON e.edge_id = re.edge_id
    GROUP BY re.route_id
) sub
WHERE sub.route_id = r.route_id
  AND r.urban_share IS NULL;

-- qa.v_draw, the 0015 definition kept whole (the review bundle selects its
-- columns by name) plus: direction, urban_share with the urban_class twin
-- (qa.v_network's buckets — one name, one vocabulary), and the strict
-- out-and-back joining route_shape_class.
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
       r.geom
FROM catalogue.route r;
