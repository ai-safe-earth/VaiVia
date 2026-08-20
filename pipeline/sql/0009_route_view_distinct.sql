-- qa.v_route double-counted an edge that a relation lists twice.
--
-- 0007 defined curated.edge_route with one row per (edge, relation, member
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
JOIN (SELECT DISTINCT rel_id, edge_id FROM curated.edge_route) er ON er.rel_id = r.rel_id
JOIN curated.edge e ON e.edge_id = er.edge_id
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
LEFT JOIN curated.edge_route er ON er.rel_id = r.rel_id
LEFT JOIN curated.edge e ON e.edge_id = er.edge_id
GROUP BY r.rel_id, r.tags, m.way_members, m.distinct_way_members,
         m.node_members, m.relation_members;
