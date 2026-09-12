/**
 * The saved-route set: what belongs in it, and whose it is.
 *
 * Small enough to have lived inline in page.tsx, which is exactly why it went
 * wrong twice — a set built from half the response, and a set that outlived
 * the account it belonged to. It lives here so page.tsx and the tests run the
 * same rules.
 */

import type { FavoritesList } from './api';

/**
 * Every id the ledger holds, hydrated or not.
 *
 * `missing` counts as saved: those ids ARE saved, their route is just out of
 * the catalogue until the next export restores it (ids are geometry-derived
 * and survive a rebuild). Dropping them showed the bookmark unfilled on a
 * route saved long ago, so one tap "saved" it — a no-op — and the next tap
 * deleted it.
 */
export function savedIds(list: FavoritesList): Set<string> {
  return new Set([...list.routes.map((route) => route.id), ...list.missing]);
}

/**
 * The optimistic flip, and its exact inverse for the rollback.
 *
 * Never mutates: the previous set is what the rollback flips back.
 */
export function applyToggle(current: Set<string>, id: string, on: boolean): Set<string> {
  const next = new Set(current);
  if (on) next.add(id);
  else next.delete(id);
  return next;
}

/**
 * May a response that started under `capturedUserId` still be rendered?
 *
 * Only if that is still who is signed in. A saved list is one account's, and
 * an in-flight request does not stop when the account changes — so a response
 * that outlived its sign-in must be dropped, not rendered under the next
 * person's name. Signed out (null) is nobody, and matches nobody.
 */
export function belongsToCurrentUser(
  capturedUserId: string,
  currentUserId: string | null | undefined,
): boolean {
  return Boolean(currentUserId) && capturedUserId === currentUserId;
}
