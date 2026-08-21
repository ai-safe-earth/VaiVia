'use client';

import { useEffect, useState } from 'react';

import {
  fetchFavorites,
  fetchRouteDetail,
  fetchRouteGeoJson,
  type FavoritesList,
} from '@/lib/api';
import type { Loop, RouteDetail } from '@/lib/types';

import { LoopCard } from './LoopCard';

interface Props {
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
export function FavoritesView({ onGeometry, onDetail, favorites, onToggleFavorite }: Props) {
  // undefined = loading, null = failed.
  const [list, setList] = useState<FavoritesList | null | undefined>(undefined);
  const [selected, setSelected] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, RouteDetail | null>>({});

  useEffect(() => {
    let cancelled = false;
    fetchFavorites()
      .then((fresh) => {
        if (!cancelled) setList(fresh);
      })
      .catch(() => {
        if (!cancelled) setList(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function select(loop: Loop) {
    setSelected(loop.id);
    onGeometry(await fetchRouteGeoJson(loop.id));
    if (loop.id in details) {
      onDetail?.(details[loop.id] ?? null);
      return;
    }
    try {
      const detail = await fetchRouteDetail(loop.id);
      setDetails((current) => ({ ...current, [loop.id]: detail }));
      onDetail?.(detail);
    } catch {
      setDetails((current) => ({ ...current, [loop.id]: null }));
    }
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
            detail={details[loop.id]}
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
