-- Phase 12 R5: travel to the trailhead (docs/route-design.md, decision 3).
--
-- The pack answers "no more than an hour's drive from here" with a measured
-- settlement × start matrix and "by train" with a station × station one —
-- both computed at build time in this database, never per ask and never by
-- an external routing service. The car network is a WORKING artefact
-- (staging), rebuilt from the PBF each load; the matrices are curated
-- output (source_map / staging.rail_min) the pack export reads.

-- Raw drivable ways, clipped to the region bboxes at load time.
CREATE TABLE IF NOT EXISTS staging.car_road (
    way_id    bigint PRIMARY KEY,
    highway   text NOT NULL,
    oneway    text,
    maxspeed  text,
    geom      geometry(LineString, 4326) NOT NULL,
    run_id    text NOT NULL
);
CREATE INDEX IF NOT EXISTS car_road_geom_idx ON staging.car_road USING gist (geom);

-- The noded drive topology pgr_dijkstraCost runs over. cost_s is seconds in
-- the digitised direction; -1 is pgRouting's "no such arc" (a oneway's
-- reverse), the same convention as catalogue.v_edges_*.
CREATE TABLE IF NOT EXISTS staging.car_edge (
    edge_id        bigserial PRIMARY KEY,
    way_id         bigint NOT NULL,
    length_m       double precision NOT NULL,
    cost_s         double precision NOT NULL,
    reverse_cost_s double precision NOT NULL,
    source         bigint,
    target         bigint,
    geom           geometry(LineString, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS car_edge_geom_idx ON staging.car_edge USING gist (geom);

-- Minutes by car, settlement place -> start vertex. Rows are city / town /
-- village settlements only: a hamlet anchors to its nearest village, and
-- 2,000 rows of near-duplicate origins would double the pack for no better
-- answer. Replaced whole per curate.drive run.
CREATE TABLE IF NOT EXISTS source_map.drive_min (
    settlement_source_id text NOT NULL,
    start_vertex         bigint NOT NULL,
    minutes              real NOT NULL,
    run_id               text NOT NULL,
    PRIMARY KEY (settlement_source_id, start_vertex)
);

-- Minutes by rail between in-region stations, from the GTFS stop_times —
-- the measured timetable, not a guess. Symmetric pairs stored once each
-- direction (timetables are not symmetric). Replaced whole per curate.rail
-- run.
CREATE TABLE IF NOT EXISTS staging.rail_min (
    feed_stop_a text NOT NULL,
    feed_stop_b text NOT NULL,
    minutes     real NOT NULL,
    run_id      text NOT NULL,
    PRIMARY KEY (feed_stop_a, feed_stop_b)
);

-- The review surface: every start with its best drive time, banded so the
-- QGIS pass is "colour by drive_min_class", never an expression.
DROP VIEW IF EXISTS qa.v_drive_start;
CREATE VIEW qa.v_drive_start AS
SELECT s.vertex_id,
       s.geom,
       d.minutes,
       CASE
           WHEN d.minutes IS NULL THEN '9 unreached'
           WHEN d.minutes < 15 THEN '1 under 15 min'
           WHEN d.minutes < 30 THEN '2 15-30 min'
           WHEN d.minutes < 60 THEN '3 30-60 min'
           ELSE '4 over an hour'
       END AS drive_min_class
FROM qa.v_start s
LEFT JOIN LATERAL (
    SELECT min(minutes) AS minutes
    FROM source_map.drive_min dm
    WHERE dm.start_vertex = s.vertex_id
) d ON true;
