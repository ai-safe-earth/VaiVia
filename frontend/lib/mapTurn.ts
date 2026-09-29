/**
 * A card's map line and the rules for trusting it. Each open card draws its
 * own route alone (owner decision 2026-09-29), so there is no shared drawn set
 * left to guard — only the line itself, which must be this route's or nothing.
 */

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

/**
 * A DRAWN route ships its line inline on the card — it is in no catalogue,
 * so there is nothing to fetch. Null for catalogue cards, which fetch.
 */
export function inlineLine(loop: {
  id: string;
  geometry?: GeoJSON.LineString;
}): LineEntry | null {
  if (!loop.geometry) return null;
  return {
    status: 'ok',
    feature: {
      type: 'Feature',
      id: loop.id,
      geometry: loop.geometry,
      properties: { route_id: loop.id },
    },
  };
}

/** Should this entry be fetched (again)? Errors retry; ok and missing hold. */
export function needsFetch(entry: LineEntry | undefined): boolean {
  return entry === undefined || entry.status === 'error';
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
