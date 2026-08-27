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

import type { DifficultyBand } from './difficulty';

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
 *
 * The slice runs from 0 in BOTH cases: the fetch behind it skips lines it
 * already holds, so on the drawn answer this costs nothing extra — and it
 * backfills any hole an earlier click-takeover left behind the fold.
 */
export function planReveal(
  drawnTurn: number | null,
  turn: number,
  _from: number,
  to: number,
): RevealPlan {
  return { clear: drawnTurn !== turn, slice: [0, to], drawnTurn: turn };
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

/**
 * What a click on a card should do, given who owns the map now.
 *
 * A click was the one entry point without a turn guard: every older answer's
 * cards stay rendered and clickable in the transcript, and clicking one drew
 * whatever answer's features happened to be cached — "any card shows any
 * map". A click on the drawn answer restyles in place; from any other answer
 * it is a change of subject, exactly like planReveal: that answer takes the
 * map over. The slice always reaches the clicked card, which may sit past
 * the fold if it was revealed before the takeover.
 */
export interface SelectPlan {
  /** Refetch and redraw: the click comes from an answer not on the map. */
  takeover: boolean;
  /** The [0, to) slice of that answer's loops the map needs. */
  slice: [number, number];
  /** The answer that owns the map after this click. */
  drawnTurn: number;
}

export function planSelect(
  drawnTurn: number | null,
  turn: number,
  fold: number,
  clickedIndex: number,
): SelectPlan {
  const to = Math.max(fold, clickedIndex + 1);
  return { takeover: drawnTurn !== turn, slice: [0, to], drawnTurn: turn };
}

/**
 * One card's map line, with the fetch outcome kept beside it.
 *
 * A failed fetch used to leave the id silently absent, so clicking that card
 * drew its four siblings with nothing selected and the map fit to them — a
 * picture of the wrong routes wearing the clicked card's name. The status is
 * what lets the card say "line unavailable" instead.
 */
export type LineEntry =
  | { status: 'ok'; feature: GeoJSON.Feature }
  /** 404 — the route left the catalogue. Settled; not refetched. */
  | { status: 'missing' }
  /** Anything else (503, 401, a mismatched payload). Retried on next ask. */
  | { status: 'error' };

export type LineStatus = LineEntry['status'];

/**
 * Record a geometry fetch's outcome — verifying that the payload IS the
 * requested route. The backend stamps properties.route_id on every route
 * response; one that disagrees with the id it was fetched for — or carries
 * none at all — must never be drawn under that card's name, whatever
 * crossed the wires to produce it. Tolerating an absent id would make the
 * verification opt-in for exactly the malformed payloads it exists for.
 */
export function recordLine(feature: GeoJSON.Feature | null, routeId: string): LineEntry {
  if (feature === null) return { status: 'missing' };
  if (feature.properties?.route_id !== routeId) return { status: 'error' };
  return { status: 'ok', feature };
}

/** Should this entry be fetched (again)? Errors retry; ok and missing hold. */
export function needsFetch(entry: LineEntry | undefined): boolean {
  return entry === undefined || entry.status === 'error';
}

/**
 * The features the map should draw for a selection, or null for "draw
 * nothing".
 *
 * The rule that closes the sibling leak: when something IS selected but its
 * line is not drawable, the map clears rather than showing the selection's
 * siblings — MapView fits the map to whatever it is given, and four wrong
 * routes framed under a clicked card's name is exactly the defect. With no
 * selection (a fresh answer drawn whole) every ok line shows, deliberately.
 */
export function drawableFeatures(
  entries: ReadonlyMap<string, LineEntry>,
  selectedId: string | null,
  bands?: ReadonlyMap<string, DifficultyBand>,
): GeoJSON.Feature[] | null {
  if (selectedId !== null && entries.get(selectedId)?.status !== 'ok') return null;
  const features: GeoJSON.Feature[] = [];
  for (const [id, entry] of entries) {
    if (entry.status !== 'ok') continue;
    features.push({
      ...entry.feature,
      properties: {
        ...(entry.feature.properties ?? {}),
        id,
        selected: id === selectedId,
        difficulty_band: bands?.get(id) ?? 'ungraded',
      },
    });
  }
  return features.length ? features : null;
}

/** The selected feature of a collection, if any — else a bare Feature (a
 *  trail, a saved route). An UNSELECTED collection is unfocused whatever its
 *  size: a 1-element fallback made the focus depend on how many siblings
 *  happened to load, which is a count, not a choice. */
function focusOf(
  geometry: GeoJSON.Feature | GeoJSON.FeatureCollection | GeoJSON.Geometry | null,
): GeoJSON.Feature | null {
  if (!geometry || !('type' in geometry)) return null;
  if (geometry.type === 'FeatureCollection') {
    return geometry.features.find((f) => f.properties?.selected) ?? null;
  }
  if (geometry.type === 'Feature') return geometry;
  return null;
}

/** The caveat the drawn line carries (a multi-piece route served as its
 *  longest piece says so in properties.note) — shown on the map, because a
 *  line that silently omits pieces is a line a walker plans around. */
export function noteOf(
  geometry: GeoJSON.Feature | GeoJSON.FeatureCollection | GeoJSON.Geometry | null,
): string | null {
  const note = focusOf(geometry)?.properties?.note;
  return typeof note === 'string' && note ? note : null;
}

/** The route id the map is focused on — surfaced as a data attribute so a
 *  browser test can assert the drawn line IS the clicked card's. */
export function focusedRouteId(
  geometry: GeoJSON.Feature | GeoJSON.FeatureCollection | GeoJSON.Geometry | null,
): string | null {
  const properties = focusOf(geometry)?.properties;
  const id = properties?.route_id ?? properties?.id;
  return typeof id === 'string' && id ? id : null;
}
