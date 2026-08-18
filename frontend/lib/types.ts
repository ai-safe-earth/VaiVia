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
  /** The best feature it passes, or null when there is nothing worth naming it
   *  after — the card shows its distance instead of inventing a name. */
  name: string | null;
  distance_m: number;
  ascent_m: number | null;
  duration_hike_min: number | null;
  duration_mtb_min: number | null;
  /** OSM sac_scale and mtb:scale as GraphHopper decoded them. */
  hike_rating: number | null;
  mtb_rating: number | null;
  /** Computed from map tags, never a promise about the surface underfoot. */
  off_road_share: number;
  score: number;
  named_pois: string[];
  trailhead_id: string;
  trailhead_name: string | null;
  start_lat: number;
  start_lon: number;
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
