-- Every start says what KIND of arrival it is.
--
-- The class existed only as two columns nobody could join from downstream:
-- place.source (poi / settlement / gtfs_stop / urban_exit) and place.kind
-- (a poi_type, a settlement kind, the literal 'stop', or an exit's outward
-- highway). "Is this start a station or a car park" was unanswerable at the
-- document boundary, and the route factory's --start-class parameter needs
-- exactly that answer. The verdict is derived in curate/anchors.py
-- (start_class, pure, tested) and written by curate/places.py with every
-- other verdict -- this file only gives it a home and a review surface.
--
-- 'station' covers rail whichever source proved it (a station POI or a rail
-- GTFS stop); a bus-basin feed lands as bus_stop by its feed label the day
-- one is loaded (pipeline/docs/data-sources.md caveat governs).

ALTER TABLE source_map.place ADD COLUMN IF NOT EXISTS start_class text;

DO $$
BEGIN
    ALTER TABLE source_map.place ADD CONSTRAINT place_start_class_check
        CHECK (start_class IN ('parking', 'station', 'bus_stop', 'urban_exit',
                               'settlement', 'campsite', 'other'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- qa.v_start, widened: which classes of arrival this vertex offers, and one
-- numbered category for the legend -- the BEST arrival, where best follows
-- the digit order (a vertex with a station and a car park reads station).
DROP VIEW IF EXISTS qa.v_start;
CREATE VIEW qa.v_start AS
SELECT p.vertex_id,
       v.component_id,
       vd.degree,
       count(*)                                       AS anchors,
       array_agg(DISTINCT p.kind ORDER BY p.kind)     AS kinds,
       array_remove(array_agg(DISTINCT p.start_class), NULL) AS start_classes,
       array_remove(array_agg(DISTINCT p.name), NULL) AS names,
       round(min(p.distance_m)::numeric, 1)           AS nearest_m,
       sum(p.n_trips)                                 AS trips,
       bool_or(p.source = 'gtfs_stop' OR p.kind = 'station') AS car_free,
       CASE WHEN bool_or(p.source = 'gtfs_stop' OR p.kind = 'station')
            THEN '0 car-free (station or stop)' ELSE '1 needs a car' END AS access_class,
       min(CASE p.start_class
           WHEN 'station'    THEN '0 station'
           WHEN 'bus_stop'   THEN '1 bus stop'
           WHEN 'parking'    THEN '2 parking'
           WHEN 'settlement' THEN '3 settlement'
           WHEN 'urban_exit' THEN '4 urban exit'
           WHEN 'campsite'   THEN '5 campsite'
           ELSE                   '6 other' END)      AS arrival_class,
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
