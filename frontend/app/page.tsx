'use client';

import dynamic from 'next/dynamic';
import { useEffect, useRef, useState } from 'react';

import { AppHeader } from '@/components/AppHeader';
import { AuthPanel } from '@/components/AuthPanel';
import { ChatPanel } from '@/components/ChatPanel';
import { FavoritesView } from '@/components/FavoritesView';
import { ElevationPanel } from '@/components/MapChrome';
import { fetchFavorites, setFavorite, type FavoritesList } from '@/lib/api';
import { applyToggle, belongsToCurrentUser, savedIds } from '@/lib/favorites';
import { onSession, signOut, type AuthUser } from '@/lib/auth';
import { listConversations, loadMessages } from '@/lib/conversations';
import { profileFromDetail } from '@/lib/profile';
import { isAuthConfigured } from '@/lib/supabaseClient';
import type { ChatMessage, Loop, RouteDetail } from '@/lib/types';

// MapLibre touches window at import time, so it must not be server-rendered.
const MapView = dynamic(() => import('@/components/MapView').then((m) => m.MapView), {
  ssr: false,
});

export default function Home() {
  const [geometry, setGeometry] = useState<
    GeoJSON.Feature | GeoJSON.FeatureCollection | GeoJSON.Geometry | null
  >(null);
  // undefined = session still resolving; render nothing rather than flashing
  // the sign-in form at an already signed-in user.
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);
  // The selected route's document detail: the elevation panel draws its
  // profile. Null whenever nothing (or a trail) is selected.
  const [routeDetail, setRouteDetail] = useState<RouteDetail | null>(null);
  // Saved routes: the id set drives every card's bookmark; the view shows
  // the hydrated list. One state, however the toggle was reached.
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(new Set());
  // The hydrated list itself, kept rather than thrown away: the page fetched
  // it for the ids alone and the Saved-routes view fetched the identical list
  // again on every open. undefined = not loaded yet, null = the load failed.
  const [favoritesList, setFavoritesList] = useState<
    FavoritesList | null | undefined
  >(undefined);
  const [showFavorites, setShowFavorites] = useState(false);
  // ONE continuous conversation (owner decision, 2026-08-26): the app resumes
  // the most recent conversation on sign-in and history scrolls back. resumed
  // is set exactly once per sign-in, before the panel is shown, so the panel
  // key never changes mid-stream — a fresh chat's first turn assigning an id
  // does not remount the panel and destroy the answer as it arrives.
  // undefined = still resolving (render no panel yet), null = start fresh.
  const [resumed, setResumed] = useState<
    { id: string | null; messages: ChatMessage[] } | undefined
  >(undefined);
  // Bumped by the header's New chat button. Part of the panel key, so a fresh
  // chat is an explicit remount — the key must never change from an SSE event
  // (that was the mid-stream remount bug).
  const [epoch, setEpoch] = useState(0);

  // Who is signed in NOW, readable from a continuation that started under
  // whoever was signed in THEN.
  const userRef = useRef(user);
  userRef.current = user;

  useEffect(() => onSession(setUser), []);

  useEffect(() => {
    // Everything below belongs to ONE account. Clearing it up front, rather
    // than letting the next account's fetch overwrite it, is what stops a
    // second sign-in seeing the first one's saved routes -- for a moment if
    // the fetch succeeds, and indefinitely if it fails.
    setResumed(undefined);
    setEpoch(0);
    setFavoriteIds(new Set());
    setFavoritesList(undefined);
    if (!user) return;

    // ...and a response that arrives after the account changed again is not
    // this account's, so it is dropped rather than rendered.
    const forUser = user.id;
    const stillCurrent = () => belongsToCurrentUser(forUser, userRef.current?.id);
    // Resume the single conversation: newest id, its transcript, its cards
    // (loadMessages rehydrates them from the stored result_refs). Any failure
    // degrades to a fresh chat rather than blocking sign-in.
    void listConversations()
      .then(async (list) => {
        const id = list[0]?.id ?? null;
        const messages = id ? await loadMessages(id).catch(() => []) : [];
        if (stillCurrent()) setResumed({ id, messages });
      })
      .catch(() => stillCurrent() && setResumed({ id: null, messages: [] }));
    void fetchFavorites()
      .then((list) => stillCurrent() && receiveFavorites(list))
      .catch(() => stillCurrent() && setFavoritesList(null));
  }, [user]);

  /** One saved list, one id set, whoever loaded it.
   *
   *  `missing` counts as saved. Those ids ARE in the ledger — their route is
   *  just out of the catalogue until the next export restores it — and
   *  dropping them showed the bookmark unfilled on a route saved long ago:
   *  one tap "saved" it (a no-op) and the next tap deleted it.
   */
  function receiveFavorites(list: FavoritesList) {
    setFavoritesList(list);
    setFavoriteIds(savedIds(list));
  }

  /** Optimistic: the bookmark flips at once, and flips back if the save
   *  fails — a favorite that silently did not stick is worse than a flicker. */
  function toggleFavorite(loop: Loop, on: boolean) {
    setFavoriteIds((current) => applyToggle(current, loop.id, on));
    void setFavorite(loop.id, on).catch(() => {
      // The exact inverse flip, so a failed save leaves the set as it was.
      setFavoriteIds((current) => applyToggle(current, loop.id, !on));
    });
  }

  const authRequired = isAuthConfigured();
  if (authRequired && user === undefined) return null;
  if (authRequired && !user) {
    return (
      <main className="shell centered">
        <AuthPanel />
      </main>
    );
  }

  return (
    <main className="shell">
      <div className="chat-column">
        <AppHeader
          email={user?.email}
          onNewChat={() => {
            setShowFavorites(false);
            setEpoch((current) => current + 1);
          }}
          onSignOut={user ? () => void signOut() : undefined}
          onFavorites={user ? () => setShowFavorites((open) => !open) : undefined}
          favoritesOpen={showFavorites}
        />
        {showFavorites && (
          <FavoritesView
            initial={favoritesList}
            onLoaded={receiveFavorites}
            onGeometry={setGeometry}
            onDetail={setRouteDetail}
            favorites={favoriteIds}
            onToggleFavorite={toggleFavorite}
          />
        )}
        {/* The chat panel is HIDDEN behind the favorites view, never replaced.
            Rendering one or the other unmounted the panel and remounted it
            seeded from the resume snapshot — so a look at Saved routes wiped
            the transcript of the conversation you were having. */}
        {(!authRequired || resumed !== undefined) && (
          <ChatPanel
            key={`${user?.id ?? 'anon'}:${epoch > 0 ? `new-${epoch}` : (resumed?.id ?? 'fresh')}`}
            hidden={showFavorites}
            onGeometry={setGeometry}
            onDetail={setRouteDetail}
            initialConversationId={epoch > 0 ? null : (resumed?.id ?? null)}
            initialMessages={epoch > 0 ? [] : (resumed?.messages ?? [])}
            favorites={user ? favoriteIds : undefined}
            onToggleFavorite={user ? toggleFavorite : undefined}
          />
        )}
      </div>
      {/* The canvas is its own row so the elevation panel is chrome around
          it rather than an overlay on top of it — and so the tile attribution
          keeps its place at the foot of the map itself. */}
      <div className="map">
        <div className="map-canvas">
          <MapView geometry={geometry} />
          {!geometry && (
            <p className="map-empty vv-body-sm">Pick a trail to see it drawn here.</p>
          )}
        </div>
        <ElevationPanel
          profile={profileFromDetail(routeDetail)}
          quality={routeDetail?.profile_quality}
        />
        {/* Trail geometry, paths and POIs in every answer are OSM-derived;
            the credit sits under the profile line, beside the map it
            attributes (owner decision 2026-08-27). */}
        <footer className="data-credit vv-body-sm">
          Trails, paths and places from{' '}
          <a
            href="https://www.openstreetmap.org/copyright"
            target="_blank"
            rel="noreferrer"
          >
            © OpenStreetMap
          </a>{' '}
          contributors, under{' '}
          <a
            href="https://opendatacommons.org/licenses/odbl/"
            target="_blank"
            rel="noreferrer"
          >
            ODbL
          </a>
          . Conditions change — check locally before you go.
        </footer>
      </div>
    </main>
  );
}
