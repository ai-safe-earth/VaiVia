/**
 * Display formatting. The API speaks metres and minutes (schema.cypher
 * convention); humans do not. This is the only place that converts.
 */

export function distance(metres: number): string {
  if (metres < 1000) return `${Math.round(metres)} m`;
  return `${(metres / 1000).toFixed(1)} km`;
}

export function duration(minutes: number | null): string | null {
  if (minutes === null || minutes <= 0) return null;
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  if (hours === 0) return `${rest} min`;
  if (rest === 0) return `${hours} h`;
  return `${hours} h ${rest} min`;
}

export function elevation(metres: number | null): string | null {
  return metres === null ? null : `${Math.round(metres)} m`;
}

const HAZARD_LABELS: Record<string, string> = {
  snow: 'snow',
  ice: 'ice',
  mud_after_rain: 'mud after rain',
  rockfall: 'rockfall',
};

export function hazard(key: string): string {
  return HAZARD_LABELS[key] ?? key.replace(/_/g, ' ');
}

const POI_LABELS: Record<string, string> = {
  lake: 'lake',
  hut: 'mountain hut',
  campsite: 'campsite',
  station: 'train station',
  bathing_water: 'swimming spot',
  viewpoint: 'viewpoint',
};

export function poiLabel(type: string): string {
  return POI_LABELS[type] ?? type.replace(/_/g, ' ');
}

/** Which duration applies to a trail, given how it will be travelled. */
export function primaryDuration(trail: {
  activity: string;
  duration_hike_min: number | null;
  duration_mtb_min: number | null;
}): { label: string; value: string } | null {
  const isBike = trail.activity === 'mtb';
  const minutes = isBike ? trail.duration_mtb_min : trail.duration_hike_min;
  const formatted = duration(minutes);
  if (!formatted) return null;
  return { label: isBike ? 'ride' : 'walk', value: formatted };
}
