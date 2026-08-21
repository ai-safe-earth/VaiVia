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
export function useRouteDetails(onDetail?: (detail: RouteDetail | null) => void) {
  // undefined = never asked, null = asked and there is none.
  const [details, setDetails] = useState<Record<string, RouteDetail | null>>({});
  const inFlight = useRef<Set<string>>(new Set());
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
    if (routeId in details) {
      if (current() === routeId) onDetail?.(details[routeId] ?? null);
      return;
    }
    if (inFlight.current.has(routeId)) return;
    inFlight.current.add(routeId);
    try {
      const detail = await fetchRouteDetail(routeId);
      setDetails((entries) => ({ ...entries, [routeId]: detail }));
      if (current() === routeId) onDetail?.(detail);
    } catch {
      setDetails((entries) => ({ ...entries, [routeId]: null }));
      if (current() === routeId) onDetail?.(null);
    } finally {
      inFlight.current.delete(routeId);
    }
  }

  return { details, load, select, current };
}
