'use client';

import dynamic from 'next/dynamic';
import { useEffect, useRef, useState } from 'react';

import { AppHeader } from '@/components/AppHeader';
import { AuthPanel } from '@/components/AuthPanel';
import { ChatPanel } from '@/components/ChatPanel';
import { FavoritesView } from '@/components/FavoritesView';
import { LoopCard } from '@/components/LoopCard';
import { TrailCard } from '@/components/TrailCard';
import { fetchFavorites, setFavorite, type FavoritesList } from '@/lib/api';
import { applyToggle, belongsToCurrentUser, savedIds } from '@/lib/favorites';
import { onSession, signOut, type AuthUser } from '@/lib/auth';
import { listConversations, loadMessages } from '@/lib/conversations';
import { focusedRouteId } from '@/lib/mapTurn';
import { isAuthConfigured } from '@/lib/supabaseClient';
import type {
  ChatMessage,
  FeedbackTurn,
  Loop,
  RouteDetail,
  Trail,
} from '@/lib/types';

// MapLibre touches window at import time, so it must not be server-rendered.
const MapView = dynamic(() => import('@/components/MapView').then((m) => m.MapView), {
  ssr: false,
});

/** A picked loop and a picked trail take different cards; they share no field
 *  that names which is which, so the shape decides. */
function isLoop(picked: Loop | Trail): picked is Loop {
  return 'distance_m' in picked;
}

