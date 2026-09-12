/**
 * Whose answer owns the map, and when a late answer may still paint.
 *
 * These import the functions the components actually call (lib/mapTurn.ts,
 * lib/useRouteDetails.ts). An earlier version of this file re-implemented the
 * rules locally, which is worth nothing: inverting a guard in ChatPanel would
 * have left every assertion here green.
 */

import { describe, expect, it } from 'vitest';

import { isStillSelected, mayDraw, planReveal } from '../lib/mapTurn';
import { shouldFetch } from '../lib/useRouteDetails';
import type { RouteDetail } from '../lib/types';

const KNOWN = {} as unknown as RouteDetail;

describe('the map shows one answer', () => {
  it('adds to the drawn set when the reveal is from the answer already drawn', () => {
    // The slice runs from 0: the fetch behind it skips lines already held,
    // and starting at the reveal point left holes a click-takeover made
    // (cards 5-9 revealed, takeover redrew 0-4, "show more" fetched 10-11
    // and 5-9 stayed line-less for ever).
    expect(planReveal(3, 3, 5, 10)).toEqual({
      clear: false,
      slice: [0, 10],
      drawnTurn: 3,
    });
  });

  it('takes the map over, from the first card, when the reveal is from another answer', () => {
    // Answer 3 is drawn (B1..B5); "show 5 more" under answer 1 must show
    // answer 1 whole (A1..A10), not B1..B5 + A6..A10 — a map of neither.
    expect(planReveal(3, 1, 5, 10)).toEqual({
      clear: true,
      slice: [0, 10],
      drawnTurn: 1,
    });
  });

  it('draws the revealed answer whole when nothing is drawn yet', () => {
    // A stored conversation reopened: no answer owns the map, so the one
    // being revealed becomes the one shown.
    expect(planReveal(null, 2, 5, 10)).toEqual({
      clear: true,
      slice: [0, 10],
      drawnTurn: 2,
    });
  });
});

describe('a slow fetch cannot write into a set that moved on', () => {
  it('draws only while its own answer still owns the map', () => {
    expect(mayDraw(2, 2)).toBe(true);
    expect(mayDraw(3, 2)).toBe(false); // answer 3 took over while this was out
    expect(mayDraw(null, 2)).toBe(false);
  });

  it('paints only while its own card is still selected', () => {
    expect(isStillSelected('b', 'a')).toBe(false); // A resolved after B was picked
    expect(isStillSelected('b', 'b')).toBe(true);
    expect(isStillSelected(null, 'a')).toBe(false);
  });
});

describe('a route detail is asked for once, unless the answer was a failure', () => {
  const none = new Set<string>();

  it('does not refetch a detail already known', () => {
    expect(shouldFetch({ a: KNOWN }, none, none, 'a')).toBe(false);
  });

  it('does not refetch a 404 — "there is no document" is an answer', () => {
    expect(shouldFetch({ a: null }, none, none, 'a')).toBe(false);
  });

  it('DOES refetch a null that came from a failure', () => {
    // A 401 or a 503 cached for the session turns an outage into a permanent
    // "no altitude profile" on a route that has one.
    expect(shouldFetch({ a: null }, new Set(['a']), none, 'a')).toBe(true);
  });

  it('does not start a second request while the first is out', () => {
    expect(shouldFetch({}, new Set(['a']), new Set(['a']), 'a')).toBe(false);
    expect(shouldFetch({}, none, new Set(['a']), 'a')).toBe(false);
  });

  it('fetches what it has never asked for', () => {
    expect(shouldFetch({}, none, none, 'a')).toBe(true);
  });
});
