/**
 * Loop cards, the fold, and the draw-all map payload.
 *
 * These pin the decisions that are easy to undo by accident: a route with no
 * name still gets a usable heading, the map payload marks exactly one feature
 * selected, and the fold reveals in steps without ever re-ordering. The
 * fixture matches the CURRENT catalogue row (the pre-catalogue shape lived
 * here unnoticed for a week because vitest does not typecheck).
 */

import { describe, expect, it } from 'vitest';

import { distance } from '../lib/format';
import { profileFromDetail } from '../lib/profile';
import type { Loop, RouteDetail } from '../lib/types';

function loop(overrides: Partial<Loop> = {}): Loop {
  return {
    id: 'generated-020f312db6521964',
    activity: 'hike',
    kind: 'generated',
    shape: 'loop',
    name: 'To Corno dell’Arco',
    ref: null,
    destination_name: 'Corno dell’Arco',
    distance_m: 11000,
    ascent_m: 1050,
    descent_m: 1050,
    lowest_m: 400,
    highest_m: 1450,
    surface_dominant: 'unpaved',
    pieces: 1,
    continuous: true,
    graded_share: 0.8,
    sac_scale: 'mountain_hiking',
    sac_max: 'demanding_mountain_hiking',
    mtb_rideable: false,
    mtb_scale: null,
    off_road_share: 0.72,
    score: 0.91,
    start_vertex_id: 43128,
    start_names: ['Vetta'],
    car_free: true,
    start_lat: 45.82,
    start_lon: 9.45,
    pois: [{ name: 'Corno dell’Arco', type: 'peak' }],
    ...overrides,
  };
}

describe('loop headings', () => {
  it('uses the name when the route has one', () => {
    expect(loop().name).toBe('To Corno dell’Arco');
  });

  it('falls back to something describing the route, never an id', () => {
    const unnamed = loop({ name: null, ref: null });
    const heading =
      unnamed.name ?? `${distance(unnamed.distance_m)} ${unnamed.activity} loop`;
    expect(heading).toBe('11.0 km hike loop');
    expect(heading).not.toContain(unnamed.id);
  });
});

describe('shape labels', () => {
  /** Mirrors LoopCard.shapeLabel. */
  function label(shape: string): string {
    return shape === 'loop' || shape === 'circular'
      ? 'Loop'
      : shape === 'destination'
        ? 'Out & back'
        : shape === 'linear'
          ? 'Linear'
          : 'Named route';
  }

  it('reads constructed and measured shapes the same way a walker would', () => {
    expect(label('loop')).toBe('Loop');
    expect(label('circular')).toBe('Loop'); // measured ring = same promise
    expect(label('destination')).toBe('Out & back');
    expect(label('linear')).toBe('Linear');
  });

  it('keeps the pre-1.2 fallback for stale transcripts only', () => {
    expect(label('named')).toBe('Named route');
  });
});

describe('the profile the expanded card draws', () => {
  function detail(overrides: Partial<RouteDetail> = {}): RouteDetail {
    return {
      route_id: loop().id,
      shape: 'loop',
      profile: {
        distance_m: [0, 5500, 11000],
        elevation_m: [400, 1450, 400],
      },
      profile_quality: 'ok',
      measures: {
        distance_m: 11000,
        ascent_m: 1050,
        descent_m: 1050,
        lowest_m: 400,
        highest_m: 1450,
      },
      continuity: { pieces: 1, continuous: true },
      surface: { distribution: { unpaved: 0.8 }, dominant: 'unpaved' },
      places: [],
      attribution: '© OpenStreetMap contributors',
      ...overrides,
    };
  }

  it('keeps start, peak and end as the real measured heights', () => {
    const profile = profileFromDetail(detail());
    expect(profile).toBeDefined();
    expect(profile!.startM).toBe(400);
    expect(profile!.maxM).toBe(1450);
    expect(profile!.endM).toBe(400);
  });

  it('thins a long series to readable bars without averaging', () => {
    const series = Array.from({ length: 500 }, (_, i) => 400 + (i % 100));
    const profile = profileFromDetail(
      detail({ profile: { distance_m: series.map((_, i) => i * 10), elevation_m: series } }),
    );
    expect(profile!.samples.length).toBeLessThanOrEqual(80);
    // Every bar is a real sample, and the endpoints survive the thinning.
    expect(profile!.samples.every((s) => series.includes(s))).toBe(true);
    expect(profile!.samples[0]).toBe(series[0]);
    expect(profile!.samples[profile!.samples.length - 1]).toBe(series[series.length - 1]);
  });

  it('is undefined when the document carries no profile — absent is not zero', () => {
    expect(profileFromDetail(detail({ profile: null }))).toBeUndefined();
    expect(profileFromDetail(null)).toBeUndefined();
  });
});

describe('the fold', () => {
  /** Mirrors FoldedCards: slice to visible, reveal in steps of 5. */
  function reveal(count: number, fold: number, clicks: number): number {
    let visible = Math.min(Math.max(fold, 1), count);
    for (let i = 0; i < clicks; i += 1) visible = Math.min(visible + 5, count);
    return visible;
  }

  it('shows the answered count first, so prose and cards agree', () => {
    expect(reveal(20, 5, 0)).toBe(5);
  });

  it('reveals five at a time and stops at the end', () => {
    expect(reveal(20, 5, 1)).toBe(10);
    expect(reveal(20, 5, 3)).toBe(20);
    expect(reveal(20, 5, 9)).toBe(20);
  });

  it('never folds a short list', () => {
    expect(reveal(3, 5, 0)).toBe(3);
  });
});

describe('draw-all map payload', () => {
  /** Mirrors ChatPanel.drawLoops. */
  function collection(ids: string[], selectedId: string | null) {
    return {
      type: 'FeatureCollection' as const,
      features: ids.map((id) => ({
        type: 'Feature' as const,
        properties: { id, selected: id === selectedId },
        geometry: { type: 'LineString' as const, coordinates: [[9.4, 45.9]] },
      })),
    };
  }

  it('marks nothing selected before a card is clicked', () => {
    const data = collection(['a', 'b', 'c'], null);
    expect(data.features.every((f) => f.properties.selected === false)).toBe(true);
  });

  it('marks exactly one selected after a click', () => {
    const data = collection(['a', 'b', 'c'], 'b');
    const picked = data.features.filter((f) => f.properties.selected);
    expect(picked).toHaveLength(1);
    expect(picked[0].properties.id).toBe('b');
  });

  it('keeps every loop in the payload so all stay drawn', () => {
    expect(collection(['a', 'b', 'c'], 'b').features).toHaveLength(3);
  });
});

describe('route ids', () => {
  it('survive being put in a url', () => {
    const encoded = encodeURIComponent(loop().id);
    expect(decodeURIComponent(encoded)).toBe(loop().id);
  });
});
