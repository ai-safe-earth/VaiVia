import type { RouteDetail, RouteProfile, RouteSpan } from '@/lib/types';

/** The bar-chart shape the first profile drawing used; kept for the tests
 *  that pin the thinning rule, which the SVG profile shares. */
export interface Profile {
  /** Metres above sea level, evenly spaced along the route. */
  samples: number[];
  startM: number;
  maxM: number;
  endM: number;
}

/** How many samples a profile renders with. The documents carry one sample
 *  per geometry vertex (hundreds); a path with more points than pixels only
 *  costs bytes, so the series is thinned to at most this many, evenly spaced
 *  along the route. */
const MAX_SAMPLES = 80;

/** Evenly spaced indices, the last element always kept: every value is a
 *  real measured height, never an average, and the end is the route's end. */
export function thin<T>(series: T[], max: number): T[] {
  if (series.length <= max) return series;
  const stride = (series.length - 1) / (max - 1);
  return Array.from({ length: max }, (_, i) => series[Math.round(i * stride)]!);
}

/**
 * The document's parallel arrays, as the bar chart wanted them.
 *
 * Thinning picks evenly spaced indices rather than averaging: each bar is a
 * real measured height, and the last sample is always kept so `end` is the
 * route's actual end, not the nearest stride.
 */
export function profileFromDetail(detail: RouteDetail | null): Profile | undefined {
  const series = detail?.profile?.elevation_m;
  if (!series || series.length < 2) return undefined;
  return {
    samples: thin(series, MAX_SAMPLES),
    startM: series[0]!,
    maxM: Math.max(...series),
    endM: series[series.length - 1]!,
  };
}

/** The surface classes the profile's band is coloured by, in the fixed order
 *  the legend lists them (never cycled): what the pipeline calls paved and
 *  unpaved, with the unpaved half split where a walker feels it — loose
 *  gravel against natural ground — and the untagged length shown as such,
 *  never renormalised away. */
export const SURFACE_ORDER = ['paved', 'gravel', 'ground', 'untagged'] as const;
export type SurfaceClass = (typeof SURFACE_ORDER)[number];

const PAVED = new Set([
  'asphalt',
  'concrete',
  'paved',
  'paving_stones',
  'sett',
  'cobblestone',
  'metal',
  'wood',
]);
const GRAVEL = new Set(['compacted', 'fine_gravel', 'gravel', 'pebblestone']);

export function surfaceClass(surface: string | null | undefined): SurfaceClass {
  if (!surface || surface === 'unknown') return 'untagged';
  if (PAVED.has(surface)) return 'paved';
  if (GRAVEL.has(surface)) return 'gravel';
  return 'ground';
}

/** Share of the route per class, from the walked spans (drawn routes). */
export function sharesFromSpans(spans: RouteSpan[]): Partial<Record<SurfaceClass, number>> {
  const metres: Partial<Record<SurfaceClass, number>> = {};
  let from = 0;
  for (const span of spans) {
    const cls = surfaceClass(span.surface);
    metres[cls] = (metres[cls] ?? 0) + Math.max(0, span.to_m - from);
    from = span.to_m;
  }
  const total = Object.values(metres).reduce((a, b) => a + b, 0);
  if (total <= 0) return {};
  return Object.fromEntries(
    Object.entries(metres).map(([cls, m]) => [cls, m / total]),
  ) as Partial<Record<SurfaceClass, number>>;
}

/** The same shares from a document's surface distribution (saved routes,
 *  which carry no spans). */
export function sharesFromDistribution(
  distribution: Record<string, number>,
): Partial<Record<SurfaceClass, number>> {
  const shares: Partial<Record<SurfaceClass, number>> = {};
  for (const [surface, share] of Object.entries(distribution)) {
    const cls = surfaceClass(surface);
    shares[cls] = (shares[cls] ?? 0) + share;
  }
  return shares;
}

/** The profile a card draws: the series aligned to the spans by metres. */
export interface DrawnProfile {
  series: RouteProfile;
  spans?: RouteSpan[];
  shares: Partial<Record<SurfaceClass, number>>;
  quality: 'ok' | 'approximate' | null;
}
