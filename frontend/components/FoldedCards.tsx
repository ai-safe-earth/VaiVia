'use client';

import { Children, useState, type ReactNode } from 'react';

interface Props {
  /** How many cards exist in total (for the "of N" in the control). */
  count: number;
  /** How many show before the fold — the same number the answer prose
   *  narrates (results.answered_count), so text and cards agree. */
  fold: number;
  /** Fired with the [from, to) range a click just revealed, so the parent can
   *  fetch what those cards need (geometry) only once they are visible. */
  onReveal?: (from: number, to: number) => void;
  children: ReactNode;
}

/** How many more cards each click reveals. */
const STEP = 5;

/**
 * The fold under a result list: the first `fold` cards render, the rest wait
 * behind a "show more" control. The search returns more than the answer
 * narrates (CARD_RESULT_LIMIT vs ANSWER_RESULT_LIMIT in the backend), and
 * this is the seam between the two.
 */
export function FoldedCards({ count, fold, onReveal, children }: Props) {
  const [visible, setVisible] = useState(Math.min(Math.max(fold, 1), count));
  const items = Children.toArray(children);
  const hidden = count - visible;

  return (
    <>
      {items.slice(0, visible)}
      {hidden > 0 && (
        <button
          type="button"
          className="show-more"
          onClick={() => {
            const next = Math.min(visible + STEP, count);
            onReveal?.(visible, next);
            setVisible(next);
          }}
        >
          <span>
            Show {Math.min(STEP, hidden)} more of {count}
          </span>
          <span className="sign" aria-hidden="true">
            +
          </span>
        </button>
      )}
    </>
  );
}
