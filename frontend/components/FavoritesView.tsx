'use client';

import { useEffect, useRef, useState } from 'react';

import { fetchFavorites, fetchRouteGeoJson, type FavoritesList } from '@/lib/api';
import type { Loop, RouteDetail } from '@/lib/types';
import { useRouteDetails } from '@/lib/useRouteDetails';

import { LoopCard } from './LoopCard';

interface Props {
  /** What the page already fetched, so opening this view shows the list at
   *  once instead of loading the identical rows a second time. */
  initial?: FavoritesList | null;
  /** A revalidated list, handed back so the page's id set follows it. */
  onLoaded?: (list: FavoritesList) => void;
  onGeometry: (geometry: GeoJSON.Feature | null) => void;
  onDetail?: (detail: RouteDetail | null) => void;
  /** The page-level saved set, so a toggle here and a toggle on a chat card
   *  are the same state. */
  favorites: Set<string>;
  onToggleFavorite: (loop: Loop, on: boolean) => void;
}

/**
 * The saved-routes view: the chat column showing the favorites list instead
 * of a transcript. The rows come hydrated from the backend in one round trip
 * (the same shape a search returns), so these are the same cards — expand,
 * profile, map focus and all. An id whose route left the catalogue is named,
 * never silently dropped: the catalogue is replaced wholesale per export and
 * only the geometry-derived id persists.
 */
export function FavoritesView({
  initial,
  onLoaded,
  onGeometry,
  onDetail,
  favorites,
  onToggleFavorite,
}: Props) {
  // undefined = loading, null = failed.
  const [list, setList] = useState<FavoritesList | null | undefined>(initial);
  const [selected, setSelected] = useState<string | null>(null);
  // The same detail loading the chat cards use, guards included.
  const routeDetails = useRouteDetails(onDetail);
  // Geometry, kept per route: clicking between saved cards re-drew the map by
  // re-fetching the same GeoJSON every time.
  const geometries = useRef<Map<string, GeoJSON.Feature | null>>(new Map());

  useEffect(() => {
    let cancelled = false;
    // Still revalidated on open — the saved list can have changed elsewhere —
    // but the rows above are already on screen while that happens.
    fetchFavorites()
      .then((fresh) => {
        if (cancelled) return;
        setList(fresh);
        onLoaded?.(fresh);
      })
      .catch(() => {
        if (!cancelled) setList((current) => current ?? null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** Draw the route and load its profile.
   *
   *  Everything that can fail is awaited defensively: the geometry fetch sat
   *  outside the try once, so a 503 from the documents store threw past an
   *  un-caught `void select(...)` and left the expanded card saying
   *  "Fetching the altitude profile…" for ever.
   */
  async function select(loop: Loop) {
    setSelected(loop.id);
    routeDetails.select(loop.id);
    if (!geometries.current.has(loop.id)) {
      geometries.current.set(
        loop.id,
        await fetchRouteGeoJson(loop.id).catch(() => null),
      );
    }
    // Late geometry for a card the user has already moved off must not
    // repaint the map under the one they are looking at now.
    if (routeDetails.current() !== loop.id) return;
    onGeometry(geometries.current.get(loop.id) ?? null);
    await routeDetails.load(loop.id);
  }

  // Unsaving from this view keeps the card until the list is reopened — an
  // accidental tap stays undoable — but the toggle state follows the set.
  const routes = list?.routes ?? [];

  return (
    <section className="chat favorites-view" aria-label="Saved routes">
      <div className="messages">
        <div className="turn turn-assistant">
          <span className="turn-label vv-label">Saved routes</span>
          <p className="vv-body">
            {list === undefined
              ? 'Loading…'
              : list === null
                ? 'Could not load your saved routes. Is the gateway running?'
                : routes.length === 0
                  ? 'Nothing saved yet — the bookmark on any route card saves it here.'
                  : `${routes.length} saved.`}
          </p>
        </div>

        {routes.map((loop) => (
          <LoopCard
            key={loop.id}
            loop={loop}
            selected={selected === loop.id}
            onSelect={(picked) => void select(picked)}
            onExpand={(picked) => void select(picked)}
            detail={routeDetails.details[loop.id]}
            favorited={favorites.has(loop.id)}
            onToggleFavorite={onToggleFavorite}
          />
        ))}

        {list && list.missing.length > 0 && (
          <div className="turn turn-assistant">
            <p className="vv-body-sm">
              {list.missing.length === 1
                ? 'One saved route is'
                : `${list.missing.length} saved routes are`}{' '}
              no longer in the catalogue — kept here in case a rebuild brings
              {list.missing.length === 1 ? ' it' : ' them'} back.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
