'use client';

import { useEffect, useRef, useState } from 'react';

import { AuthRequiredError, sendChat } from '@/lib/api';
import { cardKey, closeCard, openCard } from '@/lib/openCards';
import { isAuthConfigured } from '@/lib/supabaseClient';
import type { ChatMessage, Loop } from '@/lib/types';
import { useRouteDetails } from '@/lib/useRouteDetails';
import { useRouteLines } from '@/lib/useRouteLines';


import { Feedback } from './Feedback';
import { FoldedCards } from './FoldedCards';
import { LoopCard } from './LoopCard';
import { QueryReading } from './QueryReading';
import { TrailCard } from './TrailCard';

/** Three asks that DRAW on the parity pack (checked 2026-09-25; golden g64,
 *  g65, g61). A starter that decomposes to a trail search is a dead starter:
 *  "a two hour mountain bike ride" did, and answered nothing. */
const SUGGESTIONS = [
  'an easy half-day walk around Bergamo, starting from the station',
  'a hike loop of about two hours from Lecco, no asphalt',
  'an easy mountain bike loop from Bergamo, about two hours',
];

/** Where the card list folds when the results event carries no
 *  answered_count (older stored turns). */
/** Refinement chips under a drawn answer: each tap is a TYPED delta the
 *  backend merges in Python — no model call, values computed from the cards
 *  on screen so "shorter" means shorter than what you are looking at. */
function chipsFor(loops: Loop[]): { label: string; chip: Record<string, unknown> }[] {
  if (loops.length === 0) return [];
  const minKm = Math.min(...loops.map((l) => l.distance_m)) / 1000;
  const climbs = loops.map((l) => l.ascent_m).filter((a): a is number => a !== null);
  const shorterKm = Math.max(1, Math.round(minKm * 0.75));
  const chips: { label: string; chip: Record<string, unknown> }[] = [
    { label: `shorter — under ${shorterKm} km`, chip: { max_distance_km: shorterKm } },
  ];
  if (climbs.length > 0) {
    const easier = Math.max(50, Math.round((Math.min(...climbs) * 0.6) / 50) * 50);
    chips.push({ label: `less climbing — under ${easier} m`, chip: { max_ascent_m: easier } });
  }
  chips.push({ label: 'no asphalt', chip: { surface_exclusions: ['asphalt'] } });
  return chips;
}

const DEFAULT_FOLD = 5;

interface Props {
  /** Resume this stored conversation; null starts fresh. The page remounts the
   *  panel (via key) on explicit navigation, so state never leaks across
   *  switches — but not when this panel's own first turn is assigned an id. */
  initialConversationId?: string | null;
  initialMessages?: ChatMessage[];
  /** Fired when the backend assigns an id to a brand-new conversation. */
  onConversationCreated?: (id: string) => void;
  /** Clear chat: the page bumps its remount epoch — an explicit user action,
   *  never an SSE event, so the mid-stream remount bug stays impossible. */
  onClear?: () => void;
  /** Saved-route ids + toggle, owned by the page so a bookmark here and one
   *  in the favorites view are the same state. Absent when signed out. */
  favorites?: Set<string>;
  onToggleFavorite?: (loop: Loop, on: boolean) => void;
  /** A question was asked. The page puts Saved routes away: an answer is
   *  read on the conversation, which is where it was asked for. */
  onAsk?: () => void;
  /** Out of sight, still mounted. The Saved-routes view takes over the column
   *  but must not DESTROY the conversation underneath it: this panel owns the
   *  visible transcript, and unmounting it threw the transcript away and
   *  remounted from a `history` prop that only stored-conversation navigation
   *  ever writes. Hidden, not unmounted, so closing Saved routes returns to
   *  the answer you were reading — mid-stream turns included. */
  hidden?: boolean;
}

