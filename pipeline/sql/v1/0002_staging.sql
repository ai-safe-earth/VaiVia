-- Staging tables: raw sources, one table per source, never edited in place.
-- A reload TRUNCATEs and refills; nothing downstream references staging rows by
-- surrogate key, only by (osm_type, osm_id) or the source's own id.
--
-- Tags are kept whole as jsonb: the survey showed today's ingestion discarding
-- sac_scale/incline/access by choosing columns at the door. Columns exist only
-- for what every consumer filters on; everything else stays queryable in tags.

-- Routing-candidate ways: every highway-tagged way touching a region bbox,
-- full geometry, full tags. Legality flags are computed at load by the pure
-- functions in load/legality.py (deterministic from tags, so they belong with
-- the raw row; tests pin them).
CREATE TABLE IF NOT EXISTS staging.osm_way (
    way_id          bigint PRIMARY KEY,
    tags            jsonb NOT NULL,
    geom            geometry(LineString, 4326) NOT NULL,
    regions         text[] NOT NULL,
    routable_foot   boolean NOT NULL,
    routable_bike   boolean NOT NULL,
    legality_note   text,          -- why it was excluded, when it was
    run_id          text NOT NULL
);
CREATE INDEX IF NOT EXISTS osm_way_geom_idx ON staging.osm_way USING gist (geom);
CREATE INDEX IF NOT EXISTS osm_way_highway_idx
    ON staging.osm_way ((tags ->> 'highway'));

-- Named-route relations (route=hiking|foot|mtb|bicycle): the CAI sentieri
-- layer. Members are ordered [{type, ref, role}, ...] exactly as OSM orders
-- them; geometry is resolved downstream against osm_way, not stored twice.
CREATE TABLE IF NOT EXISTS staging.osm_relation (
    rel_id      bigint PRIMARY KEY,
    tags        jsonb NOT NULL,
    members     jsonb NOT NULL,
    regions     text[] NOT NULL,
    run_id      text NOT NULL
);

-- POIs: nodes AND areas (the hut-as-node-only bug is fixed at the query, not
-- patched later). Point geometry for nodes, polygon for areas; the polygon is
-- kept whole -- PostGIS has no reason to sample a boundary to 100 points.
CREATE TABLE IF NOT EXISTS staging.osm_poi (
    osm_type    char(1) NOT NULL,             -- n | w | r
    osm_id      bigint NOT NULL,
    poi_type    text NOT NULL,                -- peak | hut | lake | parking | ...
    name        text,
    ele_m       double precision,             -- from the ele tag, peaks mostly
    tags        jsonb NOT NULL,
    geom        geometry(Geometry, 4326) NOT NULL,
    regions     text[] NOT NULL,
    run_id      text NOT NULL,
    PRIMARY KEY (osm_type, osm_id)
);
CREATE INDEX IF NOT EXISTS osm_poi_geom_idx ON staging.osm_poi USING gist (geom);
CREATE INDEX IF NOT EXISTS osm_poi_type_idx ON staging.osm_poi (poi_type);

-- Settlements, for the start rule: place nodes and residential landuse areas.
CREATE TABLE IF NOT EXISTS staging.settlement (
    osm_type    char(1) NOT NULL,
    osm_id      bigint NOT NULL,
    kind        text NOT NULL,                -- town | village | hamlet | residential
    name        text,
    geom        geometry(Geometry, 4326) NOT NULL,
    regions     text[] NOT NULL,
    run_id      text NOT NULL,
    PRIMARY KEY (osm_type, osm_id)
);
CREATE INDEX IF NOT EXISTS settlement_geom_idx ON staging.settlement USING gist (geom);

-- GTFS stops with evidence of service: a stop with no trips is a sign, not a
-- way home. One row per (feed, stop).
CREATE TABLE IF NOT EXISTS staging.gtfs_stop (
    feed        text NOT NULL,
    stop_id     text NOT NULL,
    name        text,
    geom        geometry(Point, 4326) NOT NULL,
    n_trips     integer NOT NULL,
    regions     text[] NOT NULL,
    run_id      text NOT NULL,
    PRIMARY KEY (feed, stop_id)
);
CREATE INDEX IF NOT EXISTS gtfs_stop_geom_idx ON staging.gtfs_stop USING gist (geom);

-- The DEM raster is loaded by raster2pgsql into staging.dem (created by that
-- tool); this file only reserves the name in documentation.
