'use client';

import { distance, elevation, primaryDuration } from '@/lib/format';
import type { Loop } from '@/lib/types';

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

export function LoopCard({ loop, selected, onSelect }: Props) {
  const time = primaryDuration(loop);
  const climb = elevation(loop.ascent_m);
  const grade = difficultyLabel(loop);
  // 81% of routes are named after something they pass. The rest genuinely have
  // no name, so show what the route IS rather than an id or a made-up label.
  const heading = loop.name ?? `${distance(loop.distance_m)} ${loop.activity} loop`;

  // A div with button semantics, matching TrailCard, so the two lists behave
  // identically to a keyboard and share the .card styling.
  return (
    <div
      role="button"
      tabIndex={0}
      className="card"
      aria-pressed={selected}
      onClick={() => onSelect(loop)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect(loop);
        }
      }}
    >
      <h3>{heading}</h3>
      <div className="stats">
        {grade && <span>{grade}</span>}
        <span>{distance(loop.distance_m)}</span>
        {time && (
          <span>
            {time.value} {time.label}
          </span>
        )}
        {climb && <span>↑ {climb}</span>}
      </div>

      {loop.named_pois.length > 0 && (
        <div className="tags">
          {loop.named_pois.slice(0, 4).map((name) => (
            <span className="tag" key={name}>
              {name}
            </span>
          ))}
        </div>
      )}

      {loop.trailhead_name && <p className="note">Starts at {loop.trailhead_name}</p>}
    </div>
  );
}
