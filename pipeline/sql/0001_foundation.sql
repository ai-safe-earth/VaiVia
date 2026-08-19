-- Pipeline foundation: extensions, schemas, provenance.
--
-- Three tiers (docs/plan.md, ratified 2026-08-19):
--   staging  raw data as fetched, one table per source, never edited in place
--   curated  the product: routes, POIs, starts -- what the tile server serves
--            and the Neo4j export reads
--   qa       findings and fixes; every row carries geometry so each rule is a
--            QGIS layer
--
-- The database is the product, so runs must be diffable inside it: every
-- curated row carries the run_id that produced it, and build_run records what
-- each run was.
--
-- Idempotent: apply twice, identical schema (verification rule, docs/plan.md).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
CREATE EXTENSION IF NOT EXISTS pgrouting;

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS curated;
CREATE SCHEMA IF NOT EXISTS qa;

-- One row per pipeline run. Parameters and counts live here so a run is
-- reproducible from its row and two runs are comparable without a file export.
CREATE TABLE IF NOT EXISTS build_run (
    run_id      text PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    stage       text NOT NULL,            -- 'load' | 'topology' | 'draw' | 'curate' | 'export'
    parameters  jsonb NOT NULL DEFAULT '{}'::jsonb,
    counts      jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes       text
);

-- QA findings: one row per detected problem, one rule per QGIS layer.
-- Geometry is generic because a gap is a point, an overlap is a line, and a
-- suspect polygon is a polygon.
CREATE TABLE IF NOT EXISTS qa.finding (
    finding_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id      text NOT NULL REFERENCES build_run (run_id),
    rule        text NOT NULL,             -- 'gap' | 'dangle' | 'overlap' | ...
    severity    text NOT NULL DEFAULT 'warning',  -- 'error' | 'warning' | 'info'
    geom        geometry(Geometry, 4326) NOT NULL,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS finding_geom_idx ON qa.finding USING gist (geom);
CREATE INDEX IF NOT EXISTS finding_rule_idx ON qa.finding (rule, run_id);

-- QA fixes: every automated repair, with before and after, so nothing changes
-- silently and a bad tolerance is reversible.
CREATE TABLE IF NOT EXISTS qa.fix (
    fix_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id      text NOT NULL REFERENCES build_run (run_id),
    rule        text NOT NULL,
    target      text NOT NULL,             -- id of the row that was changed
    geom_before geometry(Geometry, 4326),
    geom_after  geometry(Geometry, 4326),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fix_rule_idx ON qa.fix (rule, run_id);
