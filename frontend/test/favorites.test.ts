/**
 * The saved-route set: one account's, built from the whole response.
 *
 * These import lib/favorites.ts — the functions page.tsx calls — rather than
 * re-implementing them here. The earlier version mirrored the set arithmetic
 * locally, so it stayed green through both bugs review found: a set built
 * from half the response, and a set that outlived the account it belonged to.
 */

import { describe, expect, it } from 'vitest';

import type { FavoritesList } from '../lib/api';
import { applyToggle, belongsToCurrentUser, savedIds } from '../lib/favorites';
import type { Loop } from '../lib/types';

function list(routeIds: string[], missing: string[] = []): FavoritesList {
  return {
    routes: routeIds.map((id) => ({ id }) as Loop),
    missing,
  };
}

describe('optimistic favorite toggle', () => {
  it('adds and removes without touching other ids', () => {
    const start = new Set(['a', 'b']);
    expect([...applyToggle(start, 'c', true)].sort()).toEqual(['a', 'b', 'c']);
    expect([...applyToggle(start, 'a', false)].sort()).toEqual(['b']);
    expect([...start].sort()).toEqual(['a', 'b']); // never mutated in place
  });

  it('reverting a save that failed restores exactly what was there', () => {
    // The rollback matters for a toggle that CHANGED something, so the start
    // state is the one each direction actually flips.
    for (const [on, start] of [
      [true, new Set(['a'])],
      [false, new Set(['a', 'x'])],
    ] as const) {
      const optimistic = applyToggle(start, 'x', on);
      expect(optimistic.has('x')).toBe(on); // the flip really happened
      const reverted = applyToggle(optimistic, 'x', !on);
      expect([...reverted].sort()).toEqual([...start].sort());
    }
  });

  it('is idempotent, matching the backend', () => {
    const once = applyToggle(new Set(), 'a', true);
    const twice = applyToggle(once, 'a', true);
    expect([...twice]).toEqual(['a']);
  });
});

describe('the saved set', () => {
  it('counts a route that left the catalogue as still saved', () => {
    // The backend reports it missing rather than dropping it; the frontend
    // dropped it anyway, so its bookmark showed unfilled and the second tap
    // on it DELETED a favorite saved long ago.
    expect([...savedIds(list(['a'], ['b']))].sort()).toEqual(['a', 'b']);
  });

  it('is empty when nothing is saved', () => {
    expect(savedIds(list([])).size).toBe(0);
  });
});

describe('a saved list belongs to the account that asked for it', () => {
  it('renders only while that account is still signed in', () => {
    expect(belongsToCurrentUser('user-a', 'user-a')).toBe(true);
  });

  it('is dropped when the account changed while the request was out', () => {
    // Sign in as A, open the app, sign in as B before A's list lands: B must
    // not be shown A's saved routes.
    expect(belongsToCurrentUser('user-a', 'user-b')).toBe(false);
  });

  it('is dropped when nobody is signed in any more', () => {
    expect(belongsToCurrentUser('user-a', null)).toBe(false);
    expect(belongsToCurrentUser('user-a', undefined)).toBe(false);
  });

  it('keeps out-of-order responses from overwriting the current account', () => {
    // A's slow response resolving after B's fast one is the ordering that
    // makes this a leak rather than a flicker.
    const current = 'user-b';
    const arrivals = ['user-b', 'user-a'];
    const rendered = arrivals.filter((captured) =>
      belongsToCurrentUser(captured, current),
    );
    expect(rendered).toEqual(['user-b']);
  });
});
