/**
 * Whose answer owns the map.
 *
 * The drawn set belongs to ONE answer. Two ways it stopped doing so, both
 * found by review: "show more" under an older answer merged that answer's
 * routes into the current one's drawn set, and a slow fetch's continuation
 * wrote into a set another answer had taken over in the meantime.
 *
 * The rules live here, as functions ChatPanel calls and the tests import —
 * not as prose a test mirrors, which is how a race guard can be inverted in
 * the component while its test stays green.
 */

export interface RevealPlan {
  /** Drop what is drawn: this reveal comes from another answer. */
  clear: boolean;
  /** The [from, to) slice of that answer's loops to fetch and draw. */
  slice: [number, number];
  /** The answer that owns the map after this reveal. */
  drawnTurn: number;
}

/**
 * What "show more" should do, given who owns the map now.
 *
 * Revealing cards of the answer already drawn ADDS to it. From any other
 * answer it is a change of subject: that answer takes the map over, drawn
 * from its first card, so what is shown is one answer whole and never a mix.
 */
export function planReveal(
  drawnTurn: number | null,
  turn: number,
  from: number,
  to: number,
): RevealPlan {
  if (drawnTurn === turn) {
    return { clear: false, slice: [from, to], drawnTurn: turn };
  }
  return { clear: true, slice: [0, to], drawnTurn: turn };
}

/**
 * May a fetch that has just resolved write into the drawn set?
 *
 * Only if the answer it was dispatched for still owns the map. Guarding the
 * dispatch alone left the continuation free to merge an older answer's routes
 * into a newer one's.
 */
export function mayDraw(drawnTurn: number | null, turn: number): boolean {
  return drawnTurn === turn;
}

/**
 * May a continuation paint for this card?
 *
 * Only if it is still the selected one, so a slow card A cannot land on top
 * of card B.
 */
export function isStillSelected(selectedNow: string | null, resolvedFor: string): boolean {
  return selectedNow === resolvedFor;
}
