-- Places snapped to the network: POIs, settlements, transit stops.
--
-- The fourth staged source to be read, and the last of the "join it onto the
-- network" jobs. One row per feature, carrying the vertex it snapped to, how
-- far that was, and whether a walk can begin there.
--
-- NO THRESHOLD. The distance is stored and nothing is dropped for being far.
-- A threshold here would be a product decision ("how close must a car park be
-- to count?") baked into a build step where nobody can see it, and
-- docs/route-pipeline.md already settled that argument for the off-road score:
-- descriptive, not a filter. Consumers filter on `distance_m`; the build only
-- reports it. That also means there is no tolerance to justify from a
-- histogram, because there is no tolerance.
--
-- NEAREST VERTEX, NOT NEAREST EDGE. A place is attached to the network so a
-- route can START there, and a route starts at a routing vertex. The other
-- question -- which places a route PASSES -- is deliberately not answered here:
-- metadata-rules.md settles it at assembly, positioning each POI along the
-- MERGED line with ST_LineLocatePoint, and precomputing a place-to-edge table
-- would answer it with a radius nobody chose, 117,000 rows at 50 m.
--
-- SEARCH PLANAR, MEASURE GEODESIC. Candidate selection uses the KNN operator
-- against a GiST index on ST_Transform(geom, 32632) -- 7,471 car parks resolve
-- in 2.6 s that way. The distance actually stored is
-- ST_Distance(::geography, ::geography), the same true-metres measure as every
-- qa.finding, so no number in this store means "metres in UTM" while its
-- neighbour means "metres on the ellipsoid". The two disagree by ~0.04% over
-- Lombardy, far inside the ranking, so the cheap search cannot pick a
-- different vertex than the exact one would.
--
-- POLYGONS ARE KEPT WHOLE. 7,280 of the 7,471 car parks, 376 of the 377 lakes
-- and 66 of the 74 huts are areas, and staging kept them as areas on purpose.
-- Distance is measured from the polygon, so a car park 60 m across that touches
-- a lane is 0 m from the network, not 30. `geom` here is ST_PointOnSurface --
-- a marker guaranteed to lie inside the feature, for drawing, never for
-- measuring.

CREATE TABLE IF NOT EXISTS curated.place (
    source      text NOT NULL,               -- poi | settlement | gtfs_stop
    source_id   text NOT NULL,               -- n1234 | w5678 | trenord:S01
    kind        text NOT NULL,               -- poi_type, settlement kind, or 'stop'
    name        text,
    ele_m       double precision,            -- the OSM ele tag, never the DEM
    vertex_id   bigint NOT NULL REFERENCES curated.vertex (vertex_id) ON DELETE CASCADE,
    distance_m  double precision NOT NULL,   -- geodesic, feature to that vertex
    is_start    boolean NOT NULL,            -- curate/anchors.py
    start_note  text,                        -- why not, when it is not
    n_trips     integer,                     -- gtfs_stop only: evidence of service
    regions     text[] NOT NULL,
    geom        geometry(Point, 4326) NOT NULL,  -- ST_PointOnSurface, for drawing
    run_id      text NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS place_geom_idx ON curated.place USING gist (geom);
CREATE INDEX IF NOT EXISTS place_vertex_idx ON curated.place (vertex_id);
CREATE INDEX IF NOT EXISTS place_kind_idx ON curated.place (kind);
CREATE INDEX IF NOT EXISTS place_start_idx ON curated.place (is_start) WHERE is_start;

-- Planar indexes, so the KNN search is index-backed in metres.
--
-- 0004 added ::geography indexes because ST_DWithin in metres could not use a
-- plain geometry index. Those serve a RANGE predicate well and a nearest
-- neighbour over POLYGONS badly: the same 7,471 car parks take 2.6 s through
-- ST_Transform(geom, 32632) and did not finish in four minutes through a
-- geography range join. Both indexes are worth their space -- they answer
-- different questions.
CREATE INDEX IF NOT EXISTS vertex_utm_idx
    ON curated.vertex USING gist (ST_Transform(geom, 32632));
CREATE INDEX IF NOT EXISTS poi_utm_idx
    ON staging.osm_poi USING gist (ST_Transform(geom, 32632));
CREATE INDEX IF NOT EXISTS settlement_utm_idx
    ON staging.settlement USING gist (ST_Transform(geom, 32632));
CREATE INDEX IF NOT EXISTS gtfs_stop_utm_idx
    ON staging.gtfs_stop USING gist (ST_Transform(geom, 32632));

-- The temporary indexes used while measuring this, under the names they were
-- created with. Dropped so the store holds only what a migration put there.
DROP INDEX IF EXISTS staging.tmp_poi_utm;
DROP INDEX IF EXISTS curated.tmp_edge_utm;
DROP INDEX IF EXISTS curated.tmp_vertex_utm;

-- Every place, for QGIS. Point layer; style by `kind`, filter on `is_start`.
DROP VIEW IF EXISTS qa.v_place;
CREATE VIEW qa.v_place AS
SELECT p.source, p.source_id, p.kind, p.name, p.ele_m,
       p.vertex_id, p.distance_m, p.is_start, p.start_note, p.n_trips,
       p.regions, v.component_id, p.geom
FROM curated.place p
JOIN curated.vertex v ON v.vertex_id = p.vertex_id;

-- THE LAYER TO OPEN FIRST when judging a snap: a line from each place to the
-- vertex it attached to. A wrong snap is invisible as a number and obvious as a
-- line reaching across a valley or through a wall. Sort by distance_m
-- descending and look at the top of the list.
DROP VIEW IF EXISTS qa.v_place_link;
CREATE VIEW qa.v_place_link AS
SELECT p.source, p.source_id, p.kind, p.name, p.distance_m, p.is_start,
       ST_MakeLine(p.geom, v.geom) AS geom
FROM curated.place p
JOIN curated.vertex v ON v.vertex_id = p.vertex_id
WHERE NOT ST_Equals(p.geom, v.geom);

-- Where a walk can begin. One row per vertex, with what makes it a start --
-- several car parks often snap to the same lane end, and that vertex is one
-- trailhead, not four.
--
-- Naming is left undone on purpose. docs/route-pipeline.md records that only 37
-- of 266 trailheads had a name because car parks are rarely named in OSM, and
-- that naming one from a nearby feature is unsolved. `names` here is whatever
-- the anchors actually carry; inventing a name from the nearest peak is a
-- decision that has not been made yet.
DROP VIEW IF EXISTS qa.v_start;
CREATE VIEW qa.v_start AS
SELECT p.vertex_id,
       v.component_id,
       vd.degree,
       count(*)                                                   AS anchors,
       array_agg(DISTINCT p.kind ORDER BY p.kind)                 AS kinds,
       array_remove(array_agg(DISTINCT p.name), NULL)             AS names,
       round(min(p.distance_m)::numeric, 1)                       AS nearest_m,
       sum(p.n_trips)                                             AS trips,
       bool_or(p.source = 'gtfs_stop' OR p.kind = 'station')       AS car_free,
       v.geom
FROM curated.place p
JOIN curated.vertex v ON v.vertex_id = p.vertex_id
LEFT JOIN curated.vertex_degree vd ON vd.vertex_id = p.vertex_id
WHERE p.is_start
GROUP BY p.vertex_id, v.component_id, vd.degree, v.geom;