export default function Home() {
  const [geometry, setGeometry] = useState<
    GeoJSON.Feature | GeoJSON.FeatureCollection | GeoJSON.Geometry | null
  >(null);
  // undefined = session still resolving; render nothing rather than flashing
  // the sign-in form at an already signed-in user.
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);
  // The picked route's document detail, drawn as the profile in the route
  // panel. Tri-state: undefined = still fetching (the panel says so), null =
  // asked and there is none, a value = draw it. A pick resets it to undefined
  // so the panel never shows the PREVIOUS route's profile under this one's
  // name, nor claims "no altitude profile" before the fetch has answered.
  const [routeDetail, setRouteDetail] = useState<RouteDetail | null | undefined>(
    undefined,
  );
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
  // Which layer is on top (owner decision 2026-08-28): 'chat' is the
  // conversation, 'map' slides the map and the picked route's data over it.
  // The composer is live in both — it is the bottom edge of the app, not part
  // of either layer.
  const [view, setView] = useState<'chat' | 'map'>('chat');
  // What the map layer is ABOUT: the card a tap sent up. Null with the layer
  // open is legitimate — the header toggle shows whatever is drawn, and a
  // fresh answer empties the panel without closing the layer.
  const [picked, setPicked] = useState<Loop | Trail | null>(null);
  // The turn that offered the picked route, so the panel card can ask whether
  // it was right. Null from the saved-routes view: a saved route answers no
  // question, so it is not eval material.
  const [pickedTurn, setPickedTurn] = useState<FeedbackTurn | null>(null);
  // ONE continuous conversation (owner decision, 2026-08-26): the app resumes
  // the most recent conversation on sign-in and history scrolls back. resumed
  // is set exactly once per sign-in, before the panel is shown, so the panel
  // key never changes mid-stream — a fresh chat's first turn assigning an id
  // does not remount the panel and destroy the answer as it arrives.
  // undefined = still resolving (render no panel yet), null = start fresh.
  const [resumed, setResumed] = useState<
    { id: string | null; messages: ChatMessage[] } | undefined
  >(undefined);
  // Bumped by the composer's Clear chat button. Part of the panel key, so a
  // fresh chat is an explicit remount — the key must never change from an SSE
  // event (that was the mid-stream remount bug).
  const [epoch, setEpoch] = useState(0);

  // Who is signed in NOW, readable from a continuation that started under
  // whoever was signed in THEN.
  const userRef = useRef(user);
  userRef.current = user;

  useEffect(() => onSession(setUser), []);

  // The open map is a history entry, so the browser's Back — and Android's
  // back gesture — return to the conversation instead of leaving the app.
  // Nothing else in the app uses the hash, so a hashchange IS the map opening
  // or closing.
  useEffect(() => {
    const sync = () => setView(window.location.hash === '#map' ? 'map' : 'chat');
    sync();
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);

  useEffect(() => {
    if (view !== 'map') return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeMap();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [view]);

  useEffect(() => {
    // Everything below belongs to ONE account. Clearing it up front, rather
    // than letting the next account's fetch overwrite it, is what stops a
    // second sign-in seeing the first one's saved routes -- for a moment if
    // the fetch succeeds, and indefinitely if it fails.
    setResumed(undefined);
    setEpoch(0);
    setFavoriteIds(new Set());
    setFavoritesList(undefined);
    // Home stays mounted across a sign-out, so the map must not still be up,
    // holding the previous account's route, when the next one signs in.
    setPicked(null);
    closeMap();
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  /** Raise the map over the conversation, as a history entry. */
  function openMap() {
    if (window.location.hash !== '#map') window.location.hash = 'map';
    setView('map');
  }

  /** Back to the conversation. replaceState rather than history.back(): the
   *  entry we would pop may not be the top one — a Back press is one of the
   *  ways the map closes — and one dead entry is cheaper than the
   *  bookkeeping that would avoid it. */
  function closeMap() {
    if (typeof window !== 'undefined' && window.location.hash === '#map') {
      window.history.replaceState(
        null,
        '',
        window.location.pathname + window.location.search,
      );
    }
    setView('chat');
  }

  /** A card tap: the map comes up with that route's data under it. A null
   *  pick — a fresh answer taking the map over — empties the panel and leaves
   *  the layer where it is, because the new answer's lines are worth seeing. */
  function pick(route: Loop | Trail | null, turn?: FeedbackTurn) {
    setPicked(route);
    setPickedTurn(turn ?? null);
    // The detail belongs to the route just picked, not the one before it.
    setRouteDetail(undefined);
    if (route) openMap();
  }

  /** Saved routes takes over the transcript, so it also takes over the pick:
   *  the map would otherwise still be about a card that is no longer on
   *  screen. */
  function toggleFavoritesView() {
    setShowFavorites((open) => !open);
    setPicked(null);
    closeMap();
  }

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

  // What the canvas says when there is nothing on it. The pick is checked
  // first: a picked route whose line never arrived must say THAT, not
  // "nothing drawn yet" — the layer is open because the user asked for this
  // route, and a blank canvas alone reads as a broken map.
  const emptyNote =
    picked && focusedRouteId(geometry) !== picked.id
      ? 'No map line for this route.'
      : geometry
        ? null
        : 'Nothing drawn yet — pick a route.';

  return (
    <main className="shell" data-view={view}>
      <AppHeader
        email={user?.email}
        onSignOut={user ? () => void signOut() : undefined}
        onFavorites={user ? toggleFavoritesView : undefined}
        favoritesOpen={showFavorites}
        onMap={() => (view === 'map' ? closeMap() : openMap())}
        mapOpen={view === 'map'}
      />

      {/* The stage: the conversation is the base layer and the map slides over
          it. Neither is ever unmounted — unmounting the chat throws away the
          transcript of the conversation you are having, and unmounting the map
          costs its camera and a fresh tile fetch on every open. */}
      <div className="stage">
        {showFavorites && (
          <FavoritesView
            initial={favoritesList}
            onLoaded={receiveFavorites}
            onGeometry={setGeometry}
            onDetail={setRouteDetail}
            onPick={pick}
            covered={view === 'map'}
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
            covered={view === 'map'}
            onGeometry={setGeometry}
            onDetail={setRouteDetail}
            onPick={pick}
            // Asking is done on the conversation: a submit brings the map
            // down so the count-first answer, a clarify and its suggestions
            // are read where they were asked for. The header tap puts the map
            // back over the new lines.
            onAsk={() => {
              setShowFavorites(false);
              closeMap();
            }}
            initialConversationId={epoch > 0 ? null : (resumed?.id ?? null)}
            initialMessages={epoch > 0 ? [] : (resumed?.messages ?? [])}
            onClear={() => {
              // The drawn route and its profile belong to the conversation
              // being discarded — a cleared chat clears its map.
              setGeometry(null);
              setPicked(null);
              setRouteDetail(undefined);
              closeMap();
              setEpoch((current) => current + 1);
            }}
            favorites={user ? favoriteIds : undefined}
            onToggleFavorite={user ? toggleFavorite : undefined}
          />
        )}

        {/* The map layer. Its own row for the canvas so the tile attribution
            keeps its place at the foot of the map itself, and the route panel
            sits BELOW that credit rather than over it. */}
        <div className="map-layer">
          <div className="map-canvas">
            <MapView geometry={geometry} />
            {emptyNote && <p className="map-empty vv-body-sm">{emptyNote}</p>}
            {/* The way back, in words, on the layer being dismissed (owner
                decision 2026-09-08, overturning "one affordance, one place"):
                the header's zigzag is where the map is OPENED, and it was not
                where anyone looked for the way out. Escape and Back still
                work; this is the one you can see. */}
            <button type="button" className="map-back" onClick={closeMap}>
              Back to list
            </button>
          </div>
          {picked && (
            <section className="route-panel" aria-label="Picked route">
              {isLoop(picked) ? (
                <LoopCard
                  key={picked.id}
                  loop={picked}
                  selected
                  // Open on arrival: the tap that raised this panel was the
                  // ask for the route's numbers, so making them a second tap
                  // away would be asking twice.
                  defaultOpen
                  detail={routeDetail}
                  favorited={favoriteIds.has(picked.id)}
                  onToggleFavorite={user ? toggleFavorite : undefined}
                  messageId={pickedTurn?.messageId}
                  conversationId={pickedTurn?.conversationId}
                />
              ) : (
                <TrailCard key={picked.id} trail={picked} selected />
              )}
            </section>
          )}
        </div>
      </div>

      {/* Trail geometry, paths and POIs in every answer are OSM-derived, so the
          credit is a row of the app itself — visible with the map up and with
          the map down, never behind a toggle (BRAND-SPEC §12). */}
      <footer className="data-credit vv-body-sm">
        <a
          href="https://www.openstreetmap.org/copyright"
          target="_blank"
          rel="noreferrer"
        >
          © OpenStreetMap
        </a>{' '}
        contributors ·{' '}
        <a
          href="https://opendatacommons.org/licenses/odbl/"
          target="_blank"
          rel="noreferrer"
        >
          ODbL
        </a>{' '}
        — check conditions locally.
      </footer>
    </main>
  );
}
