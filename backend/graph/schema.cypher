// get-out-door — graph schema (owner-validated 2026-08-15, see docs/plan.md)
//
// Conventions:
//   * All distances/elevations are METRES (properties suffixed _m); durations are MINUTES.
//   * Trail identity (name, difficulty, activity) lives ONLY on (:Trail).
//   * Routing graph: (:Intersection)-[:CONNECTS_TO]->(:Intersection), both
//     directions materialized unless OSM oneway; each direction carries its own
//     elevation_gain_m / elevation_loss_m.
//   * (:Trail)-[:COMPOSED_OF {seq, match_confidence}]->(:Segment) is the single
//     ordered OSM<->Trailforks link (there is no MAPS_TO).
//   * Segment.osm_way_id is the deterministic split-piece id "<wayId>#<n>"
//     (OSM ways are split at intersections); the raw way id is osm_parent_way_id.
//   * difficulty: 'Easy'|'Intermediate'|'Difficult'|'Pro' <-> difficulty_level 1..4.
//   * Seasonality: best_seasons / seasonal_hazards are LIST<STRING> filterable
//     vocabularies; prose stays in difficulty_notes / landscape_description.
//   * Embedding input (Phase 3) = description + landscape_description + difficulty_notes.

// ── Uniqueness constraints (idempotent re-ingestion anchors) ─────────────────
CREATE CONSTRAINT trail_id        IF NOT EXISTS FOR (t:Trail)        REQUIRE t.id IS UNIQUE;
CREATE CONSTRAINT segment_way_id  IF NOT EXISTS FOR (s:Segment)      REQUIRE s.osm_way_id IS UNIQUE;
CREATE CONSTRAINT inter_node_id   IF NOT EXISTS FOR (i:Intersection) REQUIRE i.osm_node_id IS UNIQUE;
CREATE CONSTRAINT poi_osm_id      IF NOT EXISTS FOR (p:POI)          REQUIRE p.osm_id IS UNIQUE;
CREATE CONSTRAINT region_name     IF NOT EXISTS FOR (r:Region)       REQUIRE r.name IS UNIQUE;

// ── Spatial point indexes ────────────────────────────────────────────────────
CREATE POINT INDEX inter_location   IF NOT EXISTS FOR (i:Intersection) ON (i.location);
CREATE POINT INDEX poi_location     IF NOT EXISTS FOR (p:POI)          ON (p.location);
CREATE POINT INDEX segment_location IF NOT EXISTS FOR (s:Segment)      ON (s.location);

// ── Filter indexes ───────────────────────────────────────────────────────────
CREATE INDEX trail_difficulty_level IF NOT EXISTS FOR (t:Trail) ON (t.difficulty_level);
CREATE INDEX trail_activity         IF NOT EXISTS FOR (t:Trail) ON (t.activity);
CREATE INDEX trail_distance         IF NOT EXISTS FOR (t:Trail) ON (t.total_distance_m);
CREATE INDEX trail_duration_hike    IF NOT EXISTS FOR (t:Trail) ON (t.duration_hike_min);
CREATE INDEX trail_duration_mtb     IF NOT EXISTS FOR (t:Trail) ON (t.duration_mtb_min);
CREATE INDEX trail_elevation_gain   IF NOT EXISTS FOR (t:Trail) ON (t.elevation_gain_m);
CREATE INDEX poi_type               IF NOT EXISTS FOR (p:POI)   ON (p.type);

// ── Vector index (queried from Phase 3; created now so the schema is stable) ─
CREATE VECTOR INDEX trail_embeddings IF NOT EXISTS
FOR (t:Trail) ON (t.description_embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}};
