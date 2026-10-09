'use client';

import dynamic from 'next/dynamic';

import type { LineEntry } from '@/lib/mapTurn';

// MapLibre touches window at import time, so it must not be server-rendered.
const MapView = dynamic(() => import('./MapView').then((m) => m.MapView), {
  ssr: false,
});

/** An open card's own map: this route's line alone, marked selected so it
 *  draws at full width. The line's status is said on the card, so a map with
 *  no line is simply the basemap, never a sibling's line. */
export function CardMap({ routeId, line }: { routeId: string; line?: LineEntry }) {
  const geometry =
    line?.status === 'ok'
      ? {
          ...line.feature,
          properties: { ...(line.feature.properties ?? {}), id: routeId, selected: true },
        }
      : null;
  return (
    <div className="card-map">
      <MapView geometry={geometry} />
      {line === undefined && <p className="map-empty vv-body-sm">Loading the map line…</p>}
    </div>
  );
}
