-- v2 baseline: the whole v1 chain (0001-0016), concatenated in order with
-- the v2 schema names applied -- source_map (was curated), catalogue (the
-- generated-route tables), provenance (was public.build_run) -- plus the two
-- pieces v1 kept outside the chain: staging.dem and the chain-version stamp.
--
-- v1 was replay-idempotent by doctrine (and by three verified full replays),
-- and a pure rename preserves that, so this file replays safely too. The v1
-- files are frozen under sql/v1/ for the record; a live v1 store is brought
-- here by convert_v2.py, never by this file.
--
-- The per-section comments below are the original migrations' own, kept
-- because they carry the measurements that justified each decision.

-- ==== from v1/0001_foundation.sql ====

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
-- curated row carries the run_id that produced it, and provenance.build_run records what
-- each run was.
--
-- Idempotent: apply twice, identical schema (verification rule, docs/plan.md).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
CREATE EXTENSION IF NOT EXISTS pgrouting;

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS source_map;
CREATE SCHEMA IF NOT EXISTS catalogue;
CREATE SCHEMA IF NOT EXISTS provenance;
CREATE SCHEMA IF NOT EXISTS qa;

-- One row per pipeline run. Parameters and counts live here so a run is
-- reproducible from its row and two runs are comparable without a file export.
CREATE TABLE IF NOT EXISTS provenance.build_run (
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
    run_id      text NOT NULL REFERENCES provenance.build_run (run_id),
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
    run_id      text NOT NULL REFERENCES provenance.build_run (run_id),
    rule        text NOT NULL,
    target      text NOT NULL,             -- id of the row that was changed
    geom_before geometry(Geometry, 4326),
    geom_after  geometry(Geometry, 4326),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fix_rule_idx ON qa.fix (rule, run_id);

-- ==== from v1/0002_staging.sql ====

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

-- ==== from v1/0003_network.sql ====

-- The noded routing network: source_map.vertex / source_map.edge.
--
-- Built by topology/build_network.py from staging.osm_way. Noding is
-- TOPOLOGICAL, not geometric: OSM ways share literal nodes at junctions, so a
-- junction is a coordinate used by two ways (or twice by one), never a mere
-- geometric crossing -- a bridge crosses the road below without touching it,
-- and geometric noding would weld them (the pgr_nodeNetwork trap).
--
-- Metadata rules on split are docs/metadata-rules.md; edges keep their parent
-- way's direction, so directional tags (oneway, incline) remain valid as
-- stored and invert only when route assembly reverses a piece.

CREATE TABLE IF NOT EXISTS source_map.vertex (
    vertex_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    geom         geometry(Point, 4326) NOT NULL,
    component_id bigint,               -- pgr_connectedComponents, written after build
    run_id       text NOT NULL
);
CREATE INDEX IF NOT EXISTS vertex_geom_idx ON source_map.vertex USING gist (geom);
-- Exact-equality dedup key: coordinates come from the same OSM node, so they
-- are bit-identical, and the binary form is what uniqueness means here.
CREATE UNIQUE INDEX IF NOT EXISTS vertex_geom_key ON source_map.vertex (geom);

CREATE TABLE IF NOT EXISTS source_map.edge (
    edge_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    way_id        bigint NOT NULL,     -- provenance: the parent OSM way
    piece_index   integer NOT NULL,    -- position of this piece along the way
    source        bigint NOT NULL REFERENCES source_map.vertex (vertex_id),
    target        bigint NOT NULL REFERENCES source_map.vertex (vertex_id),
    geom          geometry(LineString, 4326) NOT NULL,
    length_m      double precision NOT NULL,   -- recomputed per piece (32632)
    tags          jsonb NOT NULL,              -- inherited whole from the way
    routable_foot boolean NOT NULL,
    routable_bike boolean NOT NULL,
    regions       text[] NOT NULL,
    run_id        text NOT NULL,
    UNIQUE (way_id, piece_index)
);
CREATE INDEX IF NOT EXISTS edge_geom_idx ON source_map.edge USING gist (geom);
CREATE INDEX IF NOT EXISTS edge_source_idx ON source_map.edge (source);
CREATE INDEX IF NOT EXISTS edge_target_idx ON source_map.edge (target);
CREATE INDEX IF NOT EXISTS edge_highway_idx ON source_map.edge ((tags ->> 'highway'));

-- ==== from v1/0004_geography_indexes.sql ====

-- Geography indexes, so proximity queries in METRES are index-backed.
--
-- The QA detectors ask "what is within N metres", which means
-- ST_DWithin(a::geography, b::geography, N) — true distance, not degrees. A
-- GIST index on the plain geometry cannot serve that predicate: the planner
-- sees a cast and falls back to scanning every edge for every vertex. On
-- 80k vertices against 100k edges that is a full cross product, which is why
-- the first near-miss measurement had to be killed rather than waited out.
--
-- The ::geography cast is IMMUTABLE, so it can be indexed directly. This is
-- the same class of mistake as the per-node radius in the old graph
-- (backend/graph/queries.cypher, area_pois_near_point): a predicate that reads
-- naturally and quietly cannot use an index.
--
-- Metres-accurate work that needs a projected plane rather than a distance
-- still uses EPSG:32632 explicitly at the call site.

CREATE INDEX IF NOT EXISTS vertex_geog_idx
    ON source_map.vertex USING gist ((geom::geography));

CREATE INDEX IF NOT EXISTS edge_geog_idx
    ON source_map.edge USING gist ((geom::geography));

CREATE INDEX IF NOT EXISTS poi_geog_idx
    ON staging.osm_poi USING gist ((geom::geography));

CREATE INDEX IF NOT EXISTS settlement_geog_idx
    ON staging.settlement USING gist ((geom::geography));

CREATE INDEX IF NOT EXISTS gtfs_stop_geog_idx
    ON staging.gtfs_stop USING gist ((geom::geography));

-- Vertex degree is asked by every QA detector and by the start/end rule.
-- Materialised because computing it per query is a join over 100k edges each
-- time; refreshed by topology/build_network.py after a rebuild.
CREATE MATERIALIZED VIEW IF NOT EXISTS source_map.vertex_degree AS
SELECT v.vertex_id,
       v.geom,
       v.component_id,
       count(e.edge_id) AS degree
FROM source_map.vertex v
LEFT JOIN source_map.edge e ON e.source = v.vertex_id OR e.target = v.vertex_id
GROUP BY v.vertex_id, v.geom, v.component_id;

CREATE UNIQUE INDEX IF NOT EXISTS vertex_degree_id_idx
    ON source_map.vertex_degree (vertex_id);
CREATE INDEX IF NOT EXISTS vertex_degree_geog_idx
    ON source_map.vertex_degree USING gist ((geom::geography));
CREATE INDEX IF NOT EXISTS vertex_degree_degree_idx
    ON source_map.vertex_degree (degree);

-- ==== from v1/0005_qa_views.sql ====

-- QGIS-facing views: one per QA rule, latest run only.
--
-- QGIS loads a PostGIS layer per table or view. Filtering qa.finding by hand in
-- the layer dialog works but has to be redone every session, and the run_id of
-- "the latest run" changes each time — so the views do it. Each carries a
-- single geometry type where the rule produces one, because QGIS styles a
-- mixed-geometry layer poorly.
--
-- Every view is read-only by construction (no INSTEAD OF triggers): inspection
-- must not become a way to silently edit findings. Repairs go through
-- topology/repair.py, which records qa.fix.

-- The one view here that is REPLACED rather than dropped: every rule view below
-- selects from it, so a plain DROP fails on the dependency and a DROP CASCADE
-- would take them all with it and rely on the rest of the chain to rebuild them
-- in the right order. Its column list is frozen at one column for exactly this
-- reason -- widening it would need the cascade this avoids.
CREATE OR REPLACE VIEW qa.latest_run AS
SELECT run_id
FROM provenance.build_run
WHERE stage = 'topology' AND parameters ? 'detector'
ORDER BY started_at DESC
LIMIT 1;

DROP VIEW IF EXISTS qa.v_gap_dangle_pair;
CREATE VIEW qa.v_gap_dangle_pair AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'distance_m')::float AS distance_m,
       (f.note::json ->> 'a')::bigint AS vertex_a,
       (f.note::json ->> 'b')::bigint AS vertex_b
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'gap_dangle_pair' AND f.run_id = r.run_id;

