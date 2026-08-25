-- Which direction of travel a catalogue row describes (start/end contract
-- §5): fwd/rev for the shapes where direction is the walker's choice, NULL
-- for an out-and-back or destination route whose one outing contains both.
-- The :rev siblings land as pure additions later; the column exists from
-- the cutover so the id and the row can never disagree about direction.

ALTER TABLE catalogue.route ADD COLUMN IF NOT EXISTS direction text;

DO $$
BEGIN
    ALTER TABLE catalogue.route ADD CONSTRAINT route_direction_check
        CHECK (direction IN ('fwd', 'rev'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
