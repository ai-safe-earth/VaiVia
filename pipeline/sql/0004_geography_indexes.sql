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
    ON curated.vertex USING gist ((geom::geography));

CREATE INDEX IF NOT EXISTS edge_geog_idx
    ON curated.edge USING gist ((geom::geography));

CREATE INDEX IF NOT EXISTS poi_geog_idx
    ON staging.osm_poi USING gist ((geom::geography));

CREATE INDEX IF NOT EXISTS settlement_geog_idx
    ON staging.settlement USING gist ((geom::geography));

CREATE INDEX IF NOT EXISTS gtfs_stop_geog_idx
    ON staging.gtfs_stop USING gist ((geom::geography));

-- Vertex degree is asked by every QA detector and by the start/end rule.
-- Materialised because computing it per query is a join over 100k edges each
-- time; refreshed by topology/build_network.py after a rebuild.
CREATE MATERIALIZED VIEW IF NOT EXISTS curated.vertex_degree AS
SELECT v.vertex_id,
       v.geom,
       v.component_id,
       count(e.edge_id) AS degree
FROM curated.vertex v
LEFT JOIN curated.edge e ON e.source = v.vertex_id OR e.target = v.vertex_id
GROUP BY v.vertex_id, v.geom, v.component_id;

CREATE UNIQUE INDEX IF NOT EXISTS vertex_degree_id_idx
    ON curated.vertex_degree (vertex_id);
CREATE INDEX IF NOT EXISTS vertex_degree_geog_idx
    ON curated.vertex_degree USING gist ((geom::geography));
CREATE INDEX IF NOT EXISTS vertex_degree_degree_idx
    ON curated.vertex_degree (degree);
