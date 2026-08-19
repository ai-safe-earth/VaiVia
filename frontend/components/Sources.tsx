'use client';

import { useState } from 'react';

interface Props {
  /** The graph id of the trail or route — the one identifier the payload
   *  always carries, and what support would ask for. */
  id: string;
  /** OSM way ids behind the geometry. The chat payload does not carry them
   *  today; the row renders the moment it does, rather than shipping the
   *  illustrative ids from the brand mockups. */
  osmWayIds?: Array<number | string>;
  /** Distance of the proximity match that linked the two sources, in metres.
   *  Same story: rendered when the API exposes it. */
  matchDistanceM?: number | null;
  trailforksUrl?: string | null;
}

/**
 * Where a route came from, in the UI rather than in the marketing.
 *
 * The two sources are linked by proximity and never merged — that is the
 * product's central data decision, so it is stated on every route, collapsed
 * by default and one click from the answer.
 */
export function Sources({ id, osmWayIds, matchDistanceM, trailforksUrl }: Props) {
  const [open, setOpen] = useState(false);
  const count = 1 + (trailforksUrl ? 1 : 0);

  return (
    <div className="sources">
      <button
        type="button"
        className="sources-toggle"
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation();
          setOpen(!open);
        }}
      >
        <span>
          {count} {count === 1 ? 'source' : 'sources'}
        </span>
        <span className="sign" aria-hidden="true">
          {open ? '−' : '+'}
        </span>
      </button>

      {open && (
        <div className="sources-body">
          <div className="source-row vv-data">
            <span className="vv-data-key">geometry</span>
            <span className="value">
              {osmWayIds && osmWayIds.length > 0
                ? `OSM ways ${osmWayIds.join(' ')}`
                : 'OpenStreetMap, ODbL'}
            </span>
          </div>

          <div className="source-row vv-data">
            <span className="vv-data-key">route</span>
            <span className="value">{id}</span>
          </div>

          {trailforksUrl && (
            <div className="source-row vv-data">
              <span className="vv-data-key">grade</span>
              <span className="value">
                <a
                  href={trailforksUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(event) => event.stopPropagation()}
                >
                  Trailforks
                </a>{' '}
                — terms pending
              </span>
            </div>
          )}

          <div className="source-row vv-data">
            <span className="vv-data-key">link</span>
            <span className="value">
              {matchDistanceM != null ? `matched at ${Math.round(matchDistanceM)} m · ` : ''}
              matched by proximity, <span className="never-merged">never merged</span>
            </span>
          </div>

          <p className="sources-note vv-body-sm">
            Conditions are crowd-reported and carry no date. VaiVia does not verify them.
          </p>
        </div>
      )}
    </div>
  );
}
