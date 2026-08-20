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
  trailforks_url?: string | null;
  pois: PoiRef[];
}

/** One circular route from the precomputed catalogue. Note the field names
 *  differ from Trail: a Route is generated, not curated. */
export interface Loop {
  id: string;
  activity: string;
  /** 'osm_route' (a mapped relation) or 'generated'. */
  kind: string;
  /** 'named' | 'loop' | 'destination'. */
  shape: string;
  /** A destination route is named after where it goes ("To Rifugio Elisa");
   *  an OSM relation after itself; null when nothing earned a name — the card
   *  shows its distance instead of inventing one. */
  name: string | null;
  ref: string | null;
  destination_name: string | null;
  distance_m: number;
  ascent_m: number | null;
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
