/**
 * Whose answer is on the map.
 *
 * The drawn set belongs to ONE answer. Two ways it stopped doing so, both
 * found by review on 2026-08-21: "show more" under an older answer merged
 * that answer's routes into the current one's drawn set, and a slow card's
 * geometry landed on the map after the user had already picked another card.
 *
 * Mirrors ChatPanel.revealLoops and FavoritesView.select the way
 * favorites.test.ts mirrors the toggle: pure functions here, the same
 * decision there.
 */

import { describe, expect, it } from 'vitest';

/** Mirrors revealLoops' decision in components/ChatPanel.tsx. */
function reveal(
  drawnTurn: number | null,
  turn: number,
  from: number,
  to: number,
): { clear: boolean; slice: [number, number]; drawnTurn: number } {
  if (drawnTurn === turn) {
    return { clear: false, slice: [from, to], drawnTurn: turn };
  }
  return { clear: true, slice: [0, to], drawnTurn: turn };
}

/** Mirrors the stale-selection guard in FavoritesView.select / ChatPanel. */
function mayPaint(selectedNow: string | null, resolvedFor: string): boolean {
  return selectedNow === resolvedFor;
}

describe('the map shows one answer', () => {
  it('adds to the drawn set when the reveal is from the answer already drawn', () => {
    expect(reveal(3, 3, 5, 10)).toEqual({
      clear: false,
      slice: [5, 10],
      drawnTurn: 3,
    });
  });

  it('takes the map over, from the first card, when the reveal is from another answer', () => {
    // Answer 3 is drawn (B1..B5); "show 5 more" under answer 1 must show
    // answer 1 whole (A1..A10), not B1..B5 + A6..A10 — a map of neither.
    expect(reveal(3, 1, 5, 10)).toEqual({
      clear: true,
      slice: [0, 10],
      drawnTurn: 1,
    });
  });

  it('draws the revealed answer whole when nothing is drawn yet', () => {
    // A stored conversation reopened: no turn owns the map, so the answer
    // being revealed becomes the one shown.
    expect(reveal(null, 2, 5, 10)).toEqual({
      clear: true,
      slice: [0, 10],
      drawnTurn: 2,
    });
  });
});

describe('a late result never repaints another card', () => {
  it('paints only when its card is still the selected one', () => {
    expect(mayPaint('b', 'a')).toBe(false); // A resolved after B was picked
    expect(mayPaint('b', 'b')).toBe(true);
    expect(mayPaint(null, 'a')).toBe(false); // selection cleared meanwhile
  });
});

/**
 * The three rules useRouteDetails keeps for every card list, mirrored the way
 * the rest of this suite mirrors component logic. They were dropped once, in
 * the second copy of this code: the saved-routes view had no in-flight guard,
 * no stale-selection guard, and left a failed fetch looking like a card that
 * was still loading.
 */
type Cache = Record<string, unknown | null>;

function shouldFetch(details: Cache, inFlight: Set<string>, id: string): boolean {
  if (id in details) return false;
  return !inFlight.has(id);
}

describe('a route detail is asked for once', () => {
  it('does not refetch what is already known, including a known failure', () => {
    expect(shouldFetch({ a: { profile: 1 } }, new Set(), 'a')).toBe(false);
    // null is "asked, there is none" — not "never asked".
    expect(shouldFetch({ a: null }, new Set(), 'a')).toBe(false);
    expect(shouldFetch({}, new Set(), 'a')).toBe(true);
  });

  it('does not start a second fetch while the first is in flight', () => {
    expect(shouldFetch({}, new Set(['a']), 'a')).toBe(false);
  });
});
