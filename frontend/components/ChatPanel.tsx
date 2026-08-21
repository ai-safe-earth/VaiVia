'use client';

import { useEffect, useRef, useState } from 'react';

import {
  AuthRequiredError,
  fetchRouteDetail,
  fetchRouteGeoJson,
  fetchTrailGeoJson,
  sendChat,
} from '@/lib/api';
import { isAuthConfigured } from '@/lib/supabaseClient';
import type { ChatMessage, Loop, RouteDetail, Trail } from '@/lib/types';

import { FoldedCards } from './FoldedCards';
import { LoopCard } from './LoopCard';
import { QueryReading } from './QueryReading';
import { TrailCard } from './TrailCard';

const SUGGESTIONS = [
  'easy walk with my kids near a lake',
  'a two hour mountain bike ride',
  'hike with a hut at the halfway point',
];

/** Where the card list folds when the results event carries no
 *  answered_count (older stored turns). */
const DEFAULT_FOLD = 5;

interface Props {
  onGeometry: (
    geometry: GeoJSON.Feature | GeoJSON.FeatureCollection | GeoJSON.Geometry | null,
  ) => void;
  /** The selected route's document detail (or null), so the map column's
   *  elevation panel can draw the profile of whatever the chat selected. */
  onDetail?: (detail: RouteDetail | null) => void;
  /** Resume this stored conversation; null starts fresh. The page remounts the
   *  panel (via key) on explicit navigation, so state never leaks across
   *  switches — but not when this panel's own first turn is assigned an id. */
  initialConversationId?: string | null;
  initialMessages?: ChatMessage[];
  /** Fired when the backend assigns an id to a brand-new conversation. */
  onConversationCreated?: (id: string) => void;
  /** Saved-route ids + toggle, owned by the page so a bookmark here and one
   *  in the favorites view are the same state. Absent when signed out. */
  favorites?: Set<string>;
  onToggleFavorite?: (loop: Loop, on: boolean) => void;
}