DROP VIEW IF EXISTS qa.v_gap_dangle_edge;
CREATE VIEW qa.v_gap_dangle_edge AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'distance_m')::float AS distance_m,
       (f.note::json ->> 'vertex')::bigint AS vertex_id,
       (f.note::json ->> 'edge')::bigint AS edge_id
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'gap_dangle_edge' AND f.run_id = r.run_id;

DROP VIEW IF EXISTS qa.v_overlap;
CREATE VIEW qa.v_overlap AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'shared_m')::float AS shared_m,
       (f.note::json ->> 'a')::bigint AS edge_a,
       (f.note::json ->> 'b')::bigint AS edge_b
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'overlap' AND f.run_id = r.run_id;

DROP VIEW IF EXISTS qa.v_degenerate;
CREATE VIEW qa.v_degenerate AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'length_m')::float AS length_m,
       (f.note::json ->> 'self_loop')::boolean AS self_loop
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'degenerate' AND f.run_id = r.run_id;

DROP VIEW IF EXISTS qa.v_island;
CREATE VIEW qa.v_island AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'vertices')::int AS vertices,
       (f.note::json ->> 'component_id')::bigint AS component_id
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'island' AND f.run_id = r.run_id;

-- The network itself, for context beneath the findings: without a basemap of
-- what the edges are, a gap layer is nine unexplained lines on white.
DROP VIEW IF EXISTS qa.v_network;
CREATE VIEW qa.v_network AS
SELECT e.edge_id, e.way_id, e.geom, e.length_m,
       e.tags ->> 'highway' AS highway,
       e.tags ->> 'surface' AS surface,
       e.tags ->> 'sac_scale' AS sac_scale,
       e.tags ->> 'name' AS name,
       e.routable_foot, e.routable_bike, e.regions
FROM source_map.edge e;

-- Loose ends, the input to the gap rules: useful on its own when judging
-- whether a dangle is a defect or a real dead end.
DROP VIEW IF EXISTS qa.v_dangle;
CREATE VIEW qa.v_dangle AS
SELECT vertex_id, geom, component_id
FROM source_map.vertex_degree
WHERE degree = 1;

-- ==== from v1/0006_qa_junction.sql ====

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

DROP VIEW IF EXISTS qa.v_gap_dangle_junction;
CREATE VIEW qa.v_gap_dangle_junction AS
SELECT f.finding_id, f.severity, f.geom, f.note,
       (f.note::json ->> 'distance_m')::float AS distance_m,
       (f.note::json ->> 'vertex')::bigint AS vertex_id,
       (f.note::json ->> 'junction')::bigint AS junction_id
FROM qa.finding f, qa.latest_run r
WHERE f.rule = 'gap_dangle_junction' AND f.run_id = r.run_id;

-- Repairs are reviewable after the fact: every fix carries before/after
-- geometry, so this is the layer for "what did the last repair pass do".
DROP VIEW IF EXISTS qa.v_fix;
CREATE VIEW qa.v_fix AS
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

-- ==== from v1/0007_edge_route.sql ====

-- Route-relation membership: which edges carry which named route.
--
-- 752 route relations were loaded into staging on 2026-08-19 and, until now,
-- nothing read them. Their members are OSM way ids and source_map.edge.way_id is
-- exactly that, so this join already existed in the data and had simply never
-- been written. It is the largest metadata win available to the network: it
-- turns anonymous edges into "sentiero 6, Traversata Bassa delle Grigne".
--
-- A LINK TABLE, NOT A COLUMN ON edge. Measured against the built network,
-- 5,295 edges belong to more than one relation (a sentiero shared with a
-- Bicitalia route, a variante rejoining its parent). A column would have to
-- pick one and silently discard the rest.
--
-- The relation's own tags are NOT copied here. staging.osm_relation is the
-- source of truth for ref/name/network/osmc:symbol, and a second copy is a
-- second thing to keep in step -- the same argument that keeps edge tags in
-- `tags` instead of promoting them to columns. The views below do the join.
--
-- ORDER. `member_index` is the member's position in the relation's member list,
-- which is the order along the route; `piece_index` is the edge's position
-- along its parent way. Together they order the route's edges as OSM ordered
-- them. Neither says which DIRECTION the route traverses a piece: a member way
-- can be walked backwards along the route, and resolving that is route
-- assembly's job (pipeline/docs/metadata-rules.md, "on join"), not the link's.
-- Storing provenance and deriving direction later is the rule everywhere else
-- in curated; it holds here.
--
-- STALENESS. The link points into edge_ids, so it describes ONE build of the
-- network. build_network (which replaces the network) and repair (which splits
-- and deletes edges) both clear this table rather than leave it partly true --
-- an empty table is visibly missing, a partly-stale one lies. That is the
-- lesson from source_map.vertex_degree on 2026-08-19.
CREATE TABLE IF NOT EXISTS source_map.edge_route (
    edge_id      bigint NOT NULL REFERENCES source_map.edge (edge_id) ON DELETE CASCADE,
    rel_id       bigint NOT NULL,          -- provenance: the OSM relation id
    member_index integer NOT NULL,         -- position in the relation's members
    piece_index  integer NOT NULL,         -- position of the edge along its way
    role         text,                     -- member role, NULL when OSM gave ''
    run_id       text NOT NULL,
    -- A way may appear twice in the same relation (140 cases measured), so
    -- (edge_id, rel_id) is not unique -- the member position completes the key.
    PRIMARY KEY (edge_id, rel_id, member_index)
);
CREATE INDEX IF NOT EXISTS edge_route_rel_idx ON source_map.edge_route (rel_id);
CREATE INDEX IF NOT EXISTS edge_route_edge_idx ON source_map.edge_route (edge_id);

