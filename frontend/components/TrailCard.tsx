'use client';

import { distance, elevation, hazard, poiLabel, primaryDuration } from '@/lib/format';
import type { Trail } from '@/lib/types';

interface Props {
  trail: Trail;
  selected: boolean;
  onSelect: (trail: Trail) => void;
}

export function TrailCard({ trail, selected, onSelect }: Props) {
  const time = primaryDuration(trail);
  const gain = elevation(trail.elevation_gain_m);

  // A div with button semantics rather than <button>: the card holds a real
  // <a> to Trailforks, and an anchor inside a button is invalid HTML.
  return (
    <div
      role="button"
      tabIndex={0}
      className="card"
      aria-pressed={selected}
      onClick={() => onSelect(trail)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect(trail);
        }
      }}
    >
      <h3>{trail.name}</h3>
      <div className="stats">
        <span>{trail.difficulty}</span>
        <span>{distance(trail.total_distance_m)}</span>
        {time && (
          <span>
            {time.value} {time.label}
          </span>
        )}
        {gain && <span>↑ {gain}</span>}
      </div>

      {(trail.pois.length > 0 || trail.seasonal_hazards.length > 0) && (
        <div className="tags">
          {trail.pois.slice(0, 4).map((poi, index) => (
            <span className="tag" key={`${poi.type}-${index}`}>
              {poi.name ?? poiLabel(poi.type)}
            </span>
          ))}
          {trail.seasonal_hazards.map((key) => (
            <span className="tag warn" key={key}>
              {hazard(key)}
            </span>
          ))}
        </div>
      )}

      {trail.difficulty_notes && <p className="note">{trail.difficulty_notes}</p>}

      {trail.trailforks_url && (
        <a
          className="external"
          href={trail.trailforks_url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => event.stopPropagation()}
        >
          View on Trailforks ↗
        </a>
      )}
    </div>
  );
}
