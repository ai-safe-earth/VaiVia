/**
 * When a route's detail is asked for.
 *
 * This imports the function the hook actually calls (lib/useRouteDetails.ts). An earlier version of this file re-implemented the
 * rules locally, which is worth nothing: inverting a guard in ChatPanel would
 * have left every assertion here green.
 */

import { describe, expect, it } from 'vitest';

import { shouldFetch } from '../lib/useRouteDetails';
import type { RouteDetail } from '../lib/types';

const KNOWN = {} as unknown as RouteDetail;

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