-- The network, named. This is the QGIS layer the join exists for: every edge
-- that belongs to a route, with the route's identity as real columns so it can
-- be styled and filtered without a jsonb expression. An edge in two relations
-- appears twice, once per route -- that is the point of the table.
DROP VIEW IF EXISTS qa.v_route_edge;
CREATE VIEW qa.v_route_edge AS
SELECT er.edge_id,
       er.rel_id,
       er.member_index,
       er.piece_index,
       er.role,
       r.tags ->> 'ref'          AS ref,
       r.tags ->> 'name'         AS name,
       r.tags ->> 'route'        AS route_kind,   -- hiking | bicycle | mtb | foot
       r.tags ->> 'network'      AS network,      -- lwn | rwn | nwn | iwn | lcn ...
       r.tags ->> 'osmc:symbol'  AS osmc_symbol,
       e.length_m,
       e.tags ->> 'highway'      AS highway,
       e.tags ->> 'surface'      AS surface,
       e.tags ->> 'sac_scale'    AS sac_scale,
       e.routable_foot,
       e.routable_bike,
       e.geom
FROM source_map.edge_route er
JOIN source_map.edge e ON e.edge_id = er.edge_id
JOIN staging.osm_relation r ON r.rel_id = er.rel_id;

-- One feature per route: 752 lines instead of 102,000, which is the layer to
-- open first. The geometry is merged where the edges connect and stays a
-- MULTILINESTRING where they do not -- a route in several pieces LOOKS like a
-- route in several pieces, which is exactly what wants judging before anything
-- is generated on top of it.
DROP VIEW IF EXISTS qa.v_route;
CREATE VIEW qa.v_route AS
SELECT r.rel_id,
       r.tags ->> 'ref'         AS ref,
       r.tags ->> 'name'        AS name,
       r.tags ->> 'route'       AS route_kind,
       r.tags ->> 'network'     AS network,
       r.tags ->> 'osmc:symbol' AS osmc_symbol,
       r.regions,
       count(DISTINCT er.edge_id)                       AS edges,
       round((sum(e.length_m) / 1000)::numeric, 2)      AS km,
       ST_NumGeometries(ST_Multi(ST_LineMerge(ST_Collect(e.geom)))) AS pieces,
       ST_Multi(ST_LineMerge(ST_Collect(e.geom)))       AS geom
FROM staging.osm_relation r
JOIN source_map.edge_route er ON er.rel_id = r.rel_id
JOIN source_map.edge e ON e.edge_id = er.edge_id
GROUP BY r.rel_id, r.tags, r.regions;

-- Which relations the join could not fully place, and why it is expected. A
-- member way is absent from source_map.edge when it falls outside both region
-- bboxes or when load/legality.py excluded it (a road no one may walk). The
-- number that matters is matched_ways / way_members: a route at 0.9 is clipped
-- at the edge of coverage, a route at 0.1 is a route this network cannot hold.
DROP VIEW IF EXISTS qa.v_route_coverage;
CREATE VIEW qa.v_route_coverage AS
WITH members AS (
    SELECT r.rel_id,
           count(*) FILTER (WHERE m ->> 'type' = 'w') AS way_members,
           count(DISTINCT (m ->> 'ref')) FILTER (WHERE m ->> 'type' = 'w')
                                                      AS distinct_way_members,
           count(*) FILTER (WHERE m ->> 'type' = 'n') AS node_members,
           count(*) FILTER (WHERE m ->> 'type' = 'r') AS relation_members
    FROM staging.osm_relation r,
         LATERAL jsonb_array_elements(r.members) m
    GROUP BY r.rel_id
)
SELECT r.rel_id,
       r.tags ->> 'ref'  AS ref,
       r.tags ->> 'name' AS name,
       m.way_members,
       m.distinct_way_members,
       m.node_members,
       m.relation_members,
       count(DISTINCT e.way_id) AS matched_ways,
       count(DISTINCT er.edge_id) AS matched_edges,
       -- Against DISTINCT member ways: a way listed twice in one relation
       -- (140 cases) must not make a fully matched route look half matched.
       CASE WHEN m.distinct_way_members = 0 THEN NULL
            ELSE round(count(DISTINCT e.way_id)::numeric / m.distinct_way_members, 3)
       END AS matched_fraction
FROM staging.osm_relation r
JOIN members m ON m.rel_id = r.rel_id
LEFT JOIN source_map.edge_route er ON er.rel_id = r.rel_id
LEFT JOIN source_map.edge e ON e.edge_id = er.edge_id
GROUP BY r.rel_id, r.tags, m.way_members, m.distinct_way_members,
         m.node_members, m.relation_members;

-- ==== from v1/0008_elevation.sql ====

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
ALTER TABLE source_map.vertex ADD COLUMN IF NOT EXISTS elevation_m double precision;

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
ALTER TABLE source_map.edge ADD COLUMN IF NOT EXISTS profile_m double precision[];
ALTER TABLE source_map.edge ADD COLUMN IF NOT EXISTS ascent_m double precision;
ALTER TABLE source_map.edge ADD COLUMN IF NOT EXISTS descent_m double precision;

-- A gap in the profile makes the whole edge's climb unknown, not smaller. 57
-- vertices sit north of 46.0001 where the single GLO-30 tile ends (the loader
-- keeps a whole way that touches a region bbox, so ways spill past it), and an
-- edge touching that band gets NULL ascent rather than the climb of the part
-- that happens to be covered. Same rule as the semantic-search endpoint
-- returning 503 instead of an empty list: absent is not zero.
COMMENT ON COLUMN source_map.edge.ascent_m IS
    'Metres climbed along the stored geometry, source -> target. Reversing the '
    'piece swaps ascent and descent. NULL when any profile sample is missing.';
COMMENT ON COLUMN source_map.edge.profile_m IS
    'DEM elevation per point of geom, in geometry order, bilinear. Length '
    'equals ST_NPoints(geom). NULL entries are points outside DEM coverage.';
COMMENT ON COLUMN source_map.vertex.elevation_m IS
    'Copernicus GLO-30, bilinear. NULL outside DEM coverage.';

-- The network with height, for QGIS: style by gradient, filter the steep.
-- `gradient` is the net rise over the run, signed along the stored direction;
-- it is NOT the mean of the local slopes, which is why a switchbacked edge can
-- climb 40 m at a 5% gradient.
DROP VIEW IF EXISTS qa.v_elevation;
CREATE VIEW qa.v_elevation AS
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
FROM source_map.edge e;

-- Climb per named route: the first number a walker asks for after distance.
--
-- Joined through DISTINCT (rel_id, edge_id), not through source_map.edge_route
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
DROP VIEW IF EXISTS qa.v_route_elevation;
CREATE VIEW qa.v_route_elevation AS
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
JOIN (SELECT DISTINCT rel_id, edge_id FROM source_map.edge_route) er ON er.rel_id = r.rel_id
JOIN source_map.edge e ON e.edge_id = er.edge_id
LEFT JOIN LATERAL (
    SELECT min(z) AS lo, max(z) AS hi FROM unnest(e.profile_m) AS z
) pz ON true
GROUP BY r.rel_id, r.tags;

