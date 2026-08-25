-- The planar index curated.place was missing.
--
-- 0010 put a GiST index on ST_Transform(geom, 32632) over staging.osm_poi,
-- staging.settlement, staging.gtfs_stop and curated.vertex -- everything the
-- SNAP reads -- and not over curated.place, which nothing read yet.
--
-- export/route_documents.py reads it: for each route it asks which places lie
-- within 100 m of the merged line, and without this index that is a sequential
-- scan of 12,476 places with a reprojection each, once per route. Measured at
-- roughly 1.4 s per route, or about 18 minutes for 752. The lesson is the one
-- 0004 already recorded: a predicate that reads naturally and quietly cannot
-- use an index.
CREATE INDEX IF NOT EXISTS place_utm_idx
    ON curated.place USING gist (ST_Transform(geom, 32632));
