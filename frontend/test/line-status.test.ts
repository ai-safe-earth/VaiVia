/**
 * What a card click does to the map, and what a fetched line must prove
 * before it is drawn.
 *
 * These are the rules behind the "any card shows any map" defect: a click
 * was the one entry point with no turn guard, and a failed or mismatched
 * line was silently absent — so clicking its card drew the SIBLINGS,
 * framed them, and wore the clicked card's name.
 *
 * Like drawn-turn.test.ts, this imports the functions the components call.
 */

import { describe, expect, it } from 'vitest';

import {
  drawableFeatures,
  focusedRouteId,
  needsFetch,
  noteOf,
  planSelect,
  recordLine,
  type LineEntry,
} from '../lib/mapTurn';

function line(id: string, note?: string): GeoJSON.Feature {
  return {
    type: 'Feature',
    properties: note ? { route_id: id, note } : { route_id: id },
    geometry: {
      type: 'LineString',
      coordinates: [
        [9.3, 45.8],
        [9.4, 45.9],
      ],
    },
  };
}

describe('a click is guarded by turn, like a reveal', () => {
  it('restyles in place when the clicked card belongs to the drawn answer', () => {
    expect(planSelect(3, 3, 5, 1)).toEqual({
      takeover: false,
      slice: [0, 5],
      drawnTurn: 3,
    });
  });

  it('takes the map over when the clicked card belongs to another answer', () => {
    // Answer 3 is drawn; a click on answer 1's card must show answer 1,
    // never answer 3's cache wearing answer 1's name.
    expect(planSelect(3, 1, 5, 1)).toEqual({
      takeover: true,
      slice: [0, 5],
      drawnTurn: 1,
    });
  });

  it('takes the map over when nothing is drawn yet (a resumed transcript)', () => {
    expect(planSelect(null, 2, 5, 0)).toEqual({
      takeover: true,
      slice: [0, 5],
      drawnTurn: 2,
    });
  });

  it('reaches past the fold to the clicked card when it was revealed', () => {
    // Card 7 of an answer whose fold is 5: the slice must include it, or the
    // takeover would draw an answer that omits the very card clicked.
    expect(planSelect(1, 2, 5, 7).slice).toEqual([0, 8]);
  });
});

describe('a fetched line proves it is the requested route', () => {
  it('records the line when its route_id matches', () => {
    expect(recordLine(line('a'), 'a')).toEqual({ status: 'ok', feature: line('a') });
  });

  it('records a 404 as missing — settled, not retried', () => {
    expect(recordLine(null, 'a')).toEqual({ status: 'missing' });
    expect(needsFetch({ status: 'missing' })).toBe(false);
  });

  it('records a payload whose route_id disagrees as an error, never drawable', () => {
    // Whatever crossed the wires to produce it, a response that says it is
    // route B must not be drawn under card A's name.
    expect(recordLine(line('b'), 'a')).toEqual({ status: 'error' });
  });

  it('rejects a payload that carries no route_id at all', () => {
    // Verification must not be opt-in: the malformed payloads it exists for
    // are exactly the ones that would omit the id.
    const bare: GeoJSON.Feature = { ...line('a'), properties: {} };
    expect(recordLine(bare, 'a')).toEqual({ status: 'error' });
  });

  it('retries errors, holds ok and unknown-not-asked is fetched', () => {
    expect(needsFetch({ status: 'error' })).toBe(true);
    expect(needsFetch({ status: 'ok', feature: line('a') })).toBe(false);
    expect(needsFetch(undefined)).toBe(true);
  });
});

describe('what the map may draw for a selection', () => {
  const entries = new Map<string, LineEntry>([
    ['a', { status: 'ok', feature: line('a') }],
    ['b', { status: 'error' }],
    ['c', { status: 'ok', feature: line('c') }],
  ]);

  it('draws every ok line, selection marked, when the selected line is ok', () => {
    const features = drawableFeatures(entries, 'a')!;
    expect(features.map((f) => f.properties?.id)).toEqual(['a', 'c']);
    expect(features.map((f) => f.properties?.selected)).toEqual([true, false]);
  });

  it('draws nothing when the selected line is unavailable', () => {
    // The old behaviour drew a and c unselected and the map FIT to them —
    // the wrong routes framed under the clicked card's name.
    expect(drawableFeatures(entries, 'b')).toBeNull();
    expect(drawableFeatures(entries, 'zzz')).toBeNull();
  });

  it('draws everything, nothing selected, when nothing is selected on purpose', () => {
    const features = drawableFeatures(entries, null)!;
    expect(features).toHaveLength(2);
    expect(features.every((f) => f.properties?.selected === false)).toBe(true);
  });

  it('draws nothing when nothing is ok', () => {
    expect(drawableFeatures(new Map([['a', { status: 'error' } as LineEntry]]), null)).toBeNull();
  });
});

describe('the drawn line says what it is', () => {
  it('surfaces the selected feature of a collection', () => {
    const collection: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: [
        { ...line('a'), properties: { route_id: 'a', selected: false } },
        {
          ...line('b', 'multi-part route: longest of 3 pieces shown'),
          properties: {
            route_id: 'b',
            selected: true,
            note: 'multi-part route: longest of 3 pieces shown',
          },
        },
      ],
    };
    expect(focusedRouteId(collection)).toBe('b');
    expect(noteOf(collection)).toBe('multi-part route: longest of 3 pieces shown');
  });

  it('surfaces a sole feature without a selected mark (a trail, a saved route)', () => {
    expect(focusedRouteId(line('a'))).toBe('a');
    expect(noteOf(line('a'))).toBeNull();
  });

  it('says nothing about a many-feature collection with no selection', () => {
    const collection: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: [line('a'), line('b')],
    };
    expect(focusedRouteId(collection)).toBeNull();
    expect(noteOf(collection)).toBeNull();
  });
});