-- ==== from v1/0009_route_view_distinct.sql ====

-- qa.v_route double-counted an edge that a relation lists twice.
--
-- 0007 defined source_map.edge_route with one row per (edge, relation, member
-- position) precisely because a way may appear twice in the same relation -- an
-- out-and-back leg walked in both directions, 140 measured cases. That grain is
-- right for the link. It is wrong for any aggregate over EDGES, and qa.v_route
-- joined the link table directly and summed length per link.
--
-- Measured 2026-08-20: 123 links across 20 relations resolve to an edge already
-- counted, so those routes reported up to 3.04 km more than they hold. The
-- Dorsale Orobica Lecchese read 44.17 km against an actual 41.13.
--
-- The fix is to collapse the link to DISTINCT (rel_id, edge_id) before
-- aggregating. `edges` becomes a plain count for the same reason -- it was
-- already count(DISTINCT edge_id) and therefore correct, which is exactly why
-- the discrepancy was invisible: the edge count was right while the kilometres
-- beside it were not.
DROP VIEW IF EXISTS qa.v_route;
CREATE VIEW qa.v_route AS
SELECT r.rel_id,
       r.tags ->> 'ref'         AS ref,
       r.tags ->> 'name'        AS name,
       r.tags ->> 'route'       AS route_kind,
       r.tags ->> 'network'     AS network,
       r.tags ->> 'osmc:symbol' AS osmc_symbol,
       r.regions,
       count(*)                                                    AS edges,
       round((sum(e.length_m) / 1000)::numeric, 2)                 AS km,
       ST_NumGeometries(ST_Multi(ST_LineMerge(ST_Collect(e.geom)))) AS pieces,
       ST_Multi(ST_LineMerge(ST_Collect(e.geom)))                  AS geom
FROM staging.osm_relation r
JOIN (SELECT DISTINCT rel_id, edge_id FROM source_map.edge_route) er ON er.rel_id = r.rel_id
JOIN source_map.edge e ON e.edge_id = er.edge_id
GROUP BY r.rel_id, r.tags, r.regions;

-- Same collapse for the coverage view, which counted matched_edges off the raw
-- link table. matched_ways was already DISTINCT and unaffected.
DROP VIEW IF EXISTS qa.v_route_coverage;
CREATE VIEW qa.v_route_coverage AS
WITH members AS (
    SELECT r.rel_id,
           count(*) FILTER (WHERE m ->> 'type' = 'w') AS way_members,
           count(DISTINCT (m ->> 'ref')) FILTER (WHERE m ->> 'type' = 'w')
                                                      AS distinct_way_members,
           count(*) FILTER (WHERE m ->> 'type' = 'n') AS node_members,
           count(*) FILTER (WHERE m ->> 'type' = 'r') AS relation_members
    FROM staging.osm_relation r,
         LATERAL jsonb_array_elements(r.members) m
    GROUP BY r.rel_id
)
SELECT r.rel_id,
       r.tags ->> 'ref'  AS ref,
       r.tags ->> 'name' AS name,
       m.way_members,
       m.distinct_way_members,
       m.node_members,
       m.relation_members,
       count(DISTINCT e.way_id) AS matched_ways,
       count(DISTINCT er.edge_id) AS matched_edges,
       -- Against DISTINCT member ways: a way listed twice in one relation
       -- (140 cases) must not make a fully matched route look half matched.
       CASE WHEN m.distinct_way_members = 0 THEN NULL
            ELSE round(count(DISTINCT e.way_id)::numeric / m.distinct_way_members, 3)
       END AS matched_fraction
FROM staging.osm_relation r
JOIN members m ON m.rel_id = r.rel_id
LEFT JOIN source_map.edge_route er ON er.rel_id = r.rel_id
LEFT JOIN source_map.edge e ON e.edge_id = er.edge_id
GROUP BY r.rel_id, r.tags, m.way_members, m.distinct_way_members,
         m.node_members, m.relation_members;

-- ==== from v1/0010_place.sql ====

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

