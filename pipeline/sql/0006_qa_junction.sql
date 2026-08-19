-- The third gap class, and the QGIS layer for it.
--
-- Measured 2026-08-19 against the first full network: of the 231 loose ends
-- within 2 m of an edge they are not joined to, the pair rule sees 14 and the
-- edge rule 92. The remaining 127 are loose ends stopping just short of an
-- EXISTING JUNCTION, and no rule could see them:
--
--   * gap_dangle_pair needs both ends to be dangles, and a junction is not;
--   * gap_dangle_edge excludes anything within tolerance of an edge's start or
--     end point, precisely to avoid double-reporting the pair case — which
--     also, silently, excluded every near-junction gap.
--
-- That made the largest gap class at 2 m the one class invisible in QGIS. It is
-- also the easiest to repair correctly: the junction is real and already
-- carries two or more edges, so the dangle moves and the junction never does.

CREATE OR REPLACE VIEW qa.v_gap_dangle_junction AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'distance_m')::float AS distance_m,
       (f.note::json ->> 'vertex')::bigint AS vertex_id,
       (f.note::json ->> 'junction')::bigint AS junction_id
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'gap_dangle_junction' AND f.run_id = r.run_id;

-- Repairs are reviewable after the fact: every fix carries before/after
-- geometry, so this is the layer for "what did the last repair pass do".
CREATE OR REPLACE VIEW qa.v_fix AS
SELECT x.fix_id, x.rule, x.target, x.note,
       x.geom_after AS geom,
       x.geom_before,
       ST_Distance(
           ST_StartPoint(x.geom_before)::geography,
           ST_StartPoint(x.geom_after)::geography
       ) AS start_moved_m,
       ST_Distance(
           ST_EndPoint(x.geom_before)::geography,
           ST_EndPoint(x.geom_after)::geography
       ) AS end_moved_m,
       x.created_at
FROM qa.fix x
WHERE x.geom_before IS NOT NULL
  AND x.geom_after IS NOT NULL
  AND GeometryType(x.geom_before) = 'LINESTRING'
  AND GeometryType(x.geom_after) = 'LINESTRING';
