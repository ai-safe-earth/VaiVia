-- The catalogue's enums, enforced where a bug cannot argue with them.
--
-- A parameter-order slip once INSERTed urban_share floats into shape for a
-- whole generation run; the per-family replace could not even DELETE the
-- rows afterwards (its WHERE names real shapes), and their ids swallowed
-- the fresh candidates as false folds. The emit drift-guard caught it —
-- downstream, after the damage. A CHECK fails the first row at the moment
-- of the mistake, which is where a constraint belongs.

DO $$
BEGIN
    ALTER TABLE catalogue.route ADD CONSTRAINT route_shape_check
        CHECK (shape IN ('loop', 'destination', 'out_and_back',
                         'circular', 'linear'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE catalogue.route ADD CONSTRAINT route_kind_check
        CHECK (kind IN ('generated', 'osm_route', 'custom'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE catalogue.route ADD CONSTRAINT route_urban_share_check
        CHECK (urban_share IS NULL OR (urban_share >= 0 AND urban_share <= 1));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