CREATE TABLE IF NOT EXISTS source_map.place (
    source      text NOT NULL,               -- poi | settlement | gtfs_stop
    source_id   text NOT NULL,               -- n1234 | w5678 | trenord:S01
    kind        text NOT NULL,               -- poi_type, settlement kind, or 'stop'
    name        text,
    ele_m       double precision,            -- the OSM ele tag, never the DEM
    vertex_id   bigint NOT NULL REFERENCES source_map.vertex (vertex_id) ON DELETE CASCADE,
    distance_m  double precision NOT NULL,   -- geodesic, feature to that vertex
    is_start    boolean NOT NULL,            -- curate/anchors.py
    start_note  text,                        -- why not, when it is not
    n_trips     integer,                     -- gtfs_stop only: evidence of service
    regions     text[] NOT NULL,
    geom        geometry(Point, 4326) NOT NULL,  -- ST_PointOnSurface, for drawing
    run_id      text NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS place_geom_idx ON source_map.place USING gist (geom);
CREATE INDEX IF NOT EXISTS place_vertex_idx ON source_map.place (vertex_id);
CREATE INDEX IF NOT EXISTS place_kind_idx ON source_map.place (kind);
CREATE INDEX IF NOT EXISTS place_start_idx ON source_map.place (is_start) WHERE is_start;

-- Planar indexes, so the KNN search is index-backed in metres.
--
-- 0004 added ::geography indexes because ST_DWithin in metres could not use a
-- plain geometry index. Those serve a RANGE predicate well and a nearest
-- neighbour over POLYGONS badly: the same 7,471 car parks take 2.6 s through
-- ST_Transform(geom, 32632) and did not finish in four minutes through a
-- geography range join. Both indexes are worth their space -- they answer
-- different questions.
CREATE INDEX IF NOT EXISTS vertex_utm_idx
    ON source_map.vertex USING gist (ST_Transform(geom, 32632));
CREATE INDEX IF NOT EXISTS poi_utm_idx
    ON staging.osm_poi USING gist (ST_Transform(geom, 32632));
CREATE INDEX IF NOT EXISTS settlement_utm_idx
    ON staging.settlement USING gist (ST_Transform(geom, 32632));
CREATE INDEX IF NOT EXISTS gtfs_stop_utm_idx
    ON staging.gtfs_stop USING gist (ST_Transform(geom, 32632));

-- The temporary indexes used while measuring this, under the names they were
-- created with. Dropped so the store holds only what a migration put there.
DROP INDEX IF EXISTS staging.tmp_poi_utm;
DROP INDEX IF EXISTS source_map.tmp_edge_utm;
DROP INDEX IF EXISTS source_map.tmp_vertex_utm;

-- Every place, for QGIS. Point layer; style by `kind`, filter on `is_start`.
DROP VIEW IF EXISTS qa.v_place;
CREATE VIEW qa.v_place AS
SELECT p.source, p.source_id, p.kind, p.name, p.ele_m,
       p.vertex_id, p.distance_m, p.is_start, p.start_note, p.n_trips,
       p.regions, v.component_id, p.geom
FROM source_map.place p
JOIN source_map.vertex v ON v.vertex_id = p.vertex_id;

-- THE LAYER TO OPEN FIRST when judging a snap: a line from each place to the
-- vertex it attached to. A wrong snap is invisible as a number and obvious as a
-- line reaching across a valley or through a wall. Sort by distance_m
-- descending and look at the top of the list.
DROP VIEW IF EXISTS qa.v_place_link;
CREATE VIEW qa.v_place_link AS
SELECT p.source, p.source_id, p.kind, p.name, p.distance_m, p.is_start,
       ST_MakeLine(p.geom, v.geom) AS geom
FROM source_map.place p
JOIN source_map.vertex v ON v.vertex_id = p.vertex_id
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
FROM source_map.place p
JOIN source_map.vertex v ON v.vertex_id = p.vertex_id
LEFT JOIN source_map.vertex_degree vd ON vd.vertex_id = p.vertex_id
WHERE p.is_start
GROUP BY p.vertex_id, v.component_id, vd.degree, v.geom;

-- ==== from v1/0011_review_categories.sql ====

-- Category columns on the review layers, so QGIS can style without expressions.
--
-- The layers so far carry raw measures: gradient as a float, matched_fraction
-- as a float, distance_m as a float. Every one of those needs a QGIS expression
-- and a hand-built class ramp before it shows anything, and a class ramp built
-- by hand in a dialog is a decision that lives in one .qgz file on one machine
-- rather than in the database that is the product.
--
-- So every measure that gets styled has a `*_class` twin beside it. Read the
-- number when the number matters; drag the class into Categorized and get a
-- legend that already means something.
--
-- THE NUMERIC PREFIX IS DELIBERATE. QGIS sorts categories by value, so
-- "gentle / moderate / steep / very steep / flat" comes out with flat between
-- gentle and moderate and the ramp reads backwards in the legend. A leading
-- digit fixes the order for every renderer, and it survives export to
-- GeoPackage, which an ordering defined in a style file does not.
--
-- EVERY VIEW HERE IS DROPPED AND CREATED, NEVER `CREATE OR REPLACE`, and so is
-- every view in 0005-0010 as of this migration. Two reasons, both found the
-- hard way while writing this file:
--
--   * CREATE OR REPLACE can only APPEND a column. Adding a class beside the
--     measure it classifies inserts one in the middle, which it refuses.
--   * migrate.py replays the WHOLE chain every run, and replay is the normal
--     case, not an error. So when a later migration widens a view, the earlier
--     migration that first defined it runs again on the NEXT replay and fails
--     with "cannot drop columns from view". Dropping first makes the chain
--     order-independent; leaving it would have made the store un-migratable
--     from its own history.
--
-- Boundaries come from the measurements already in docs/metadata-rules.md, not
-- from round numbers chosen here: the gradient bands are the ones the network
-- actually falls into (48.6% under 5%, 0.3% over 50%), and the place bands are
-- where the snap distributions separate (car parks p50 6.2 m, peaks p90 320 m).

-- ── Shared classifiers ───────────────────────────────────────────────────────

-- Steepness of a stretch, on the ABSOLUTE gradient: for review, "how steep is
-- this" is the question, and up or down is the `net_m` column beside it.
CREATE OR REPLACE FUNCTION qa.steepness_class(gradient double precision)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN gradient IS NULL          THEN '9 unknown'
        WHEN abs(gradient) < 0.05      THEN '1 flat (<5%)'
        WHEN abs(gradient) < 0.15      THEN '2 gentle (5-15%)'
        WHEN abs(gradient) < 0.30      THEN '3 moderate (15-30%)'
        WHEN abs(gradient) < 0.50      THEN '4 steep (30-50%)'
        ELSE                                '5 very steep (>50%)'
    END
$$;

-- SAC hiking scale, grouped to what a walker distinguishes. `9 invalid tag`
-- is not defensive padding: four rows in staging carry junk in sac_scale (two
-- `T3`, one `2`, one containing a sentence about stone ruins), and a
-- classifier that folded them into "no grade" would hide them for good.
CREATE OR REPLACE FUNCTION qa.difficulty_class(sac_scale text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    -- Searched CASE, not `CASE sac_scale WHEN NULL`: that never matches,
    -- because NULL = NULL is unknown, and every ungraded edge would silently
    -- fall through to the invalid bucket.
    SELECT CASE
        WHEN sac_scale IS NULL                        THEN '0 ungraded'
        WHEN sac_scale = 'hiking'                     THEN '1 hiking (T1)'
        WHEN sac_scale = 'mountain_hiking'            THEN '2 mountain (T2)'
        WHEN sac_scale = 'demanding_mountain_hiking'  THEN '3 demanding mountain (T3)'
        WHEN sac_scale = 'alpine_hiking'              THEN '4 alpine (T4)'
        WHEN sac_scale = 'demanding_alpine_hiking'    THEN '5 demanding alpine (T5)'
        WHEN sac_scale = 'difficult_alpine_hiking'    THEN '6 difficult alpine (T6)'
        ELSE                                               '9 invalid tag'
    END
$$;

-- Underfoot, grouped from the ~62% of edges that carry a surface tag.
CREATE OR REPLACE FUNCTION qa.surface_class(surface text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN surface IS NULL THEN '0 untagged'
        WHEN surface IN ('asphalt','concrete','paved','paving_stones','sett',
                         'cobblestone','concrete:plates','metal','wood')
                             THEN '1 paved'
        WHEN surface IN ('compacted','fine_gravel','gravel','pebblestone',
                         'unpaved','ground','dirt','earth','grass','sand',
                         'mud','rock','stone','woodchips','grass_paver')
                             THEN '2 unpaved'
        ELSE                      '3 other'
    END
$$;

-- How far a place sits from the network. Bands, not a threshold: nothing is
-- filtered on this, it only makes the tail visible at a glance.
CREATE OR REPLACE FUNCTION qa.distance_band(distance_m double precision)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN distance_m IS NULL THEN '9 unknown'
        WHEN distance_m < 10    THEN '1 on the network (<10 m)'
        WHEN distance_m < 50    THEN '2 close (10-50 m)'
        WHEN distance_m < 100   THEN '3 near (50-100 m)'
        WHEN distance_m < 500   THEN '4 far (100-500 m)'
        ELSE                         '5 remote (>500 m)'
    END
$$;

-- ── The network, with everything on it ───────────────────────────────────────

-- The one layer to open if you only open one. Every styled field has a class
-- beside it, and `carries_route` says whether this edge has a name from a
-- relation even when its own `name` tag is empty (10,361 edges do).
DROP VIEW IF EXISTS qa.v_network;
CREATE VIEW qa.v_network AS
SELECT e.edge_id, e.way_id, e.geom, e.length_m,
       e.tags ->> 'highway'   AS highway,
       e.tags ->> 'surface'   AS surface,
       e.tags ->> 'sac_scale' AS sac_scale,
       e.tags ->> 'name'      AS name,
       e.routable_foot, e.routable_bike, e.regions,
       e.ascent_m, e.descent_m,
       e.ascent_m - e.descent_m AS net_m,
       CASE WHEN e.length_m > 0
            THEN (e.ascent_m - e.descent_m) / e.length_m END AS gradient,
       qa.steepness_class(
           CASE WHEN e.length_m > 0
                THEN (e.ascent_m - e.descent_m) / e.length_m END)  AS steepness_class,
       qa.difficulty_class(e.tags ->> 'sac_scale')                 AS difficulty_class,
       qa.surface_class(e.tags ->> 'surface')                      AS surface_class,
       CASE WHEN EXISTS (SELECT 1 FROM source_map.edge_route er
                         WHERE er.edge_id = e.edge_id)
            THEN '1 carries a named route' ELSE '0 unnamed' END    AS route_class,
       CASE WHEN e.routable_foot AND e.routable_bike THEN '1 foot and bike'
            WHEN e.routable_foot                     THEN '2 foot only'
            WHEN e.routable_bike                     THEN '3 bike only'
            ELSE                                          '4 neither' END AS access_class,
       -- The profile columns live here too, so the review bundle can ship ONE
       -- layer of 101,951 geometries instead of two. qa.v_elevation stays for a
       -- direct QGIS connection, where a focused layer costs nothing.
       CASE WHEN e.profile_m IS NULL THEN '2 not sampled'
            WHEN e.ascent_m IS NULL  THEN '1 outside DEM coverage'
            ELSE                          '0 profiled' END AS profile_class,
       array_length(e.profile_m, 1)              AS profile_points,
       e.profile_m[1]                            AS start_m,
       e.profile_m[array_length(e.profile_m, 1)] AS end_m
FROM source_map.edge e;

-- ── Elevation ────────────────────────────────────────────────────────────────

DROP VIEW IF EXISTS qa.v_elevation;
CREATE VIEW qa.v_elevation AS
SELECT e.edge_id, e.way_id, e.length_m, e.ascent_m, e.descent_m,
       e.ascent_m - e.descent_m                     AS net_m,
       CASE WHEN e.length_m > 0
            THEN (e.ascent_m - e.descent_m) / e.length_m END AS gradient,
       qa.steepness_class(
           CASE WHEN e.length_m > 0
                THEN (e.ascent_m - e.descent_m) / e.length_m END) AS steepness_class,
       CASE WHEN e.profile_m IS NULL           THEN '2 not sampled'
            WHEN e.ascent_m IS NULL            THEN '1 outside DEM coverage'
            ELSE                                    '0 profiled' END AS profile_class,
       array_length(e.profile_m, 1)                 AS profile_points,
       e.profile_m[1]                               AS start_m,
       e.profile_m[array_length(e.profile_m, 1)]    AS end_m,
       e.tags ->> 'highway'   AS highway,
       e.tags ->> 'sac_scale' AS sac_scale,
       e.tags ->> 'name'      AS name,
       e.geom
FROM source_map.edge e;

-- ── Routes ───────────────────────────────────────────────────────────────────

DROP VIEW IF EXISTS qa.v_route;
CREATE VIEW qa.v_route AS
SELECT r.rel_id,
       r.tags ->> 'ref'         AS ref,
       r.tags ->> 'name'        AS name,
       r.tags ->> 'route'       AS route_kind,
       r.tags ->> 'network'     AS network,
       r.tags ->> 'osmc:symbol' AS osmc_symbol,
       r.regions,
       count(*)                                    AS edges,
       round((sum(e.length_m) / 1000)::numeric, 2) AS km,
       ST_NumGeometries(ST_Multi(ST_LineMerge(ST_Collect(e.geom)))) AS pieces,
       -- A route in one piece runs continuously through this network. In nine,
       -- it is either clipped at the bbox edge or has real gaps -- and that is
       -- a defect the topology rules cannot see, because they look at loose
       -- ends, not at whether a route runs through.
       CASE WHEN ST_NumGeometries(ST_Multi(ST_LineMerge(ST_Collect(e.geom)))) = 1
                 THEN '0 continuous'
            WHEN ST_NumGeometries(ST_Multi(ST_LineMerge(ST_Collect(e.geom)))) <= 3
                 THEN '1 broken (2-3 pieces)'
            ELSE      '2 scattered (4+ pieces)' END AS continuity_class,
       CASE WHEN sum(e.ascent_m) IS NULL   THEN '9 unknown'
            WHEN sum(e.ascent_m) < 200     THEN '1 flat (<200 m)'
            WHEN sum(e.ascent_m) < 600     THEN '2 rolling (200-600 m)'
            WHEN sum(e.ascent_m) < 1200    THEN '3 hilly (600-1200 m)'
            ELSE                                '4 mountain (>1200 m)' END AS climb_class,
       CASE WHEN sum(e.length_m) < 5000    THEN '1 short (<5 km)'
            WHEN sum(e.length_m) < 15000   THEN '2 half day (5-15 km)'
            WHEN sum(e.length_m) < 30000   THEN '3 long (15-30 km)'
            ELSE                                '4 multi-day (>30 km)' END AS length_class,
       CASE r.tags ->> 'network'
            WHEN 'iwn' THEN '4 international' WHEN 'icn' THEN '4 international'
            WHEN 'nwn' THEN '3 national'      WHEN 'ncn' THEN '3 national'
            WHEN 'rwn' THEN '2 regional'      WHEN 'rcn' THEN '2 regional'
            WHEN 'lwn' THEN '1 local'         WHEN 'lcn' THEN '1 local'
            ELSE            '0 unclassified' END AS scope_class,
       ST_Multi(ST_LineMerge(ST_Collect(e.geom))) AS geom
FROM staging.osm_relation r
JOIN (SELECT DISTINCT rel_id, edge_id FROM source_map.edge_route) er ON er.rel_id = r.rel_id
JOIN source_map.edge e ON e.edge_id = er.edge_id
GROUP BY r.rel_id, r.tags, r.regions;

DROP VIEW IF EXISTS qa.v_route_coverage;
CREATE VIEW qa.v_route_coverage AS
WITH members AS (
    SELECT r.rel_id,
           count(*) FILTER (WHERE m ->> 'type' = 'w') AS way_members,
           count(DISTINCT (m ->> 'ref')) FILTER (WHERE m ->> 'type' = 'w')
                                                      AS distinct_way_members,
           count(*) FILTER (WHERE m ->> 'type' = 'n') AS node_members,
           count(*) FILTER (WHERE m ->> 'type' = 'r') AS relation_members
    FROM staging.osm_relation r, LATERAL jsonb_array_elements(r.members) m
    GROUP BY r.rel_id
), matched AS (
    SELECT r.rel_id, r.tags, m.way_members, m.distinct_way_members,
           m.node_members, m.relation_members,
           count(DISTINCT e.way_id)   AS matched_ways,
           count(DISTINCT er.edge_id) AS matched_edges,
           CASE WHEN m.distinct_way_members = 0 THEN NULL
                ELSE round(count(DISTINCT e.way_id)::numeric
                           / m.distinct_way_members, 3) END AS matched_fraction
    FROM staging.osm_relation r
    JOIN members m ON m.rel_id = r.rel_id
    LEFT JOIN source_map.edge_route er ON er.rel_id = r.rel_id
    LEFT JOIN source_map.edge e ON e.edge_id = er.edge_id
    GROUP BY r.rel_id, r.tags, m.way_members, m.distinct_way_members,
             m.node_members, m.relation_members
)
SELECT rel_id,
       tags ->> 'ref'  AS ref,
       tags ->> 'name' AS name,
       way_members, distinct_way_members, node_members, relation_members,
       matched_ways, matched_edges, matched_fraction,
       -- A fragment is a route this network cannot hold: BI-12 Trieste-Savona
       -- matches 2 of its 646 ways. Route generation must filter on this.
       CASE WHEN matched_fraction IS NULL  THEN '9 no way members'
            WHEN matched_fraction >= 0.9   THEN '0 complete (>=90%)'
            WHEN matched_fraction >= 0.6   THEN '1 most (60-90%)'
            WHEN matched_fraction >= 0.2   THEN '2 partial (20-60%)'
            ELSE                                '3 fragment (<20%)' END AS coverage_class
FROM matched;

DROP VIEW IF EXISTS qa.v_route_elevation;
CREATE VIEW qa.v_route_elevation AS
SELECT r.rel_id,
       r.tags ->> 'ref'   AS ref,
       r.tags ->> 'name'  AS name,
       r.tags ->> 'route' AS route_kind,
       count(*)                                    AS edges,
       round((sum(e.length_m) / 1000)::numeric, 2) AS km,
       round(sum(e.ascent_m)::numeric, 0)          AS ascent_m,
       round(sum(e.descent_m)::numeric, 0)         AS descent_m,
       round(min(pz.lo)::numeric, 0)               AS lowest_m,
       round(max(pz.hi)::numeric, 0)               AS highest_m,
       count(*) FILTER (WHERE e.ascent_m IS NULL)  AS edges_without_profile,
       CASE WHEN sum(e.ascent_m) IS NULL  THEN '9 unknown'
            WHEN sum(e.ascent_m) < 200    THEN '1 flat (<200 m)'
            WHEN sum(e.ascent_m) < 600    THEN '2 rolling (200-600 m)'
            WHEN sum(e.ascent_m) < 1200   THEN '3 hilly (600-1200 m)'
            ELSE                               '4 mountain (>1200 m)' END AS climb_class,
       ST_Multi(ST_LineMerge(ST_Collect(e.geom)))  AS geom
FROM staging.osm_relation r
JOIN (SELECT DISTINCT rel_id, edge_id FROM source_map.edge_route) er ON er.rel_id = r.rel_id
JOIN source_map.edge e ON e.edge_id = er.edge_id
LEFT JOIN LATERAL (
    SELECT min(z) AS lo, max(z) AS hi FROM unnest(e.profile_m) AS z
) pz ON true
GROUP BY r.rel_id, r.tags;

-- ── Places and starts ────────────────────────────────────────────────────────

DROP VIEW IF EXISTS qa.v_place;
CREATE VIEW qa.v_place AS
SELECT p.source, p.source_id, p.kind, p.name, p.ele_m,
       p.vertex_id, p.distance_m, p.is_start, p.start_note, p.n_trips,
       p.regions, v.component_id, p.geom,
       qa.distance_band(p.distance_m) AS distance_band,
       CASE WHEN p.is_start THEN '0 can start a walk'
            ELSE                 '1 destination or passed' END AS role_class,
       -- A place snapped to an island is attached to a network that goes
       -- nowhere. Invisible in the distance, obvious here.
       CASE WHEN v.component_id = (SELECT component_id FROM source_map.vertex
                                   GROUP BY component_id
                                   ORDER BY count(*) DESC LIMIT 1)
            THEN '0 main network' ELSE '1 island' END AS reachability_class
FROM source_map.place p
JOIN source_map.vertex v ON v.vertex_id = p.vertex_id;

DROP VIEW IF EXISTS qa.v_place_link;
CREATE VIEW qa.v_place_link AS
SELECT p.source, p.source_id, p.kind, p.name, p.distance_m, p.is_start,
       qa.distance_band(p.distance_m) AS distance_band,
       CASE WHEN p.is_start THEN '0 can start a walk'
            ELSE                 '1 destination or passed' END AS role_class,
       ST_MakeLine(p.geom, v.geom) AS geom
FROM source_map.place p
JOIN source_map.vertex v ON v.vertex_id = p.vertex_id
WHERE NOT ST_Equals(p.geom, v.geom);

DROP VIEW IF EXISTS qa.v_start;
CREATE VIEW qa.v_start AS
SELECT p.vertex_id,
       v.component_id,
       vd.degree,
       count(*)                                       AS anchors,
       array_agg(DISTINCT p.kind ORDER BY p.kind)     AS kinds,
       array_remove(array_agg(DISTINCT p.name), NULL) AS names,
       round(min(p.distance_m)::numeric, 1)           AS nearest_m,
       sum(p.n_trips)                                 AS trips,
       bool_or(p.source = 'gtfs_stop' OR p.kind = 'station') AS car_free,
       CASE WHEN bool_or(p.source = 'gtfs_stop' OR p.kind = 'station')
            THEN '0 car-free (station or stop)' ELSE '1 needs a car' END AS access_class,
       -- Begin here and get nowhere: 86 of these.
       CASE WHEN v.component_id = (SELECT component_id FROM source_map.vertex
                                   GROUP BY component_id
                                   ORDER BY count(*) DESC LIMIT 1)
            THEN '0 main network' ELSE '1 island' END AS reachability_class,
       CASE WHEN array_length(array_remove(array_agg(DISTINCT p.name), NULL), 1) IS NULL
            THEN '1 unnamed' ELSE '0 named' END AS naming_class,
       v.geom
FROM source_map.place p
JOIN source_map.vertex v ON v.vertex_id = p.vertex_id
LEFT JOIN source_map.vertex_degree vd ON vd.vertex_id = p.vertex_id
WHERE p.is_start
GROUP BY p.vertex_id, v.component_id, vd.degree, v.geom;

-- ==== from v1/0012_place_utm_index.sql ====

-- The planar index source_map.place was missing.
--
-- 0010 put a GiST index on ST_Transform(geom, 32632) over staging.osm_poi,
-- staging.settlement, staging.gtfs_stop and source_map.vertex -- everything the
-- SNAP reads -- and not over source_map.place, which nothing read yet.
--
-- export/route_documents.py reads it: for each route it asks which places lie
-- within 100 m of the merged line, and without this index that is a sequential
-- scan of 12,476 places with a reprojection each, once per route. Measured at
-- roughly 1.4 s per route, or about 18 minutes for 752. The lesson is the one
-- 0004 already recorded: a predicate that reads naturally and quietly cannot
-- use an index.
CREATE INDEX IF NOT EXISTS place_utm_idx
    ON source_map.place USING gist (ST_Transform(geom, 32632));

-- ==== from v1/0013_draw.sql ====

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
-- ways we hold, and routing over source_map.edge directly means the route IS an
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

CREATE TABLE IF NOT EXISTS catalogue.route (
    route_id        text PRIMARY KEY,     -- geometry-derived, stable across rebuilds
    kind            text NOT NULL DEFAULT 'generated',
    name            text,                 -- absent until trailhead naming is solved
    start_vertex    bigint NOT NULL REFERENCES source_map.vertex (vertex_id)
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
CREATE INDEX IF NOT EXISTS route_geom_idx ON catalogue.route USING gist (geom);
CREATE INDEX IF NOT EXISTS route_start_idx ON catalogue.route (start_vertex);

-- The walked sequence: which edge, in what order, in which direction. This is
-- what "along the routed edge sequence" means — the MTB conjunction, the
-- ascent and the profile all read THIS, not a spatial match.
-- `forward` records whether the edge is walked source→target as stored;
-- walked backwards, ascent and descent SWAP (metadata-rules.md, directional
-- attributes) and the profile reverses.
CREATE TABLE IF NOT EXISTS catalogue.route_edge (
    route_id  text NOT NULL REFERENCES catalogue.route (route_id) ON DELETE CASCADE,
    seq       integer NOT NULL,
    edge_id   bigint NOT NULL REFERENCES source_map.edge (edge_id) ON DELETE CASCADE,
    forward   boolean NOT NULL,
    PRIMARY KEY (route_id, seq)
);
CREATE INDEX IF NOT EXISTS route_edge_edge_idx ON catalogue.route_edge (edge_id);

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
FROM catalogue.route r;

-- ==== from v1/0014_draw_activity.sql ====

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

ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS activity text NOT NULL DEFAULT 'foot';
ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS sac_max text;
ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS graded_share double precision;
ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS bike_blocked_m double precision;
CREATE INDEX IF NOT EXISTS route_activity_idx ON catalogue.route (activity);

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
FROM catalogue.route r;

-- ==== from v1/0015_draw_destinations.sql ====

-- Destination routes: out to somewhere worth going, and back.
--
-- Owner (2026-08-20): routes are not just loops — some go from a start to an
-- INTERESTING POI (a view, a peak...) and come back. The anchors module
-- already knew the half of it ("a summit is a destination, not a trailhead");
-- this is the other half: the destination becomes the point of the route.
--
-- Two consequences the loop generator never had:
--
--   * Generated routes get NAMES. Trailhead naming is unsolved, but
--     destination naming is free — 219 of the 240 reachable peaks carry one.
--     "To Rifugio Elisa" is an answer; "generated-9f2c1ab4" is not.
--   * The catalogue is replaced per (activity, shape). Loops and destination
--     routes are siblings the same way foot and mtb are: regenerating one
--     family must not delete the other.

ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS shape text NOT NULL DEFAULT 'loop';
ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS destination_id text;
ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS destination_kind text;
ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS destination_name text;
CREATE INDEX IF NOT EXISTS route_shape_idx ON catalogue.route (activity, shape);

DROP VIEW IF EXISTS qa.v_draw;
CREATE VIEW qa.v_draw AS
SELECT r.route_id,
       r.activity,
       r.shape,
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
       r.score,
       r.seed,
       qa.difficulty_class(r.sac_max)           AS difficulty_class,
       CASE WHEN r.shape = 'destination' THEN '0 to a destination'
            ELSE                              '1 loop' END AS route_shape_class,
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
FROM catalogue.route r;

-- ==== from v1/0016_urban_exit.sql ====

-- Urban exits: where the network leaves a residential area, and whether that
-- is a place a walk begins.
--
-- The 999 residential polygons are still not starts -- "a residential area is
-- a polygon, not a point a walk begins at" is right about the polygon. Its
-- EXITS are points, they are few, and they are what "start from the village"
-- means on the ground. curate/places.py writes them into source_map.place with
-- source = 'urban_exit' and kind = the way that leads out, so qa.v_start picks
-- them up with every other start and nothing here duplicates that.
--
-- This view is the review surface for the new rule: colour by exit_class and
-- the question "is this really where a walk starts" is one look, not an
-- expression. Same shape as every other qa.v_*, categories numbered so the
-- QGIS legend sorts.

DROP VIEW IF EXISTS qa.v_urban_exit;
CREATE VIEW qa.v_urban_exit AS
SELECT p.vertex_id,
       p.name,
       p.kind                            AS way_out,
       p.is_start,
       p.start_note,
       v.component_id,
       vd.degree,
       p.regions,
       CASE WHEN p.is_start THEN '0 onto a trail'
            ELSE                 '1 onto a lane (town continuing)' END AS exit_class,
       -- Begin here and get nowhere. The same island test qa.v_start applies,
       -- repeated because an exit is judged before anything is generated from
       -- it, and an exit off the main network is worth seeing at that point.
       CASE WHEN v.component_id = (SELECT component_id FROM source_map.vertex
                                   GROUP BY component_id
                                   ORDER BY count(*) DESC LIMIT 1)
            THEN '0 main network' ELSE '1 island' END AS reachability_class,
       -- 21 of 999 residential areas carry a name, so most exits are unnamed.
       -- Recorded rather than invented: naming one from a nearby feature is
       -- the same unsolved problem qa.v_start.names has for car parks.
       CASE WHEN p.name IS NULL THEN '1 unnamed' ELSE '0 named' END AS naming_class,
       -- How many ways meet here: a degree-2 exit is a way passing through the
       -- boundary, a higher one is a junction where a walker has a choice.
       CASE WHEN vd.degree IS NULL THEN '9 unknown'
            WHEN vd.degree <= 2    THEN '0 through (2 or fewer)'
            WHEN vd.degree = 3     THEN '1 fork (3)'
            ELSE                        '2 junction (4+)' END AS degree_class,
       v.geom
FROM source_map.place p
JOIN source_map.vertex v ON v.vertex_id = p.vertex_id
LEFT JOIN source_map.vertex_degree vd ON vd.vertex_id = p.vertex_id
WHERE p.source = 'urban_exit';

-- ==== staging.dem (created by load/dem.py in v1, outside the chain) ====
-- A fresh v1 database could not run curate/elevation.py until the loader had
-- run once; the table belongs to the schema, so it lives here now. The
-- loader keeps its own IF NOT EXISTS as a belt.

CREATE TABLE IF NOT EXISTS staging.dem (
    rid    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source text NOT NULL,
    rast   raster NOT NULL,
    run_id text NOT NULL
);

CREATE INDEX IF NOT EXISTS dem_rast_idx
    ON staging.dem USING gist (ST_ConvexHull(rast));

-- ==== the chain-version stamp ====
-- migrate.py refuses to replay a chain against a store from the other one:
-- v1 files against this layout would resurrect empty curated.* tables, and
-- this file against a v1 store would build a second, empty copy of the
-- schema beside the full one.

CREATE TABLE IF NOT EXISTS provenance.chain_version (
    version int PRIMARY KEY,
    converted_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO provenance.chain_version (version) VALUES (2)
ON CONFLICT (version) DO NOTHING;
