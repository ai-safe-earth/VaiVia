'use client';

import {
  distanceFigure,
  durationFigure,
  elevationFigure,
  poiLabel,
} from '@/lib/format';
import type { Trail } from '@/lib/types';

import { Icon, poiIcon } from './brand';
import { Hazard } from './Hazard';
import { Sources } from './Sources';

interface Props {
  trail: Trail;
  selected: boolean;
  onSelect: (trail: Trail) => void;
}

export function TrailCard({ trail, selected, onSelect }: Props) {
  const length = distanceFigure(trail.total_distance_m);
  const climb = elevationFigure(trail.elevation_gain_m);
  const time = durationFigure(trail);

  // A div with button semantics rather than <button>: the card holds real
  // anchors, and an anchor inside a button is invalid HTML.
  return (
    <div
      role="button"
      tabIndex={0}
      className="route-card"
      aria-pressed={selected}
      onClick={() => onSelect(trail)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect(trail);
        }
      }}
    >
      <h3 className="route-name vv-title">{trail.name}</h3>

      {/* Numbers lead, units follow. The first figure in the set is the lime
          one; everything after it is --vv-text. */}
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
        <div className="figure grade">
          <span className="vv-subtitle">{trail.difficulty}</span>
          <span className="unit vv-label">grade</span>
        </div>
      </div>

      {trail.pois.length > 0 && (
        <div className="key-facts">
          <div>
            <span className="vv-label">Along the way</span>
            <div className="poi-list">
              {trail.pois.slice(0, 4).map((poi, index) => {
                const icon = poiIcon(poi.type);
                return (
                  <span className="poi vv-body-sm" key={`${poi.type}-${index}`}>
                    {icon && <Icon name={icon} />}
                    {poi.name ?? poiLabel(poi.type)}
                  </span>
                );
              })}
            </div>
          </div>
          {trail.best_seasons.length > 0 && (
            <div>
              <span className="vv-label">Best seasons</span>
              <p className="fact vv-body-sm">{trail.best_seasons.join(', ')}</p>
            </div>
          )}
        </div>
      )}

      {trail.difficulty_notes && (
        <p className="route-note vv-body">{trail.difficulty_notes}</p>
      )}

      <Hazard hazards={trail.seasonal_hazards} />

      {trail.trailforks_url && (
        <a
          className="route-link vv-body-sm"
          href={trail.trailforks_url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => event.stopPropagation()}
        >
          View on Trailforks
        </a>
      )}

      <Sources id={trail.id} trailforksUrl={trail.trailforks_url} />
    </div>
  );
}
