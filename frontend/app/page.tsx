'use client';

import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';

import { AuthPanel } from '@/components/AuthPanel';
import { ChatPanel } from '@/components/ChatPanel';
import { ConversationList } from '@/components/ConversationList';
import { onSession, signOut, type AuthUser } from '@/lib/auth';
import {
  listConversations,
  loadMessages,
  type ConversationSummary,
} from '@/lib/conversations';
import { isAuthConfigured } from '@/lib/supabaseClient';
import type { ChatMessage } from '@/lib/types';

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
  }, [user]);

  async function selectConversation(id: string | null) {
    setGeometry(null);
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
        {user && (
          <div className="session-bar">
            <span title={user.email ?? undefined}>{user.email}</span>
            <button type="button" className="link" onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        )}
        {user && conversations.length > 0 && (
          <ConversationList
            conversations={conversations}
            selected={selected}
            onSelect={(id) => void selectConversation(id)}
          />
        )}
        <ChatPanel
          key={panelKey}
          onGeometry={setGeometry}
          initialConversationId={selected}
          initialMessages={history}
          onConversationCreated={conversationCreated}
        />
      </div>
      <div className="map">
        <MapView geometry={geometry} />
        {!geometry && <p className="map-empty">Pick a trail to see it drawn here.</p>}
      </div>
    </main>
  );
}
