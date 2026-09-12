'use client';

/**
 * "How I read it" — what the system understood from the question.
 *
 * Load-bearing per the brand spec: it is what lets a user correct a constraint
 * instead of rewriting the whole question. Wired 2026-08-21.
 *
 * The rows come from the backend (chat/readback.py) and describe the plan that
 * was EXECUTED, not the model's subqueries — the composer's own decisions are
 * the interesting half, and this is the only place they surface: a distance
 * band widened from a single stated number, a duration dropped because our
 * figures are not calibrated, a difficulty capped by "with my kids", features
 * required together rather than any-of, and which store was searched.
 *
 * Empty means nothing was searched — a clarify turn — and says exactly that
 * rather than showing an empty grid, which would read as "we understood
 * nothing about your question".
 */

export interface Constraint {
  key: string;
  value: string;
}

export function QueryReading({ reading }: { reading?: Constraint[] }) {
  const empty = !reading || reading.length === 0;

  return (
    <section className="reading" aria-label="How I read it">
      <span className="vv-label">How I read it</span>

      {empty ? (
        <p className="reading-pending vv-body-sm">
          I did not search for this one — I asked for more detail instead.
        </p>
      ) : (
        <>
          <div className="reading-grid">
            {reading.map((constraint) => (
              <div className="reading-row vv-data" key={constraint.key}>
                <span className="vv-data-key">{constraint.key}</span>
                <span className="reading-value">{constraint.value}</span>
              </div>
            ))}
          </div>
          <p className="reading-edit vv-body-sm">
            Not editable yet — ask again in different words to change it.
          </p>
        </>
      )}
    </section>
  );
}
