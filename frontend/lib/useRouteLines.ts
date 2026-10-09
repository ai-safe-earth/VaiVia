'use client';

import { useRef, useState } from 'react';

import { fetchRouteGeoJson } from './api';
import { inlineLine, needsFetch, recordLine, type LineEntry } from './mapTurn';
import type { Loop } from './types';

/**
 * Each open card's map line, kept per route with its fetch OUTCOME: a drawn
 * route carries its line inline, a saved one is fetched once; a 404 is
 * settled, anything else retries on the next open, and a payload that is not
 * this route is recorded as an error — never drawn under its name.
 *
 * One copy for the chat and the saved-routes view, so a card behaves the same
 * wherever it is opened.
 */
export function useRouteLines() {
  const [lines, setLines] = useState<Record<string, LineEntry>>({});
  const known = useRef<Map<string, LineEntry>>(new Map());
  const inFlight = useRef<Set<string>>(new Set());

  function settle(id: string, entry: LineEntry) {
    known.current.set(id, entry);
    setLines((current) => ({ ...current, [id]: entry }));
  }

  async function ensure(loop: Loop): Promise<void> {
    if (!needsFetch(known.current.get(loop.id)) || inFlight.current.has(loop.id)) return;
    const inline = inlineLine(loop);
    if (inline) {
      settle(loop.id, inline);
      return;
    }
    inFlight.current.add(loop.id);
    try {
      settle(loop.id, recordLine(await fetchRouteGeoJson(loop.id), loop.id));
    } catch {
      settle(loop.id, { status: 'error' });
    } finally {
      inFlight.current.delete(loop.id);
    }
  }

  return { lines, ensure };
}