export function ChatPanel({
  initialConversationId = null,
  initialMessages = [],
  onConversationCreated,
  onClear,
  favorites,
  onToggleFavorite,
  onAsk,
  hidden = false,
}: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(
    initialConversationId,
  );
  // Cards open in place, oldest first, at most three (lib/openCards.ts).
  const [openKeys, setOpenKeys] = useState<string[]>([]);
  const lines = useRouteLines();

  // The user's location, for "from here" asks. Read ONLY when the browser
  // has already granted geolocation — this never prompts; an explicit "use
  // my location" affordance can, later. Sent typed on every ask; the
  // backend validates it to coverage and attaches it after extraction.
  const nearRef = useRef<{ lat: number; lon: number } | null>(null);
  useEffect(() => {
    if (!('permissions' in navigator) || !('geolocation' in navigator)) return;
    navigator.permissions
      .query({ name: 'geolocation' })
      .then((status) => {
        if (status.state !== 'granted') return;
        navigator.geolocation.getCurrentPosition(
          (pos) => {
            nearRef.current = { lat: pos.coords.latitude, lon: pos.coords.longitude };
          },
          () => {},
          { maximumAge: 600_000 },
        );
      })
      .catch(() => {});
  }, []);
  // Route documents' detail (profile, measures) for a SAVED route, fetched
  // once on first open. A drawn route carries its own profile.
  const routes = useRouteDetails();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  /** Open a card in place: its line, and its detail when it has no profile
   *  of its own (/detail answers 404 for a drawn route). */
  function open(loop: Loop, key: string) {
    setOpenKeys((current) => openCard(current, key));
    void lines.ensure(loop);
    if (loop.profile === undefined) void routes.load(loop.id);
  }

  async function submit(text: string, chip?: Record<string, unknown>) {
    const message = text.trim();
    if (!message || busy) return;

    // The answer is read on the conversation.
    onAsk?.();
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
      for await (const event of sendChat(message, conversationId, {
        chip,
        near: nearRef.current ?? undefined,
      })) {
        switch (event.type) {
          case 'conversation':
            if (conversationId === null) onConversationCreated?.(event.conversationId);
            setConversationId(event.conversationId);
            break;
          case 'results': {
            updateLast({ results: event.results });
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
            updateLast({ streaming: false, messageId: event.messageId });
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
    // Hidden hides the TRANSCRIPT, never the section: the composer is the
    // bottom edge of the app and stays live behind the Saved-routes view, so a
    // question asked there is answered here. flex:none shrinks the section to
    // that composer instead of holding half the stage empty.
    <section className="chat" style={hidden ? { flex: 'none' } : undefined}>
      {!isAuthConfigured() && !hidden && (
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

      <div
        className="messages"
        style={hidden ? { display: 'none' } : undefined}
      >
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

            {/* The assumptions strip: the compiler's judgements, said out
                loud where a wrong reading gets corrected — "we read ~3 h as
                12–18 km for kids on bikes". */}
            {message.results?.assumptions && message.results.assumptions.length > 0 && (
              <div className="assumptions">
                {message.results.assumptions.map((assumption) => (
                  <span key={assumption}>{assumption}</span>
                ))}
              </div>
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
              >
                {message.results.loops.map((loop) => {
                  const key = cardKey(index, loop.id);
                  return (
                    <LoopCard
                      key={loop.id}
                      loop={loop}
                      // The turn this card answers: the open card asks whether
                      // the route was right, and the vote is stored against
                      // (this message, this route).
                      messageId={message.messageId}
                      conversationId={conversationId ?? undefined}
                      expanded={openKeys.includes(key)}
                      onOpen={(picked) => open(picked, key)}
                      onClose={() => setOpenKeys((current) => closeCard(current, key))}
                      line={lines.lines[loop.id]}
                      detail={routes.details[loop.id]}
                      favorited={favorites?.has(loop.id) ?? false}
                      onToggleFavorite={onToggleFavorite}
                    />
                  );
                })}
              </FoldedCards>
            )}

            {/* Chips: only under the LATEST drawn answer — a tap refines the
                conversation's standing plan, and older turns no longer speak
                for it. */}
            {message.results?.drawn &&
              !message.streaming &&
              index === messages.length - 1 &&
              message.results.loops &&
              message.results.loops.length > 0 && (
                <div className="suggestions chips">
                  {chipsFor(message.results.loops).map(({ label, chip }) => (
                    <button
                      key={label}
                      type="button"
                      disabled={busy}
                      onClick={() => void submit(label, chip)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}

            {message.results?.trails && message.results.trails.length > 0 && (
              <FoldedCards fold={message.results.answered_count ?? DEFAULT_FOLD}>
                {message.results.trails.map((trail) => (
                  <TrailCard
                    key={trail.id}
                    trail={trail}
                  />
                ))}
              </FoldedCards>
            )}

            {/* An emptied search says so on screen, not only in prose — the
                cards' absence alone is indistinguishable from a turn that
                did nothing. */}
            {message.results &&
              message.results.kind !== 'clarify' &&
              !message.streaming &&
              !message.results.loops?.length &&
              !message.results.trails?.length &&
              !message.results.routes?.length &&
              !message.results.geometry && (
                <div className="notice" data-testid="no-matches">
                  <div className="notice-bar" />
                  <div className="notice-body">
                    <span className="vv-label">No matches</span>
                    <p className="vv-body">
                      Nothing fits all of that — try relaxing one constraint.
                    </p>
                  </div>
                </div>
              )}

            {/* The user half of the eval loop: rendered once the turn is
                stored (messageId arrives on `done`), never mid-stream. */}
            {message.role === 'assistant' &&
              !message.streaming &&
              message.messageId &&
              conversationId && (
                <Feedback
                  key={message.messageId}
                  messageId={message.messageId}
                  conversationId={conversationId}
                />
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
        {/* type="button" is load-bearing: a submit-typed button placed
            before .ask would become the form's default submit. Disabled while
            streaming: a mid-stream clear would remount the panel and orphan
            the SSE loop, whose late results still paint the page's map. */}
        {onClear && (
          <button
            type="button"
            className="clear vv-label"
            onClick={onClear}
            disabled={busy}
          >
            Clear chat
          </button>
        )}
        <button className="ask" type="submit" disabled={busy || !input.trim()}>
          {busy ? '…' : 'Ask'}
        </button>
      </form>
    </section>
  );
}
