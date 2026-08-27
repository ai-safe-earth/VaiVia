'use client';

import { useState } from 'react';

import { sendFeedback } from '@/lib/api';

interface Props {
  messageId: string;
  conversationId: string;
}

/**
 * Thumbs on an assistant answer — the user half of the eval loop. A vote
 * posts the moment it is clicked; a thumbs-down also asks what could be
 * improved, and the answer rides a second upsert. Fire-and-forget with a
 * silent catch: a lost vote is not worth interrupting the conversation for.
 *
 * Existing votes are deliberately NOT fetched on resume — re-voting is a
 * harmless upsert, so the thumbs simply render unpressed.
 */
export function Feedback({ messageId, conversationId }: Props) {
  const [vote, setVote] = useState<1 | -1 | null>(null);
  const [askWhy, setAskWhy] = useState(false);
  const [comment, setComment] = useState('');
  const [sent, setSent] = useState(false);

  const cast = (next: 1 | -1) => {
    setVote(next);
    setAskWhy(next === -1);
    setSent(false);
    void sendFeedback(messageId, conversationId, next).catch(() => undefined);
  };

  return (
    <div className="feedback">
      <div className="feedback-row">
        <span className="vv-label">Was this helpful?</span>
        <button
          type="button"
          className="feedback-thumb"
          aria-label="Good answer"
          aria-pressed={vote === 1}
          onClick={() => cast(1)}
        >
          ↑
        </button>
        <button
          type="button"
          className="feedback-thumb"
          aria-label="Bad answer"
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
            void sendFeedback(messageId, conversationId, -1, comment).catch(
              () => undefined,
            );
            setAskWhy(false);
            setSent(true);
          }}
        >
          <input
            type="text"
            value={comment}
            maxLength={2000}
            placeholder="What could be improved?"
            aria-label="What could be improved?"
            onChange={(event) => setComment(event.target.value)}
          />
          <button type="submit" disabled={!comment.trim()}>
            Send
          </button>
        </form>
      )}
    </div>
  );
}
