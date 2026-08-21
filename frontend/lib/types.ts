/** Shapes the gateway proxies through from the backend. Metres and minutes. */

export interface PoiRef {
  name: string | null;
  type: string;
}

export interface Trail {
  id: string;
  name: string;
  activity: string;
  difficulty: string;
  difficulty_level: number;
  difficulty_notes: string | null;
  landscape_description: string | null;
  total_distance_m: number;
  elevation_gain_m: number | null;
  elevation_loss_m: number | null;
  duration_hike_min: number | null;
  duration_mtb_min: number | null;
  best_seasons: string[];
  seasonal_hazards: string[];
  pois: PoiRef[];
}

/** One circular route from the precomputed catalogue. Note the field names
 *  differ from Trail: a Route is generated, not curated. */
export interface Loop {
  id: string;
  activity: string;
  /** 'osm_route' (a mapped relation) or 'generated'. */
  kind: string;
  /** 'loop' | 'destination' (constructed by the generator) or 'circular' |
   *  'linear' (measured on a mapped route, schema 1.2). 'named' survives only
   *  in pre-1.2 transcripts. */
  shape: string;
  /** A destination route is named after where it goes ("To Rifugio Elisa");
   *  an OSM relation after itself; null when nothing earned a name — the card
   *  shows its distance instead of inventing one. */
  name: string | null;
  ref: string | null;
  destination_name: string | null;
  distance_m: number;
  ascent_m: number | null;
  /** The expanded card's figures — already on the catalogue node, so they
   *  travel with every row rather than needing a second fetch. */
  descent_m: number | null;
  lowest_m: number | null;
  highest_m: number | null;
  surface_dominant: string | null;
  pieces: number | null;
  continuous: boolean | null;
  graded_share: number | null;
  /** Two grades, both true (metadata-rules.md): sac_scale is the CHARACTER
   *  (hardest grade covering ≥5% — the label it wears), sac_max the EXIGENT
   *  grade (hardest metre walked — what you must be able to handle). */
  sac_scale: string | null;
  sac_max: string | null;
  /** The access conjunction along the walked sequence: one forbidding segment
   *  forbids. null = unknown, which is not yes. */
  mtb_rideable: boolean | null;
  mtb_scale: string | null;
  /** Generation-time measure; OSM relations carry null, not 0. */
  off_road_share: number | null;
  score: number | null;
  start_vertex_id: number | null;
  start_names: string[] | null;
  car_free: boolean | null;
  start_lat: number | null;
  start_lon: number | null;
  pois: PoiRef[];
}

/** The altitude profile as the route document carries it: two parallel
 *  arrays, cumulative metres and heights. */
export interface RouteProfile {
  distance_m: number[];
  elevation_m: number[];
}

/** The expandable card's payload from GET /routes/{id}/detail — what the
 *  route document knows beyond the map shape. */
export interface RouteDetail {
  route_id: string;
  shape: string | null;
  profile: RouteProfile | null;
  /** 'ok' = a true along-route measure; 'approximate' = stitched across the
   *  gaps of a multi-piece route (drawn with a caveat, never as clean truth);
   *  null = no profile at all. */
  profile_quality: 'ok' | 'approximate' | null;
  measures: {
    distance_m: number;
    ascent_m: number | null;
    descent_m: number | null;
    lowest_m: number | null;
    highest_m: number | null;
  };
  continuity: { pieces: number; continuous: boolean };
  surface: { distribution: Record<string, number>; dominant: string | null };
  places: {
    id: string;
    kind: string;
    name: string | null;
    offset_m: number;
    distance_along_m: number | null;
  }[];
  attribution: string;
}

export interface RouteResult {
  total_distance_m: number;
  elevation_gain_m: number | null;
  start: string;
  end: string;
}

/** One resolved (or failed) route from a composed plan. */
export interface RouteBlock {
  route: RouteResult | null;
  geometry?: GeoJSON.LineString;
  unknown_place?: string;
  off_network?: string;
  no_path?: boolean;
}

export interface ChatResults {
  kind: 'trail_search' | 'loop_search' | 'route' | 'clarify';
  /** Where the card list folds: the prose narrates the first N results, the
   *  rest sit behind "show more". Absent on older stored turns. */
  answered_count?: number;
  /** What the composed plan actually did, in the walker's own words — the
   *  backend's account of the EXECUTED plan, never re-derived here. Absent
   *  when nothing was searched (a clarify turn). */
  reading?: { key: string; value: string }[];
  trails?: Trail[];
  /** Circular routes selected from the catalogue. Render on presence, not
   *  on `kind`: a loops+theme turn is still labelled trail_search. */
  loops?: Loop[];
  /** Every route in the plan; `route`/`geometry` mirror the first resolved one. */
  routes?: RouteBlock[];
  route?: RouteResult | null;
  geometry?: GeoJSON.LineString;
  unknown_place?: string;
  off_network?: string;
  no_path?: boolean;
  clarification?: string;
  suggestions?: string[];
  semantic_unavailable?: boolean;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  results?: ChatResults;
  streaming?: boolean;
  error?: string;
}
