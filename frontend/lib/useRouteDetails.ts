'use client';

import { useRef, useState } from 'react';

import { fetchRouteDetail } from './api';
import type { RouteDetail } from './types';

/**
 * A route's document detail, fetched once and remembered, for whichever list
 * of cards is on screen.
 *
 * The chat panel and the saved-routes view both do this, and they did it
 * twice: the second copy dropped the in-flight guard, dropped the
 * stale-selection guard, and let a failed fetch leave the card fetching a
 * profile for ever. One copy, so a card behaves the same wherever it is drawn.
 *
 * The three rules it keeps:
 *   * asked once — a second click while the first is in flight is ignored;
 *   * a failure records null, which is "we asked and there is none", never an
 *     absent entry the card reads as "still loading";
 *   * a late answer only paints if its card is still the selected one, so a
 *     slow card A cannot land on top of card B.
 */
/**
 * Is this route's detail worth asking the gateway for?
 *
 * No if it is already known, no if a request for it is already out — and YES
 * even when it is "known" as null, if that null came from a failure rather
 * than from a 404. Exported so the test exercises the rule the hook runs.
 */
export function shouldFetch(
  known: Record<string, RouteDetail | null>,
  retryable: ReadonlySet<string>,
  inFlight: ReadonlySet<string>,
  routeId: string,
): boolean {
  if (inFlight.has(routeId)) return false;
  if (routeId in known && !retryable.has(routeId)) return false;
  return true;
}

export function useRouteDetails(onDetail?: (detail: RouteDetail | null) => void) {
  // undefined = never asked, null = asked and there is none.
  const [details, setDetails] = useState<Record<string, RouteDetail | null>>({});
  const inFlight = useRef<Set<string>>(new Set());
  // Routes whose null is a FAILURE rather than an answer. fetchRouteDetail
  // returns null for a 404 -- "this route has no document", which is settled
  // -- and throws for anything else. A 401 or a 503 cached as "no profile"
  // for the rest of the session is a temporary outage turned permanent, so
  // those stay retryable: the card still says "no profile" instead of
  // spinning, but asking again asks again.
  const retryable = useRef<Set<string>>(new Set());
  // Selection, readable from an async continuation without a stale closure.
  const selectedRef = useRef<string | null>(null);

  /** Mark what is selected now. Pass null when nothing is. */
  function select(routeId: string | null): void {
    selectedRef.current = routeId;
  }

  /** What is selected now — for a continuation deciding whether to paint. */
  function current(): string | null {
    return selectedRef.current;
  }

  async function load(routeId: string): Promise<void> {
    if (!shouldFetch(details, retryable.current, inFlight.current, routeId)) {
      if (routeId in details && current() === routeId) {
        onDetail?.(details[routeId] ?? null);
      }
      return;
    }
    inFlight.current.add(routeId);
    try {
      const detail = await fetchRouteDetail(routeId);
      retryable.current.delete(routeId);
      setDetails((entries) => ({ ...entries, [routeId]: detail }));
      if (current() === routeId) onDetail?.(detail);
    } catch {
      retryable.current.add(routeId);
      setDetails((entries) => ({ ...entries, [routeId]: null }));
      if (current() === routeId) onDetail?.(null);
    } finally {
      inFlight.current.delete(routeId);
    }
  }

  return { details, load, select, current };
}
