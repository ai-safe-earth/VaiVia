'use client';

import { distance, distanceFigure, durationFigure, elevationFigure } from '@/lib/format';
import type { Loop } from '@/lib/types';

import { Sources } from './Sources';

interface Props {
  loop: Loop;
  selected: boolean;
  onSelect: (loop: Loop) => void;
}

/** sac_scale and mtb:scale, in words. Both scales run past what a route
 *  catalogue should be offering, so the top band is deliberately blunt. */
const HIKE_RATING_LABEL: Record<number, string> = {
  0: 'Easy walking',
  1: 'Hiking',
  2: 'Mountain hiking',
  3: 'Demanding',
  4: 'Alpine',
  5: 'Alpine',
  6: 'Alpine',
};

const MTB_RATING_LABEL: Record<number, string> = {
  0: 'Easy',
  1: 'Easy',
  2: 'Intermediate',
  3: 'Technical',
  4: 'Technical',
  5: 'Very technical',
  6: 'Very technical',
};

function difficultyLabel(loop: Loop): string | null {
  const rating = loop.activity === 'mtb' ? loop.mtb_rating : loop.hike_rating;
  if (rating === null || rating === undefined) return null;
  const table = loop.activity === 'mtb' ? MTB_RATING_LABEL : HIKE_RATING_LABEL;
  return table[rating] ?? null;
}

/** SAC T1–T6 as rotated squares, filled to the route's grade. Hiking only:
 *  mtb:scale is a different scale and rendering it in SAC squares would be
 *  inventing a grade the route does not have. */
function SacScale({ rating }: { rating: number }) {
  return (
    <div className="sac" role="img" aria-label={`SAC grade ${rating} of 6`}>
      {[1, 2, 3, 4, 5, 6].map((step) => (
        <i key={step} className={step <= rating ? 'on' : undefined} />
      ))}
    </div>
  );
}

export function LoopCard({ loop, selected, onSelect }: Props) {
  const length = distanceFigure(loop.distance_m);
  const climb = elevationFigure(loop.ascent_m);
  const time = durationFigure(loop);
  const grade = difficultyLabel(loop);
  const sac = loop.activity === 'mtb' ? null : loop.hike_rating;
  // 81% of routes are named after something they pass. The rest genuinely have
  // no name, so show what the route IS rather than an id or a made-up label.
  const heading = loop.name ?? `${distance(loop.distance_m)} ${loop.activity} loop`;

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
        {time && (
          <div className="figure">
            <span className="vv-figure">{time.value}</span>
            <span className="unit vv-label">{time.unit}</span>
          </div>
        )}
        {(sac !== null || grade) && (
          <div className="figure grade">
            {sac !== null ? (
              <SacScale rating={sac} />
            ) : (
              <span className="vv-subtitle">{grade}</span>
            )}
            <span className="unit vv-label">{grade ?? 'grade'}</span>
          </div>
        )}
      </div>

      {(loop.named_pois.length > 0 || loop.trailhead_name) && (
        <div className="key-facts">
          <div>
            <span className="vv-label">Starts at</span>
            <p className="fact vv-body-sm">{loop.trailhead_name ?? 'an unnamed junction'}</p>
          </div>
          {loop.named_pois.length > 0 && (
            <div>
              <span className="vv-label">Along the way</span>
              <div className="poi-list">
                {loop.named_pois.slice(0, 3).map((name) => (
                  <span className="poi vv-body-sm" key={name}>
                    {name}
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
