'use client';

import { useState } from 'react';

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
  // Same expand idiom as LoopCard / Sources: per-card state, stopPropagation
  // because the card body is the selection click target. A trail's expanded
  // half shows only what its row already carries — no second fetch.
  const [open, setOpen] = useState(false);
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
      {/* The kind label keeps trails distinguishable from catalogue outings
          when one answer holds both (owner rule 2026-08-21). */}
      <span className="route-kind vv-label">Named trail</span>
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

      <button
        type="button"
        className="detail-toggle"
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation();
          setOpen(!open);
        }}
      >
        <span>{open ? 'Less' : 'Full card'}</span>
        <span className="sign" aria-hidden="true">
          {open ? '−' : '+'}
        </span>
      </button>

      {open && (
        <div className="route-detail">
          {trail.landscape_description && (
            <p className="detail-note vv-body-sm">{trail.landscape_description}</p>
          )}
          {trail.pois.length > 4 && (
            <div className="detail-pois">
              <span className="vv-label">Everything it passes</span>
              <div className="poi-list">
                {trail.pois.map((poi, index) => (
                  <span className="poi vv-body-sm" key={`all-${poi.type}-${index}`}>
                    {poi.name ?? poiLabel(poi.type)}
                  </span>
                ))}
              </div>
            </div>
          )}
          {trail.elevation_loss_m !== null && (
            <div className="detail-figures">
              <div className="detail-figure">
                <span className="vv-label">down</span>
                <span className="vv-data">{Math.round(trail.elevation_loss_m)} m</span>
              </div>
            </div>
          )}
        </div>
      )}

      <Hazard hazards={trail.seasonal_hazards} />

      <Sources id={trail.id} />
    </div>
  );
}
