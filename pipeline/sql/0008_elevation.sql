-- Elevation: the DEM sampled onto the network.
--
-- 225 Copernicus GLO-30 tiles were loaded on 2026-08-19 and read by nothing.
-- This is the second of the two joins that make the network describable --
-- names came from the route relations, height comes from here, and everything
-- about difficulty needs it.
--
-- SAMPLED BILINEAR, AND THAT IS THE WHOLE DESIGN. Measured 2026-08-20 over
-- ~5,000 edges (33,023 consecutive point pairs), nearest-neighbour against
-- bilinear:
--
--   |            | median |dz| | p90 |dz| | pairs implying >100% slope | ascent |
--   | nearest    |   0.024 m   |  8.61 m  |   1,926 (5.83%)            | 42,014 |
--   | bilinear   |   1.049 m   |  4.13 m  |      38 (0.12%)            | 28,610 |
--
-- OSM points sit a median 9.4 m apart and the DEM cell is 30 m, so the network
-- is sampled three times finer than the raster it reads. Nearest-neighbour
-- therefore returns the SAME cell value for several points in a row and then
-- jumps a whole cell: median |dz| of 24 mm punctuated by 8 m steps, which is a
-- staircase, not a hillside. Summing the positive part of a staircase invents
-- climb -- 47% of it here. Bilinear interpolates across the cell and the
-- staircase disappears.
--
-- NO NOISE THRESHOLD, AND THAT WAS MEASURED TOO. The obvious next move is to
-- ignore small |dz| as DEM noise. Binning the same pairs by point spacing says
-- not to:
--
--   |  dx band  | pairs  | median |dz| | median slope |
--   |   0-2 m   |    790 |    0.12 m   |     9.4%     |
--   |   2-5 m   |  5,976 |    0.50 m   |    14.1%     |
--   |  5-10 m   | 10,056 |    0.93 m   |    12.8%     |
--   | 10-20 m   |  9,850 |    1.46 m   |    10.7%     |
--   | 20-30 m   |  3,431 |    2.09 m   |     8.6%     |
--
-- |dz| scales with distance and never plateaus: at sub-2 m spacing it is
-- 0.12 m, not the ~1 m a noise floor would leave behind. Median slope holds at
-- 9-14% across every band, which is what a mountain path is. There is no noise
-- to threshold away, so thresholding would only remove real terrain. (Under
-- nearest-neighbour the same table is bimodal -- that bimodality was the
-- artefact, and it is gone.)
--
-- ABSOLUTE ACCURACY, against the OSM `ele` tag on 567 POIs:
--
--   | class     |   n | mean bias | median bias | median |err| |
--   | saddle    | 160 |   +0.5 m  |    +0.2 m   |    4.1 m     |
--   | peak      | 385 |  -23.3 m  |   -11.0 m   |   11.5 m     |
--   | viewpoint |  11 |  -14.3 m  |    -8.3 m   |    8.3 m     |
--
-- Saddles are the honest test and the DEM passes it at ~4 m. Peaks read low by
-- design: a 30 m cell averages a summit with the slopes falling away from it,
-- so a sharp convex feature is smoothed down. Trails run on slopes, not on
-- knife-edges, so the saddle figure is the one that describes this network --
-- but a peak's elevation must come from its `ele` tag, never from the DEM.

-- One value per vertex. The routing graph is vertex-based, a vertex is shared
-- by several edges, and elevation_change on a routing edge is a difference of
-- two of these -- so it needs one authoritative number per vertex rather than
-- one per edge endpoint.
ALTER TABLE curated.vertex ADD COLUMN IF NOT EXISTS elevation_m double precision;

-- The profile is stored, not just its summary. metadata-rules.md requires a
-- route's ascent to come from the altitude profile rather than from per-piece
-- sums, so the profile has to survive assembly: `profile_m` holds one sample
-- per point of `geom`, in geometry order, so concatenating two edges
-- concatenates two real profiles.
--
-- ascent_m / descent_m are the summary over that profile, and they are
-- DIRECTIONAL in exactly the sense metadata-rules.md gives `oneway` and
-- `incline`: they are measured along the stored geometry, source -> target, so
-- reversing a piece during assembly SWAPS them. Reading ascent_m off a piece
-- being walked backwards is the same class of error as reading oneway=yes off
-- a reversed piece.
ALTER TABLE curated.edge ADD COLUMN IF NOT EXISTS profile_m double precision[];
ALTER TABLE curated.edge ADD COLUMN IF NOT EXISTS ascent_m double precision;
ALTER TABLE curated.edge ADD COLUMN IF NOT EXISTS descent_m double precision;

