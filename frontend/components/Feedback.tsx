'use client';

import { useRef, useState } from 'react';

import { sendFeedback } from '@/lib/api';

interface Props {
  messageId: string;
  conversationId: string;
  /** Judge ONE route of the answer rather than the answer as a whole. Its
   *  own row server-side, so the two votes never overwrite each other. */
  routeId?: string;
}

/**
 * Thumbs on an assistant answer — the user half of the eval loop. A vote
 * posts the moment it is clicked; a thumbs-down also asks what could be
 * improved, and the answer rides a second upsert. Fire-and-forget with a
 * silent catch: a lost vote is not worth interrupting the conversation for.
 *
 * The same question, in the same two-field shape, is what a route card asks
 * inside its detail (`routeId`): one standard ask, so every downvote in the
 * ledger reads the same way whatever it is about.
 *
 * Existing votes are deliberately NOT fetched on resume — re-voting is a
 * harmless upsert, so the thumbs simply render unpressed.
 */
export function Feedback({ messageId, conversationId, routeId }: Props) {
  const [vote, setVote] = useState<1 | -1 | null>(null);
  const [askWhy, setAskWhy] = useState(false);
  const [comment, setComment] = useState('');
  const [expected, setExpected] = useState('');
  const [sent, setSent] = useState(false);

  // The why-form's submit chains on the last cast, so the bare vote POST and
  // the texted one can never commit out of order (a delayed bare downvote
  // landing last would null the texts the user just sent).
  const pending = useRef<Promise<unknown> | null>(null);

  const cast = (next: 1 | -1) => {
    setVote(next);
    setAskWhy(next === -1);
    setSent(false);
    // A downvote carries whatever is typed (or already sent), so re-tapping
    // the thumb never wipes the stored answer; a flip to +1 still clears —
    // the documented upsert semantics (the comment was about the old vote).
    pending.current = (
      next === -1
        ? sendFeedback(
            messageId,
            conversationId,
            -1,
            comment.trim() || undefined,
            expected.trim() || undefined,
            routeId,
          )
        : sendFeedback(messageId, conversationId, next, undefined, undefined, routeId)
    ).catch(() => undefined);
  };

  // What is being judged, said in the question and in both thumbs: a card's
  // thumbs sit inside an answer that has thumbs of its own, and two "Good
  // answer" buttons on one screen is a screen reader saying nothing.
  const subject = routeId ? 'route' : 'answer';

  return (
    <div className="feedback">
      <div className="feedback-row">
        <span className="vv-label">
          {routeId ? 'Was this route right?' : 'Was this helpful?'}
        </span>
        <button
          type="button"
          className="feedback-thumb"
          aria-label={`Good ${subject}`}
          aria-pressed={vote === 1}
          onClick={() => cast(1)}
        >
          ↑
        </button>
        <button
          type="button"
          className="feedback-thumb"
          aria-label={`Bad ${subject}`}
          aria-pressed={vote === -1}
          onClick={() => cast(-1)}
        >
          ↓
        </button>
        {sent && <span className="feedback-thanks vv-label">Noted — thank you</span>}
      </div>
      {askWhy && (
        <form
          className="feedback-why"
          onSubmit={(event) => {
            event.preventDefault();
            void (pending.current ?? Promise.resolve())
              .then(() =>
                sendFeedback(
                  messageId,
                  conversationId,
                  -1,
                  comment.trim() || undefined,
                  expected.trim() || undefined,
                  routeId,
                ),
              )
              .catch(() => undefined);
            setAskWhy(false);
            setSent(true);
          }}
        >
          <input
            type="text"
            value={comment}
            maxLength={2000}
            placeholder="What's wrong?"
            aria-label="What's wrong?"
            onChange={(event) => setComment(event.target.value)}
          />
          <input
            type="text"
            value={expected}
            maxLength={2000}
            placeholder="How should it be instead?"
            aria-label="How should it be instead?"
            onChange={(event) => setExpected(event.target.value)}
          />
          <button type="submit" disabled={!comment.trim() && !expected.trim()}>
            Send
          </button>
        </form>
      )}
    </div>
  );
}
