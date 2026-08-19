-- Route-relation membership: which edges carry which named route.
--
-- 752 route relations were loaded into staging on 2026-08-19 and, until now,
-- nothing read them. Their members are OSM way ids and curated.edge.way_id is
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
-- lesson from curated.vertex_degree on 2026-08-19.
CREATE TABLE IF NOT EXISTS curated.edge_route (
    edge_id      bigint NOT NULL REFERENCES curated.edge (edge_id) ON DELETE CASCADE,
    rel_id       bigint NOT NULL,          -- provenance: the OSM relation id
    member_index integer NOT NULL,         -- position in the relation's members
    piece_index  integer NOT NULL,         -- position of the edge along its way
    role         text,                     -- member role, NULL when OSM gave ''
    run_id       text NOT NULL,
    -- A way may appear twice in the same relation (140 cases measured), so
    -- (edge_id, rel_id) is not unique -- the member position completes the key.
    PRIMARY KEY (edge_id, rel_id, member_index)
);
CREATE INDEX IF NOT EXISTS edge_route_rel_idx ON curated.edge_route (rel_id);
CREATE INDEX IF NOT EXISTS edge_route_edge_idx ON curated.edge_route (edge_id);

-- The network, named. This is the QGIS layer the join exists for: every edge
-- that belongs to a route, with the route's identity as real columns so it can
-- be styled and filtered without a jsonb expression. An edge in two relations
-- appears twice, once per route -- that is the point of the table.
CREATE OR REPLACE VIEW qa.v_route_edge AS
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
FROM curated.edge_route er
JOIN curated.edge e ON e.edge_id = er.edge_id
JOIN staging.osm_relation r ON r.rel_id = er.rel_id;

-- One feature per route: 752 lines instead of 102,000, which is the layer to
-- open first. The geometry is merged where the edges connect and stays a
-- MULTILINESTRING where they do not -- a route in several pieces LOOKS like a
-- route in several pieces, which is exactly what wants judging before anything
-- is generated on top of it.
CREATE OR REPLACE VIEW qa.v_route AS
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
JOIN curated.edge_route er ON er.rel_id = r.rel_id
JOIN curated.edge e ON e.edge_id = er.edge_id
GROUP BY r.rel_id, r.tags, r.regions;

-- Which relations the join could not fully place, and why it is expected. A
-- member way is absent from curated.edge when it falls outside both region
-- bboxes or when load/legality.py excluded it (a road no one may walk). The
-- number that matters is matched_ways / way_members: a route at 0.9 is clipped
-- at the edge of coverage, a route at 0.1 is a route this network cannot hold.
CREATE OR REPLACE VIEW qa.v_route_coverage AS
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
LEFT JOIN curated.edge_route er ON er.rel_id = r.rel_id
LEFT JOIN curated.edge e ON e.edge_id = er.edge_id
GROUP BY r.rel_id, r.tags, m.way_members, m.distinct_way_members,
         m.node_members, m.relation_members;
