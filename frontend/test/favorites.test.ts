/**
 * The favorites toggle: optimistic, revertible, one state for every surface.
 *
 * Mirrors page.tsx's set arithmetic the way loop.test.ts mirrors the fold:
 * the page flips the set first and flips it back if the save fails, so the
 * arithmetic must be exactly involutive.
 */

import { describe, expect, it } from 'vitest';

/** Mirrors toggleFavorite's setFavoriteIds updater in app/page.tsx. */
function applyToggle(current: Set<string>, id: string, on: boolean): Set<string> {
  const next = new Set(current);
  if (on) next.add(id);
  else next.delete(id);
  return next;
}

describe('optimistic favorite toggle', () => {
  it('adds and removes without touching other ids', () => {
    const start = new Set(['a', 'b']);
    expect([...applyToggle(start, 'c', true)].sort()).toEqual(['a', 'b', 'c']);
    expect([...applyToggle(start, 'a', false)].sort()).toEqual(['b']);
    expect([...start].sort()).toEqual(['a', 'b']); // never mutated in place
  });

  it('reverting is the inverse toggle, exactly', () => {
    const start = new Set(['a']);
    for (const on of [true, false]) {
      const optimistic = applyToggle(start, 'x', on);
      const reverted = applyToggle(optimistic, 'x', !on);
      expect([...reverted].sort()).toEqual([...applyToggle(start, 'x', !on)].sort());
    }
  });

  it('is idempotent, matching the backend', () => {
    const once = applyToggle(new Set(), 'a', true);
    const twice = applyToggle(once, 'a', true);
    expect([...twice]).toEqual(['a']);
  });
});