export function ChatPanel({
  onGeometry,
  onDetail,
  initialConversationId = null,
  initialMessages = [],
  onConversationCreated,
  favorites,
  onToggleFavorite,
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
  // Route documents' detail (profile, measures), fetched once per route on
  // first expand or selection. undefined = never asked, null = gone/failed.
  const [routeDetails, setRouteDetails] = useState<
    Record<string, RouteDetail | null>
  >({});
  const detailInFlight = useRef<Set<string>>(new Set());
  // Selection, readable from async continuations without a stale closure.
  const selectedRef = useRef<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function selectTrail(trail: Trail) {
    setSelectedTrail(trail.id);
    selectedRef.current = trail.id;
    // Trails carry no document detail; the elevation panel goes quiet rather
    // than showing the previous route's profile under a trail's name.
    onDetail?.(null);
    loopFeatures.current.clear();
    onGeometry(await fetchTrailGeoJson(trail.id));
  }

  /** Fetch a route's document detail once and hand it to the elevation
   *  panel. A failure records null — "no profile", never a spinner forever. */
  async function loadDetail(loop: Loop) {
    if (loop.id in routeDetails) {
      onDetail?.(routeDetails[loop.id] ?? null);
      return;
    }
    if (detailInFlight.current.has(loop.id)) return;
    detailInFlight.current.add(loop.id);
    try {
      const detail = await fetchRouteDetail(loop.id);
      setRouteDetails((current) => ({ ...current, [loop.id]: detail }));
      if (selectedRef.current === loop.id) onDetail?.(detail);
    } catch {
      setRouteDetails((current) => ({ ...current, [loop.id]: null }));
    } finally {
      detailInFlight.current.delete(loop.id);
    }
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
    selectedRef.current = loop.id;
    drawLoops(loop.id);
    void loadDetail(loop);
  }

  async function loadLoopGeometry(loops: Loop[]) {
    loopFeatures.current.clear();
    await appendLoopGeometry(loops);
  }

  /** Fetch geometry for these loops and merge it into the drawn set — used
   *  both for the visible fold of a fresh answer and for cards a "show more"
   *  just revealed. Settled, not all: one route missing its geometry must not
   *  stop the others being drawn. */
  async function appendLoopGeometry(loops: Loop[]) {
    const missing = loops.filter((loop) => !loopFeatures.current.has(loop.id));
    const results = await Promise.allSettled(
      missing.map((loop) => fetchRouteGeoJson(loop.id)),
    );
    results.forEach((result, index) => {
      if (result.status === 'fulfilled' && result.value) {
        loopFeatures.current.set(missing[index].id, result.value);
      }
    });
    drawLoops(selectedRef.current);
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
            // answer model is not handed thousands of coordinates. Only the
            // visible fold is fetched; "show more" fetches what it reveals.
            if (event.results.loops?.length) {
              const fold = event.results.answered_count ?? DEFAULT_FOLD;
              void loadLoopGeometry(event.results.loops.slice(0, fold));
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
      {!isAuthConfigured() && (
        <div className="notice">
          <div className="notice-bar" />
          <div className="notice-body">
            <span className="vv-label vv-label-hazard">Not configured</span>
            <p className="vv-body">
              Supabase is not configured, so the gateway will reject requests. Set
              NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to sign in.
            </p>
          </div>
        </div>
      )}

      <div className="messages">
        {messages.length === 0 && (
          <>
            <div className="turn turn-assistant">
              <p className="vv-body">Ask for a trail the way you would ask a local.</p>
            </div>
            <div className="suggestions">
              {SUGGESTIONS.map((suggestion) => (
                <button key={suggestion} type="button" onClick={() => void submit(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
          </>
        )}

        {/* The transcript is a ruled document: full-width turns separated by
            hairlines, the user's quoted on --vv-panel. No bubbles. */}
        {messages.map((message, index) => (
          <div key={index}>
            <div className={`turn turn-${message.role}`}>
              {message.role === 'user' && (
                <span className="turn-label vv-label">You asked</span>
              )}
              <p className="vv-body">
                {message.content || (message.streaming ? '…' : '')}
              </p>
            </div>

            {/* Inactive until the backend returns the composed plan — see
                QueryReading. It sits between the answer and the routes because
                that is where a correction would be made. */}
            {message.role === 'assistant' && message.results && !message.streaming && (
              <QueryReading reading={message.results.reading} />
            )}

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
            {message.results?.loops && message.results.loops.length > 0 && (
              <FoldedCards
                count={message.results.loops.length}
                fold={message.results.answered_count ?? DEFAULT_FOLD}
                onReveal={(from, to) =>
                  void appendLoopGeometry(
                    (message.results?.loops ?? []).slice(from, to),
                  )
                }
              >
                {message.results.loops.map((loop) => (
                  <LoopCard
                    key={loop.id}
                    loop={loop}
                    selected={selectedTrail === loop.id}
                    onSelect={selectLoop}
                    onExpand={selectLoop}
                    detail={routeDetails[loop.id]}
                    favorited={favorites?.has(loop.id) ?? false}
                    onToggleFavorite={onToggleFavorite}
                  />
                ))}
              </FoldedCards>
            )}

            {message.results?.trails && message.results.trails.length > 0 && (
              <FoldedCards
                count={message.results.trails.length}
                fold={message.results.answered_count ?? DEFAULT_FOLD}
              >
                {message.results.trails.map((trail) => (
                  <TrailCard
                    key={trail.id}
                    trail={trail}
                    selected={selectedTrail === trail.id}
                    onSelect={(selected) => void selectTrail(selected)}
                  />
                ))}
              </FoldedCards>
            )}

            {message.error && (
              <div className="notice">
                <div className="notice-bar" />
                <div className="notice-body">
                  <span className="vv-label vv-label-hazard">No answer</span>
                  <p className="vv-body">{message.error}</p>
                </div>
              </div>
            )}
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
          placeholder="Type what you want to do"
          aria-label="Your message"
          disabled={busy}
        />
        <button className="ask" type="submit" disabled={busy || !input.trim()}>
          {busy ? '…' : 'Ask'}
        </button>
      </form>
    </section>
  );
}
