/**
 * What a fetched line must prove before an open card's map draws it: a
 * failed or mismatched line is said on the card, never drawn under its name.
 *
 * Like drawn-turn.test.ts, this imports the functions the components call.
 */

import { describe, expect, it } from 'vitest';

import {
  focusedRouteId,
  needsFetch,
  noteOf,
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
