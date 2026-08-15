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

export interface RouteResult {
  total_distance_m: number;
  elevation_gain_m: number | null;
  start: string;
  end: string;
}

export interface ChatResults {
  kind: 'trail_search' | 'route' | 'clarify';
  trails?: Trail[];
  route?: RouteResult | null;
  geometry?: GeoJSON.LineString;
  unknown_place?: string;
  off_network?: string;
  no_path?: boolean;
  clarification?: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  results?: ChatResults;
  streaming?: boolean;
  error?: string;
}
