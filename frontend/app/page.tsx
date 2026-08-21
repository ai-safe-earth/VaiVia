'use client';

import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';

import { AppHeader } from '@/components/AppHeader';
import { AuthPanel } from '@/components/AuthPanel';
import { ChatPanel } from '@/components/ChatPanel';
import { ConversationList } from '@/components/ConversationList';
import { FavoritesView } from '@/components/FavoritesView';
import { ElevationPanel, MapLayerTabs } from '@/components/MapChrome';
import { fetchFavorites, setFavorite } from '@/lib/api';
import { onSession, signOut, type AuthUser } from '@/lib/auth';
import {
  listConversations,
  loadMessages,
  type ConversationSummary,
} from '@/lib/conversations';
import { profileFromDetail } from '@/lib/profile';
import { isAuthConfigured } from '@/lib/supabaseClient';
import type { ChatMessage, Loop, RouteDetail } from '@/lib/types';

// MapLibre touches window at import time, so it must not be server-rendered.
const MapView = dynamic(() => import('@/components/MapView').then((m) => m.MapView), {
  ssr: false,
});

/** Where the graph has been ingested. Named, because the honest answer to a
 *  request past the edge of coverage is where it stops. */
const REGION = 'Lecco · Bergamo';

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
  const [showFavorites, setShowFavorites] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [history, setHistory] = useState<ChatMessage[]>([]);
  // The remount key changes only on explicit navigation (picking a stored
  // conversation, "+ New chat"). It must NOT track `selected` directly: when a
  // fresh chat's first turn is assigned an id, `selected` updates so the list
  // highlights it — and keying on that would remount the panel mid-stream and
  // destroy the answer as it arrives.
  const [panelKey, setPanelKey] = useState('new-0');

  useEffect(() => onSession(setUser), []);

  useEffect(() => {
    if (!user) {
      setConversations([]);
      setSelected(null);
      setHistory([]);
      return;
    }
    void listConversations()
      .then(setConversations)
      .catch(() => setConversations([]));
    void fetchFavorites()
      .then((list) => setFavoriteIds(new Set(list.routes.map((r) => r.id))))
      .catch(() => {});
  }, [user]);

  /** Optimistic: the bookmark flips at once, and flips back if the save
   *  fails — a favorite that silently did not stick is worse than a flicker. */
  function toggleFavorite(loop: Loop, on: boolean) {
    setFavoriteIds((current) => {
      const next = new Set(current);
      if (on) next.add(loop.id);
      else next.delete(loop.id);
      return next;
    });
    void setFavorite(loop.id, on).catch(() => {
      setFavoriteIds((current) => {
        const next = new Set(current);
        if (on) next.delete(loop.id);
        else next.add(loop.id);
        return next;
      });
    });
  }

  async function selectConversation(id: string | null) {
    setShowFavorites(false);
    setGeometry(null);
    setRouteDetail(null);
    if (id === null) {
      setSelected(null);
      setHistory([]);
      setPanelKey(`new-${Date.now()}`); // always a fresh panel, even new -> new
      return;
    }
    // Load before switching so the remounted panel starts with its history.
    const messages = await loadMessages(id).catch(() => []);
    setHistory(messages);
    setSelected(id);
    setPanelKey(id);
  }

  function conversationCreated(id: string) {
    // Highlight the new conversation, but leave panelKey alone — the panel
    // that created it is mid-stream and must not be remounted.
    setSelected(id);
    void listConversations()
      .then(setConversations)
      .catch(() => {});
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
          region={REGION}
          email={user?.email}
          onSignOut={user ? () => void signOut() : undefined}
          onFavorites={user ? () => setShowFavorites((open) => !open) : undefined}
          favoritesOpen={showFavorites}
        />
        {user && conversations.length > 0 && (
          <ConversationList
            conversations={conversations}
            selected={selected}
            onSelect={(id) => void selectConversation(id)}
          />
        )}
        {showFavorites ? (
          <FavoritesView
            onGeometry={setGeometry}
            onDetail={setRouteDetail}
            favorites={favoriteIds}
            onToggleFavorite={toggleFavorite}
          />
        ) : (
          <ChatPanel
            key={panelKey}
            onGeometry={setGeometry}
            onDetail={setRouteDetail}
            initialConversationId={selected}
            initialMessages={history}
            onConversationCreated={conversationCreated}
            favorites={user ? favoriteIds : undefined}
            onToggleFavorite={user ? toggleFavorite : undefined}
          />
        )}
        {/* Trail geometry, paths and POIs in every answer are OSM-derived, so
            the credit belongs in the app chrome and not only on the map — a
            user reading results never has to open the map to see it. */}
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
      {/* The canvas is its own row so the layer tabs and the elevation panel
          are chrome around it rather than overlays on top of it — and so the
          tile attribution keeps its place at the foot of the map itself. */}
      <div className="map">
        <MapLayerTabs />
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
      </div>
    </main>
  );
}