-- A gap in the profile makes the whole edge's climb unknown, not smaller. 57
-- vertices sit north of 46.0001 where the single GLO-30 tile ends (the loader
-- keeps a whole way that touches a region bbox, so ways spill past it), and an
-- edge touching that band gets NULL ascent rather than the climb of the part
-- that happens to be covered. Same rule as the semantic-search endpoint
-- returning 503 instead of an empty list: absent is not zero.
COMMENT ON COLUMN curated.edge.ascent_m IS
    'Metres climbed along the stored geometry, source -> target. Reversing the '
    'piece swaps ascent and descent. NULL when any profile sample is missing.';
COMMENT ON COLUMN curated.edge.profile_m IS
    'DEM elevation per point of geom, in geometry order, bilinear. Length '
    'equals ST_NPoints(geom). NULL entries are points outside DEM coverage.';
COMMENT ON COLUMN curated.vertex.elevation_m IS
    'Copernicus GLO-30, bilinear. NULL outside DEM coverage.';

-- The network with height, for QGIS: style by gradient, filter the steep.
-- `gradient` is the net rise over the run, signed along the stored direction;
-- it is NOT the mean of the local slopes, which is why a switchbacked edge can
-- climb 40 m at a 5% gradient.
CREATE OR REPLACE VIEW qa.v_elevation AS
SELECT e.edge_id,
       e.way_id,
       e.length_m,
       e.ascent_m,
       e.descent_m,
       e.ascent_m - e.descent_m                            AS net_m,
       CASE WHEN e.length_m > 0
            THEN (e.ascent_m - e.descent_m) / e.length_m
       END                                                 AS gradient,
       array_length(e.profile_m, 1)                        AS profile_points,
       e.profile_m[1]                                      AS start_m,
       e.profile_m[array_length(e.profile_m, 1)]           AS end_m,
       e.tags ->> 'highway'                                AS highway,
       e.tags ->> 'sac_scale'                              AS sac_scale,
       e.tags ->> 'name'                                   AS name,
       e.geom
FROM curated.edge e;

-- Climb per named route: the first number a walker asks for after distance.
--
-- Joined through DISTINCT (rel_id, edge_id), not through curated.edge_route
-- directly. A way listed twice in one relation produces two links to the same
-- edge, and summing over links would count that edge's length and climb twice
-- -- 123 such links across 20 relations, see 0009. The link table's grain is
-- deliberately finer than "which edges does this route use", so any aggregate
-- over edges has to collapse it first.
--
-- Ascent is summed over the route's edges AS STORED, which is correct only
-- because ascent and descent are both kept -- an assembled route that reverses
-- a piece swaps that piece's two figures, and this view does not know the
-- traversal direction. So read it as "the climb contained in this route's
-- edges", not "the climb of walking it end to end". Generating the walked
-- profile is route assembly's job, and profile_m is stored so it can.
CREATE OR REPLACE VIEW qa.v_route_elevation AS
SELECT r.rel_id,
       r.tags ->> 'ref'   AS ref,
       r.tags ->> 'name'  AS name,
       r.tags ->> 'route' AS route_kind,
       count(*)                                               AS edges,
       round((sum(e.length_m) / 1000)::numeric, 2)            AS km,
       round(sum(e.ascent_m)::numeric, 0)                     AS ascent_m,
       round(sum(e.descent_m)::numeric, 0)                    AS descent_m,
       -- Over the whole profile, not over the edges' first points: the summit
       -- of a route is a point in the middle of some edge, not the start of one.
       round(min(pz.lo)::numeric, 0)                          AS lowest_m,
       round(max(pz.hi)::numeric, 0)                          AS highest_m,
       count(*) FILTER (WHERE e.ascent_m IS NULL)             AS edges_without_profile,
       ST_Multi(ST_LineMerge(ST_Collect(e.geom)))             AS geom
FROM staging.osm_relation r
JOIN (SELECT DISTINCT rel_id, edge_id FROM curated.edge_route) er ON er.rel_id = r.rel_id
JOIN curated.edge e ON e.edge_id = er.edge_id
LEFT JOIN LATERAL (
    SELECT min(z) AS lo, max(z) AS hi FROM unnest(e.profile_m) AS z
) pz ON true
GROUP BY r.rel_id, r.tags;
