-- The noded routing network: curated.vertex / curated.edge.
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

CREATE TABLE IF NOT EXISTS curated.vertex (
    vertex_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    geom         geometry(Point, 4326) NOT NULL,
    component_id bigint,               -- pgr_connectedComponents, written after build
    run_id       text NOT NULL
);
CREATE INDEX IF NOT EXISTS vertex_geom_idx ON curated.vertex USING gist (geom);
-- Exact-equality dedup key: coordinates come from the same OSM node, so they
-- are bit-identical, and the binary form is what uniqueness means here.
CREATE UNIQUE INDEX IF NOT EXISTS vertex_geom_key ON curated.vertex (geom);

CREATE TABLE IF NOT EXISTS curated.edge (
    edge_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    way_id        bigint NOT NULL,     -- provenance: the parent OSM way
    piece_index   integer NOT NULL,    -- position of this piece along the way
    source        bigint NOT NULL REFERENCES curated.vertex (vertex_id),
    target        bigint NOT NULL REFERENCES curated.vertex (vertex_id),
    geom          geometry(LineString, 4326) NOT NULL,
    length_m      double precision NOT NULL,   -- recomputed per piece (32632)
    tags          jsonb NOT NULL,              -- inherited whole from the way
    routable_foot boolean NOT NULL,
    routable_bike boolean NOT NULL,
    regions       text[] NOT NULL,
    run_id        text NOT NULL,
    UNIQUE (way_id, piece_index)
);
CREATE INDEX IF NOT EXISTS edge_geom_idx ON curated.edge USING gist (geom);
CREATE INDEX IF NOT EXISTS edge_source_idx ON curated.edge (source);
CREATE INDEX IF NOT EXISTS edge_target_idx ON curated.edge (target);
CREATE INDEX IF NOT EXISTS edge_highway_idx ON curated.edge ((tags ->> 'highway'));
