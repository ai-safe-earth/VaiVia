-- How much of an edge runs through urban fabric, in metres.
--
-- The route factory needs a measurable urban-exposure rule ("a route wholly
-- inside town is not an outing") and off_road_share cannot carry it: that
-- is a highway-class heuristic, and a footway through a park and a footway
-- between apartment blocks read identically there. This is geometric --
-- length of the edge inside the union of residential polygons, computed in
-- UTM32 (one statement over the whole table: PostGIS's job) by
-- curate/urban.py, which re-runs after any network rebuild like every other
-- derived layer. NULL means "not computed for this build", never 0 --
-- absent is not zero, in column form.

ALTER TABLE source_map.edge ADD COLUMN IF NOT EXISTS urban_m double precision;
