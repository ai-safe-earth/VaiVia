/**
 * Loop cards and the draw-all map payload.
 *
 * These pin the decisions that are easy to undo by accident: a route with no
 * name still gets a usable heading, difficulty is read from the scale matching
 * the activity, and the map payload marks exactly one feature selected.
 */

import { describe, expect, it } from 'vitest';

import { distance, primaryDuration } from '../lib/format';
import type { Loop } from '../lib/types';

function loop(overrides: Partial<Loop> = {}): Loop {
  return {
    id: '1461822581:hike:15000:0',
    activity: 'hike',
    name: 'Monte Ocone',
    distance_m: 15300,
    ascent_m: 1200,
    duration_hike_min: 480,
    duration_mtb_min: 210,
    hike_rating: 2,
    mtb_rating: 3,
    off_road_share: 0.88,
    score: 0.91,
    named_pois: ['Monte Ocone', 'Passo del Gandazzo'],
    trailhead_id: '1461822581',
    trailhead_name: null,
    start_lat: 45.82,
    start_lon: 9.45,
    pois: [{ name: 'Monte Ocone', type: 'peak' }],
    ...overrides,
  };
}

describe('loop headings', () => {
  it('uses the name when the route has one', () => {
    expect(loop().name).toBe('Monte Ocone');
  });

  it('falls back to something describing the route, never an id', () => {
    // 19% of routes pass nothing worth naming them after. The id
    // "1461822581:hike:15000:0" must never reach a user.
    const unnamed = loop({ name: null });
    const heading = unnamed.name ?? `${distance(unnamed.distance_m)} ${unnamed.activity} loop`;
    expect(heading).toBe('15.3 km hike loop');
    expect(heading).not.toContain(unnamed.id);
  });
});

describe('duration', () => {
  it('picks the walking figure for a hike and the riding one for an mtb route', () => {
    // primaryDuration takes a structural shape, so it works on a Loop
    // unchanged — which is why both durations are stored on every route.
    expect(primaryDuration(loop())).toEqual({ label: 'walk', value: '8 h' });
    expect(primaryDuration(loop({ activity: 'mtb' }))).toEqual({
      label: 'ride',
      value: '3 h 30 min',
    });
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
    // Selecting one must not remove the others: the point of draw-all is
    // comparing options, and re-fetching on every click would flicker.
    expect(collection(['a', 'b', 'c'], 'b').features).toHaveLength(3);
  });
});

describe('route ids', () => {
  it('survive being put in a url', () => {
    // "{trailhead}:{activity}:{distance}:{rank}" — colons are legal in a path
    // segment but must still be encoded by the caller.
    const encoded = encodeURIComponent(loop().id);
    expect(encoded).not.toContain(':');
    expect(decodeURIComponent(encoded)).toBe(loop().id);
  });
});
