'use client';

import { useEffect, useRef, useState } from 'react';

import {
  AuthRequiredError,
  fetchRouteGeoJson,
  fetchTrailGeoJson,
  sendChat,
} from '@/lib/api';
import {
  drawableFeatures,
  isStillSelected,
  mayDraw,
  needsFetch,
  planReveal,
  planSelect,
  recordLine,
  type LineEntry,
  type LineStatus,
} from '@/lib/mapTurn';
import { isAuthConfigured } from '@/lib/supabaseClient';
import type { ChatMessage, Loop, RouteDetail, Trail } from '@/lib/types';
import { useRouteDetails } from '@/lib/useRouteDetails';

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
  /** Out of sight, still mounted. The Saved-routes view takes over the column
   *  but must not DESTROY the conversation underneath it: this panel owns the
   *  visible transcript, and unmounting it threw the transcript away and
   *  remounted from a `history` prop that only stored-conversation navigation
   *  ever writes. Hidden, not unmounted, so closing Saved routes returns to
   *  the answer you were reading — mid-stream turns included. */
  hidden?: boolean;
}

export function ChatPanel({
  onGeometry,
  onDetail,
  initialConversationId = null,
  initialMessages = [],
  onConversationCreated,
  favorites,
  onToggleFavorite,
  hidden = false,
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
  // refetching and making the map flicker. Each entry keeps its fetch
  // OUTCOME too: a failed line must read as "unavailable" on the card, not
  // as an id silently absent from the map.
  const loopFeatures = useRef<Map<string, LineEntry>>(new Map());
  // The same statuses as React state, so the cards re-render when a line
  // arrives or fails - the ref alone repaints nothing.
  const [lineStatus, setLineStatus] = useState<Record<string, LineStatus>>({});
  // Which ANSWER those features belong to. The drawn set is one answer's, and
  // "show more" under an older one used to merge its routes into the current
  // answer's map — a picture belonging to neither turn.
  const drawnTurn = useRef<number | null>(null);
  // How far each answer's fold has been revealed. FoldedCards keeps the
  // count as its own state; this mirror is what lets a click-takeover
  // restore every card the user can SEE, not just the fold — without it,
  // cards revealed before another answer took the map over came back
  // line-less, and no later "show more" could heal them.
  const revealed = useRef<Map<number, number>>(new Map());
  // Route lines currently being fetched, so a click during the initial
  // batch does not dispatch the same GET twice.
  const linesInFlight = useRef<Set<string>>(new Set());
  // Route documents' detail (profile, measures), fetched once per route on
  // first expand or selection — asked once, null on failure, and only painted
  // if its card is still the selected one. Shared with the saved-routes view,
  // which is where those three rules were dropped when they were copied.
  // Hidden means the Saved-routes view owns the column AND the map. A stream
  // that finishes back here must not repaint under it, so every emission goes
  // through these -- and through a REF, because a continuation that started
  // while visible would otherwise close over the old value.
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  const emitGeometry: Props['onGeometry'] = (geometry) => {
    if (!hiddenRef.current) onGeometry(geometry);
  };
  const emitDetail = (detail: RouteDetail | null) => {
    if (!hiddenRef.current) onDetail?.(detail);
  };

  const routes = useRouteDetails(emitDetail);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    // Back in view: the map is this panel's again, so redraw what this answer
    // had on it. Nothing was emitted while hidden, so without this the
    // favorites view's last route would stay under the transcript.
    if (!hidden && loopFeatures.current.size) drawLoops(routes.current());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hidden]);

  async function selectTrail(trail: Trail) {
    setSelectedTrail(trail.id);
    routes.select(trail.id);
    // Trails carry no document detail; the elevation panel goes quiet rather
    // than showing the previous route's profile under a trail's name.
    emitDetail(null);
    loopFeatures.current.clear();
    setLineStatus({});
    // No answer owns the map now. Leaving the old turn here made a later
    // same-turn "show more" append into a cache this clear just emptied -
    // a partial answer drawn as if it were whole.
    drawnTurn.current = null;
    // A trail whose geometry will not come (gone, or the session expired) is
    // a blank map, not an unhandled rejection out of a void-ed handler.
    const geometry = await fetchTrailGeoJson(trail.id).catch(() => null);
    // A slow trail A must not repaint the map after trail B was picked.
    if (!isStillSelected(routes.current(), trail.id)) return;
    emitGeometry(geometry);
  }

  /** Every loop drawn at once, with `selected` marking the one to highlight.
   *  MapView styles and fits on that property, so switching selection is a
   *  restyle rather than a refetch. The rule for WHAT may draw - including
   *  "a selection whose line is unavailable clears the map rather than
   *  framing its siblings" - lives in lib/mapTurn.ts, tested. */
  function drawLoops(selectedId: string | null) {
    const features = drawableFeatures(loopFeatures.current, selectedId);
    emitGeometry(features ? { type: 'FeatureCollection', features } : null);
  }

  /** A card click. The one entry point that had no turn guard: older
   *  answers' cards stay clickable in the transcript, and clicking one drew
   *  whatever answer owned the cache. Now it is planReveal's rule applied to
   *  clicks - same answer restyles (retrying a failed line), any other
   *  answer takes the map over, drawn through to the clicked card. */
  async function selectLoop(loop: Loop, turn: number, loops: Loop[], fold: number) {
    setSelectedTrail(loop.id);
    routes.select(loop.id);
    void routes.load(loop.id);
    const clickedIndex = loops.findIndex((candidate) => candidate.id === loop.id);
    // A takeover restores everything this answer had on show: its revealed
    // reach when that is known, never less than the fold or the clicked card.
    const reach = Math.max(fold, revealed.current.get(turn) ?? 0);
    const plan = planSelect(drawnTurn.current, turn, reach, clickedIndex);
    drawnTurn.current = plan.drawnTurn;
    if (plan.takeover) {
      await loadLoopGeometry(loops.slice(...plan.slice), turn);
      return;
    }
    if (needsFetch(loopFeatures.current.get(loop.id))) {
      await appendLoopGeometry([loop], turn);
      return;
    }
    drawLoops(loop.id);
  }

  async function loadLoopGeometry(loops: Loop[], turn: number) {
    loopFeatures.current.clear();
    setLineStatus({});
    await appendLoopGeometry(loops, turn);
  }

  /** "Show more" under one answer's loops.
   *
   *  Revealing cards of the answer already on the map ADDS them to it. From
   *  any other answer it is a change of subject: that turn takes the map
   *  over, drawn from its first card so what is shown is one answer whole,
   *  never a mix of two.
   */
  async function revealLoops(turn: number, loops: Loop[], from: number, to: number) {
    const plan = planReveal(drawnTurn.current, turn, from, to);
    drawnTurn.current = plan.drawnTurn;
    const [start, end] = plan.slice;
    if (!plan.clear) {
      await appendLoopGeometry(loops.slice(start, end), turn);
      return;
    }
    routes.select(null);
    setSelectedTrail(null);
    await loadLoopGeometry(loops.slice(start, end), turn);
  }

  /** Fetch geometry for these loops and merge it into the drawn set — used
   *  both for the visible fold of a fresh answer and for cards a "show more"
   *  just revealed. Settled, not all: one route missing its geometry must not
   *  stop the others being drawn. */
  async function appendLoopGeometry(loops: Loop[], turn: number) {
    // Errors are retried on the next ask; ok and missing (404) are settled;
    // a line already on the wire is not asked for again.
    const wanted = loops.filter(
      (loop) =>
        needsFetch(loopFeatures.current.get(loop.id)) &&
        !linesInFlight.current.has(loop.id),
    );
    wanted.forEach((loop) => linesInFlight.current.add(loop.id));
    const results = await Promise.allSettled(
      wanted.map((loop) => fetchRouteGeoJson(loop.id)),
    ).finally(() => wanted.forEach((loop) => linesInFlight.current.delete(loop.id)));
    // The drawn set belongs to ONE answer, and this fetch was slow enough that
    // another answer may own it now. Guarding only the dispatch left the
    // continuation free to merge an older turn's routes into the new set.
    if (!mayDraw(drawnTurn.current, turn)) return;
    results.forEach((result, index) => {
      const id = wanted[index].id;
      // recordLine also verifies the payload IS this route: a response whose
      // own route_id disagrees is recorded as an error, never drawn.
      loopFeatures.current.set(
        id,
        result.status === 'fulfilled' ? recordLine(result.value, id) : { status: 'error' },
      );
    });
    setLineStatus(
      Object.fromEntries(
        [...loopFeatures.current.entries()].map(([id, entry]) => [id, entry.status]),
      ),
    );
    drawLoops(routes.current());
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

    // Which assistant turn this submit is writing: the user turn is appended
    // first, so it is the one after it. A later "show more" compares against
    // this to tell whose geometry is on the map.
    const turnIndex = messages.length + 1;

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
              drawnTurn.current = turnIndex;
              // The new answer owns the SELECTION as well as the map —
              // revealLoops' takeover resets both, and this path must too:
              // a selected id from the previous answer is never in the
              // fresh cache, and drawableFeatures rightly draws nothing for
              // a selection it cannot show. Without the reset, every answer
              // after a card click arrived to a blank map.
              routes.select(null);
              setSelectedTrail(null);
              emitDetail(null);
              void loadLoopGeometry(event.results.loops.slice(0, fold), turnIndex);
              break;
            }
            // A composed plan can resolve several routes; draw them all.
            const lines = (event.results.routes ?? [])
              .map((block) => block.geometry)
              .filter((g): g is GeoJSON.LineString => Boolean(g));
            if (lines.length > 1) {
              emitGeometry({
                type: 'FeatureCollection',
                features: lines.map((geometry) => ({
                  type: 'Feature',
                  properties: {},
                  geometry,
                })),
              });
            } else if (event.results.geometry) {
              emitGeometry(event.results.geometry);
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
    // display:none rather than a class, so it beats `.chat { display: flex }`
    // — and it takes the panel out of the tab order and the a11y tree too.
    <section className="chat" style={hidden ? { display: 'none' } : undefined}>
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
                fold={message.results.answered_count ?? DEFAULT_FOLD}
                onReveal={(from, to) => {
                  revealed.current.set(index, to);
                  void revealLoops(index, message.results?.loops ?? [], from, to);
                }}
              >
                {message.results.loops.map((loop) => (
                  <LoopCard
                    key={loop.id}
                    loop={loop}
                    selected={selectedTrail === loop.id}
                    onSelect={(picked) =>
                      void selectLoop(
                        picked,
                        index,
                        message.results?.loops ?? [],
                        message.results?.answered_count ?? DEFAULT_FOLD,
                      )
                    }
                    onExpand={(picked) =>
                      void selectLoop(
                        picked,
                        index,
                        message.results?.loops ?? [],
                        message.results?.answered_count ?? DEFAULT_FOLD,
                      )
                    }
                    line={lineStatus[loop.id]}
                    detail={routes.details[loop.id]}
                    favorited={favorites?.has(loop.id) ?? false}
                    onToggleFavorite={onToggleFavorite}
                  />
                ))}
              </FoldedCards>
            )}

            {message.results?.trails && message.results.trails.length > 0 && (
              <FoldedCards fold={message.results.answered_count ?? DEFAULT_FOLD}>
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
