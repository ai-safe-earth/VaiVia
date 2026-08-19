'use client';

/**
 * "How I read it" — what the system understood from the question.
 *
 * Load-bearing per the brand spec: it is what lets a user correct a constraint
 * instead of rewriting the whole question. It is INACTIVE until the backend
 * returns the composed plan: `/chat` streams results, not the constraints the
 * composer merged to get them, so there is nothing truthful to fill the grid
 * with yet.
 *
 * The placeholder says which of the two it is — no data, rather than no
 * constraints — because a silent empty grid would read as "we understood
 * nothing about your question".
 */

export interface Constraint {
  key: string;
  value: string;
}

export function QueryReading({ reading }: { reading?: Constraint[] }) {
  const pending = !reading || reading.length === 0;

  return (
    <section className="reading" aria-label="How I read it">
      <span className="vv-label">How I read it</span>

      {pending ? (
        <p className="reading-pending vv-body-sm">
          Not wired up yet — the plan behind this answer stays in the backend, so
          there is nothing to show you here. Ask again in different words to change
          it.
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
            Change one value instead of rewriting the question.
          </p>
        </>
      )}
    </section>
  );
}
