/**
 * Which cards are open in place (owner decision 2026-09-29): a card opens
 * inside the transcript with its own map, stays open until its close button,
 * and at most MAX_OPEN are open at once — opening one more closes the oldest.
 *
 * Keys, not route ids: the same route can be offered by two answers, and each
 * of those cards opens on its own.
 */

export const MAX_OPEN = 3;

/** Open `key`, oldest first; already open is a no-op, one too many drops the
 *  oldest. Each open card holds a WebGL map, and browsers cap live contexts. */
export function openCard(open: readonly string[], key: string, max = MAX_OPEN): string[] {
  if (open.includes(key)) return [...open];
  const next = [...open, key];
  return next.slice(Math.max(0, next.length - max));
}

export function closeCard(open: readonly string[], key: string): string[] {
  return open.filter((candidate) => candidate !== key);
}

/** A card's key: the turn that offered it and the route. */
export function cardKey(scope: string | number, routeId: string): string {
  return `${scope}:${routeId}`;
}
