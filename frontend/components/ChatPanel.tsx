'use client';

import { useEffect, useRef, useState } from 'react';

import {
  AuthRequiredError,
  fetchRouteGeoJson,
  fetchTrailGeoJson,
  sendChat,
} from '@/lib/api';
import { isAuthConfigured } from '@/lib/supabaseClient';
import type { ChatMessage, Loop, Trail } from '@/lib/types';

import { LoopCard } from './LoopCard';
import { TrailCard } from './TrailCard';

const SUGGESTIONS = [
  'easy walk with my kids near a lake',
  'a two hour mountain bike ride',
  'hike with a hut at the halfway point',
];

interface Props {
  onGeometry: (
    geometry: GeoJSON.Feature | GeoJSON.FeatureCollection | GeoJSON.Geometry | null,
  ) => void;
  /** Resume this stored conversation; null starts fresh. The page remounts the
   *  panel (via key) on explicit navigation, so state never leaks across
   *  switches — but not when this panel's own first turn is assigned an id. */
  initialConversationId?: string | null;
  initialMessages?: ChatMessage[];
  /** Fired when the backend assigns an id to a brand-new conversation. */
  onConversationCreated?: (id: string) => void;
}

export function ChatPanel({
  onGeometry,
  initialConversationId = null,
  initialMessages = [],
  onConversationCreated,
}: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(
    initialConversationId,
  );
  // One selection slot shared with trails: route ids cannot collide with
  // trail ids, and picking a loop should clear a trail anyway.
  const [selectedTrail, setSelectedTrail] = useState<string | null>(null);
  // Loop geometries are fetched once per results event and kept, so
  // clicking between loops restyles what is already drawn instead of
  // refetching and making the map flicker.
  const loopFeatures = useRef<Map<string, GeoJSON.Feature>>(new Map());
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function selectTrail(trail: Trail) {
    setSelectedTrail(trail.id);
    loopFeatures.current.clear();
    onGeometry(await fetchTrailGeoJson(trail.id));
  }

  /** Every loop drawn at once, with `selected` marking the one to highlight.
   *  MapView styles and fits on that property, so switching selection is a
   *  restyle rather than a refetch. */
  function drawLoops(selectedId: string | null) {
    const features = [...loopFeatures.current.entries()].map(([id, feature]) => ({
      ...feature,
      properties: { ...(feature.properties ?? {}), id, selected: id === selectedId },
    }));
    if (features.length === 0) return;
    onGeometry({ type: 'FeatureCollection', features });
  }

  function selectLoop(loop: Loop) {
    setSelectedTrail(loop.id);
    drawLoops(loop.id);
  }

  async function loadLoopGeometry(loops: Loop[]) {
    loopFeatures.current.clear();
    // Settled, not all: one route missing its geometry must not stop the
    // others being drawn.
    const results = await Promise.allSettled(
      loops.map((loop) => fetchRouteGeoJson(loop.id)),
    );
    results.forEach((result, index) => {
      if (result.status === 'fulfilled' && result.value) {
        loopFeatures.current.set(loops[index].id, result.value);
      }
    });
    drawLoops(null);
  }

  async function submit(text: string) {
    const message = text.trim();
    if (!message || busy) return;

    setInput('');
    setBusy(true);
    setMessages((current) => [
      ...current,
      { role: 'user', content: message },
      { role: 'assistant', content: '', streaming: true },
    ]);

    // Mutate only the last message as tokens arrive, so earlier turns are never
    // re-rendered mid-stream.
    const updateLast = (patch: Partial<ChatMessage>) =>
      setMessages((current) => {
        const next = [...current];
        next[next.length - 1] = { ...next[next.length - 1]!, ...patch };
        return next;
      });

    let streamed = '';
    try {
      for await (const event of sendChat(message, conversationId)) {
        switch (event.type) {
          case 'conversation':
            if (conversationId === null) onConversationCreated?.(event.conversationId);
            setConversationId(event.conversationId);
            break;
          case 'results': {
            updateLast({ results: event.results });
            // Loops arrive without geometry — it is fetched per route so the
            // answer model is not handed thousands of coordinates. Draw them
            // all; clicking a card highlights and zooms to one.
            if (event.results.loops?.length) {
              void loadLoopGeometry(event.results.loops);
              break;
            }
            // A composed plan can resolve several routes; draw them all.
            const lines = (event.results.routes ?? [])
              .map((block) => block.geometry)
              .filter((g): g is GeoJSON.LineString => Boolean(g));
            if (lines.length > 1) {
              onGeometry({
                type: 'FeatureCollection',
                features: lines.map((geometry) => ({
                  type: 'Feature',
                  properties: {},
                  geometry,
                })),
              });
            } else if (event.results.geometry) {
              onGeometry(event.results.geometry);
            }
            break;
          }
          case 'token':
            streamed += event.delta;
            updateLast({ content: streamed });
            break;
          case 'error':
            updateLast({ error: event.message, streaming: false });
            break;
          case 'done':
            updateLast({ streaming: false });
            break;
        }
      }
    } catch (error) {
      updateLast({
        streaming: false,
        error:
          error instanceof AuthRequiredError
            ? error.message
            : 'Could not reach the service. Is the gateway running?',
      });
    } finally {
      updateLast({ streaming: false });
      setBusy(false);
    }
  }

  return (
    <section className="chat">
      <header>
        <h1>VaiVia</h1>
        <span>Lake Como · Lecco</span>
      </header>

      {!isAuthConfigured() && (
        <p className="banner">
          Supabase is not configured, so requests will be rejected by the gateway. Set
          NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to sign in.
        </p>
      )}

      <div className="messages">
        {messages.length === 0 && (
          <div className="bubble assistant">
            Ask for a trail the way you would ask a local.
            <div className="suggestions">
              {SUGGESTIONS.map((suggestion) => (
                <button key={suggestion} type="button" onClick={() => void submit(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message, index) => (
          <div key={index} className="cards">
            <div className={`bubble ${message.role}`}>
              {message.content || (message.streaming ? '…' : '')}
            </div>

            {message.results?.suggestions && message.results.suggestions.length > 0 && (
              <div className="suggestions">
                {message.results.suggestions.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    disabled={busy}
                    onClick={() => void submit(suggestion)}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}

            {/* Rendered on presence, not on `kind`: a loops+theme turn is
                still labelled trail_search, so keying off kind would hide
                them. */}
            {message.results?.loops?.map((loop) => (
              <LoopCard
                key={loop.id}
                loop={loop}
                selected={selectedTrail === loop.id}
                onSelect={selectLoop}
              />
            ))}

            {message.results?.trails?.map((trail) => (
              <TrailCard
                key={trail.id}
                trail={trail}
                selected={selectedTrail === trail.id}
                onSelect={(selected) => void selectTrail(selected)}
              />
            ))}

            {message.error && <div className="bubble error">{message.error}</div>}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          void submit(input);
        }}
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Find me a trail…"
          aria-label="Your message"
          disabled={busy}
        />
        <button className="primary" type="submit" disabled={busy || !input.trim()}>
          {busy ? '…' : 'Ask'}
        </button>
      </form>
    </section>
  );
}
