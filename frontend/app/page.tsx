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
  const [geometry, setGeometry] = useState<GeoJSON.Feature | GeoJSON.Geometry | null>(null);
  // undefined = session still resolving; render nothing rather than flashing
  // the sign-in form at an already signed-in user.
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [history, setHistory] = useState<ChatMessage[]>([]);

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
      return;
    }
    // Load before switching so the remounted panel starts with its history.
    const messages = await loadMessages(id).catch(() => []);
    setHistory(messages);
    setSelected(id);
  }

  function conversationCreated(id: string) {
    setSelected(id);
    // The backend just inserted the row; refresh the list so it appears.
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
          key={selected ?? 'new'}
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
