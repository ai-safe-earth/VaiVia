import type { Profile } from '@/components/MapChrome';
import type { RouteDetail } from '@/lib/types';

/** How many bars a profile renders with. The documents carry one sample per
 *  geometry vertex (hundreds); bars below ~3px stop being readable, so the
 *  series is thinned to at most this many, evenly spaced along the route. */
const MAX_SAMPLES = 80;

/**
 * The document's parallel arrays, as the bar chart wants them.
 *
 * Thinning picks evenly spaced indices rather than averaging: each bar is a
 * real measured height, and the last sample is always kept so `end` is the
 * route's actual end, not the nearest stride.
 */
export function profileFromDetail(detail: RouteDetail | null): Profile | undefined {
  const series = detail?.profile?.elevation_m;
  if (!series || series.length < 2) return undefined;

  let samples = series;
  if (series.length > MAX_SAMPLES) {
    const stride = (series.length - 1) / (MAX_SAMPLES - 1);
    samples = Array.from(
      { length: MAX_SAMPLES },
      (_, i) => series[Math.round(i * stride)],
    );
  }
  return {
    samples,
    startM: series[0],
    maxM: Math.max(...series),
    endM: series[series.length - 1],
  };
}
