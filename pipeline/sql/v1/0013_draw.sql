-- Generated routes: the catalogue pipeline/draw/ writes.
--
-- The design docs/route-pipeline.md ratified, moved into the pipeline where
-- the data now lives: for each anchor × each target distance × N seeds →
-- generate, score, keep the best few. Bounded, so the catalogue size is
-- predictable and reviewable before a user sees it.
--
-- ROUTES ARE DRAWN OVER OUR OWN EDGES with pgRouting, not over an external
-- engine's graph. That is the provider spike's verdict applied
-- (pipeline/docs/provider-comparison.md): every engine draws on the same OSM
-- ways we hold, and routing over curated.edge directly means the route IS an
-- edge sequence — difficulty, the MTB conjunction and ascent run along that
-- sequence natively, with no corridor match and none of its artefacts. An
-- external engine can still be plugged in later; it would only need the
-- corridor enrichment the spike already built.
--
-- THE ID IS DERIVED FROM GEOMETRY (draw/route_id.py), never a sequence number,
-- a run_id, or a vertex id. docs/social-layer.md imposes this before the first
-- comment exists: photos and likes key to route.id, vertex ids do not survive
-- a rebuild, and a regenerated route that follows the same ground must keep
-- its id while a genuinely different one becomes a NEW route.

CREATE TABLE IF NOT EXISTS curated.route (
    route_id        text PRIMARY KEY,     -- geometry-derived, stable across rebuilds
    kind            text NOT NULL DEFAULT 'generated',
    name            text,                 -- absent until trailhead naming is solved
    start_vertex    bigint NOT NULL REFERENCES curated.vertex (vertex_id)
                        ON DELETE CASCADE,
    target_m        double precision NOT NULL,   -- what was asked for
    distance_m      double precision NOT NULL,   -- what came out
    ascent_m        double precision,     -- along the walked direction; NULL if any
    descent_m       double precision,     --   edge profile is missing (absent ≠ 0)
    sac_scale       text,                 -- ≥5% rule over the walked edges
    mtb_rideable    boolean,              -- access conjunction along the sequence
    mtb_scale       text,
    surface         jsonb NOT NULL,       -- length-weighted distribution, kept whole
    off_road_share  double precision NOT NULL,
    retrace_share   double precision NOT NULL,   -- metres walked more than once / total
    score           double precision NOT NULL,   -- descriptive, NEVER a filter (route-pipeline.md)
    seed            integer NOT NULL,     -- which attempt produced it (determinism)
    geom            geometry(LineString, 4326) NOT NULL,
    run_id          text NOT NULL
);
CREATE INDEX IF NOT EXISTS route_geom_idx ON curated.route USING gist (geom);
CREATE INDEX IF NOT EXISTS route_start_idx ON curated.route (start_vertex);

-- The walked sequence: which edge, in what order, in which direction. This is
-- what "along the routed edge sequence" means — the MTB conjunction, the
-- ascent and the profile all read THIS, not a spatial match.
-- `forward` records whether the edge is walked source→target as stored;
-- walked backwards, ascent and descent SWAP (metadata-rules.md, directional
-- attributes) and the profile reverses.
CREATE TABLE IF NOT EXISTS curated.route_edge (
    route_id  text NOT NULL REFERENCES curated.route (route_id) ON DELETE CASCADE,
    seq       integer NOT NULL,
    edge_id   bigint NOT NULL REFERENCES curated.edge (edge_id) ON DELETE CASCADE,
    forward   boolean NOT NULL,
    PRIMARY KEY (route_id, seq)
);
CREATE INDEX IF NOT EXISTS route_edge_edge_idx ON curated.route_edge (edge_id);

-- The QGIS layer, with the same class discipline as everything else.
DROP VIEW IF EXISTS qa.v_draw;
CREATE VIEW qa.v_draw AS
SELECT r.route_id,
       r.start_vertex,
       round((r.target_m / 1000)::numeric, 0)   AS target_km,
       round((r.distance_m / 1000)::numeric, 2) AS km,
       round(r.ascent_m::numeric, 0)            AS ascent_m,
       r.sac_scale,
       r.mtb_rideable,
       r.off_road_share,
       r.retrace_share,
       r.score,
       r.seed,
       qa.difficulty_class(r.sac_scale)         AS difficulty_class,
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
            ELSE                              '1 not rideable' END AS mtb_class,
       r.geom
FROM curated.route r;
