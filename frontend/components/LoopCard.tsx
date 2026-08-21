'use client';

import { distance, distanceFigure, elevationFigure } from '@/lib/format';
import type { Loop } from '@/lib/types';

import { Sources } from './Sources';

interface Props {
  loop: Loop;
  selected: boolean;
  onSelect: (loop: Loop) => void;
}

/** SAC grades in catalogue order — index+1 is the rank the squares fill to. */
const SAC_ORDER = [
  'hiking',
  'mountain_hiking',
  'demanding_mountain_hiking',
  'alpine_hiking',
  'demanding_alpine_hiking',
  'difficult_alpine_hiking',
];

/** sac_scale in words. The scale runs past what a route catalogue should be
 *  offering, so the top band is deliberately blunt. */
const SAC_LABEL: Record<string, string> = {
  hiking: 'Hiking',
  mountain_hiking: 'Mountain hiking',
  demanding_mountain_hiking: 'Demanding',
  alpine_hiking: 'Alpine',
  demanding_alpine_hiking: 'Alpine',
  difficult_alpine_hiking: 'Alpine',
};

/** SAC T1–T6 as rotated squares, filled to the route's EXIGENT grade — the
 *  hardest metre walked, never the character label: on a card the grade is a
 *  safety promise, and "a T2 walk with a T4 move" must show T4. */
function SacScale({ rank }: { rank: number }) {
  return (
    <div className="sac" role="img" aria-label={`SAC grade ${rank} of 6`}>
      {[1, 2, 3, 4, 5, 6].map((step) => (
        <i key={step} className={step <= rank ? 'on' : undefined} />
      ))}
    </div>
  );
}

export function LoopCard({ loop, selected, onSelect }: Props) {
  const length = distanceFigure(loop.distance_m);
  const climb = elevationFigure(loop.ascent_m);
  // The exigent grade drives the display (owner rule 2026-08-20).
  const sacRank = loop.sac_max ? SAC_ORDER.indexOf(loop.sac_max) + 1 : 0;
  const grade =
    loop.activity === 'mtb'
      ? loop.mtb_scale !== null
        ? `S${loop.mtb_scale}`
        : loop.mtb_rideable
          ? 'Rideable'
          : null
      : loop.sac_max
        ? (SAC_LABEL[loop.sac_max] ?? null)
        : null;
  // Destination routes carry their destination's name; OSM relations their
  // own. The rest genuinely have no name, so show what the route IS rather
  // than an id or a made-up label.
  const heading =
    loop.name ??
    (loop.ref ? `Sentiero ${loop.ref}` : `${distance(loop.distance_m)} ${loop.activity} loop`);
  const startName = loop.start_names?.[0] ?? null;

  // Which KIND of outing this is, said out loud (owner rule 2026-08-21): a
  // trail ask can answer with loops, out-and-backs and named trails in one
  // list, and the shapes must stay distinguishable at a glance. 'circular'
  // and 'linear' are MEASURED on mapped routes (pipeline/export/shape.py);
  // 'loop'/'destination' are constructed. 'Named route' survives only for
  // pre-1.2 documents in stale transcripts.
  const shapeLabel =
    loop.shape === 'loop' || loop.shape === 'circular'
      ? 'Loop'
      : loop.shape === 'destination'
        ? 'Out & back'
        : loop.shape === 'linear'
          ? 'Linear'
          : 'Named route';

  // A div with button semantics, matching TrailCard, so the two lists behave
  // identically to a keyboard and share the route-card styling.
  return (
    <div
      role="button"
      tabIndex={0}
      className="route-card"
      aria-pressed={selected}
      onClick={() => onSelect(loop)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect(loop);
        }
      }}
    >
      <span className="route-kind vv-label">{shapeLabel}</span>
      <h3 className="route-name vv-title">{heading}</h3>

      <div className="figures">
        <div className="figure">
          <span className="vv-figure vv-figure-key">{length.value}</span>
          <span className="unit vv-label">{length.unit}</span>
        </div>
        {climb && (
          <div className="figure">
            <span className="vv-figure">{climb.value}</span>
            <span className="unit vv-label">{climb.unit}</span>
          </div>
        )}
        {(sacRank > 0 || grade) && (
          <div className="figure grade">
            {loop.activity !== 'mtb' && sacRank > 0 ? (
              <SacScale rank={sacRank} />
            ) : (
              <span className="vv-subtitle">{grade}</span>
            )}
            <span className="unit vv-label">{grade ?? 'grade'}</span>
          </div>
        )}
      </div>

      {(loop.pois.length > 0 || startName || loop.car_free) && (
        <div className="key-facts">
          {(startName || loop.car_free) && (
            <div>
              <span className="vv-label">Starts at</span>
              <p className="fact vv-body-sm">
                {startName ?? 'an unnamed trailhead'}
                {loop.car_free ? ' · reachable by train' : ''}
              </p>
            </div>
          )}
          {loop.pois.length > 0 && (
            <div>
              <span className="vv-label">Along the way</span>
              <div className="poi-list">
                {loop.pois
                  .filter((poi) => poi.name)
                  .slice(0, 3)
                  .map((poi) => (
                    <span className="poi vv-body-sm" key={`${poi.name}-${poi.type}`}>
                      {poi.name}
                    </span>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      <Sources id={loop.id} />
    </div>
  );
}
