'use client';

import { useState } from 'react';

import { SAC_ORDER } from '@/lib/difficulty';
import { distance, distanceFigure, elevationFigure } from '@/lib/format';
import type { LineStatus } from '@/lib/mapTurn';
import { profileFromDetail } from '@/lib/profile';
import type { Loop, RouteDetail } from '@/lib/types';

import { Icon } from './brand';
import { ElevationProfile } from './MapChrome';
import { Sources } from './Sources';

interface Props {
  loop: Loop;
  selected: boolean;
  onSelect: (loop: Loop) => void;
  /** The document detail, fetched by the parent on first expand: undefined =
   *  not asked yet / loading, null = route left the catalogue. */
  detail?: RouteDetail | null;
  /** Fired when the card opens, so the parent can fetch the detail and focus
   *  the map on this route. */
  onExpand?: (loop: Loop) => void;
  /** This card's map line: undefined = not asked yet, 'ok' = drawn on
   *  select, 'missing' = the route left the catalogue (settled), 'error' =
   *  the fetch failed and a click retries. Said on the card, because the
   *  silent alternative was drawing the SIBLINGS under this card's name. */
  line?: LineStatus;
  /** Saved state + toggle. Absent when signed out — favorites are account
   *  data, so the mark only exists with an account. */
  favorited?: boolean;
  onToggleFavorite?: (loop: Loop, on: boolean) => void;
}

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

export function LoopCard({
  loop,
  selected,
  onSelect,
  detail,
  onExpand,
  line,
  favorited = false,
  onToggleFavorite,
}: Props) {
  // Sources.tsx idiom: per-card state, stopPropagation on the toggle because
  // the card body is the selection click target.
  const [open, setOpen] = useState(false);
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
      data-route-id={loop.id}
      aria-pressed={selected}
      onClick={() => onSelect(loop)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect(loop);
        }
      }}
    >
      <div className="route-kind-row">
        <span className="route-kind vv-label">{shapeLabel}</span>
        {onToggleFavorite && (
          <button
            type="button"
            className="save-toggle"
            aria-pressed={favorited}
            aria-label={favorited ? 'Remove from saved routes' : 'Save this route'}
            title={favorited ? 'Remove from saved routes' : 'Save this route'}
            onClick={(event) => {
              event.stopPropagation();
              onToggleFavorite(loop, !favorited);
            }}
          >
            <Icon name="saved" />
          </button>
        )}
      </div>
      <h3 className="route-name vv-title">{heading}</h3>

      {/* A line that could not be fetched is said out loud. The quiet
          alternative was worse than silence: the map drew this card's
          siblings, framed them, and wore this card's name. */}
      {line === 'missing' && (
        <p className="line-note vv-body-sm">
          No map line — this route has left the catalogue.
        </p>
      )}
      {line === 'error' && (
        <p className="line-note line-note-retry vv-body-sm">
          Map line unavailable — select the card to retry.
        </p>
      )}

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

      <button
        type="button"
        className="detail-toggle"
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation();
          const next = !open;
          setOpen(next);
          if (next) onExpand?.(loop);
        }}
      >
        <span>{open ? 'Less' : 'See more'}</span>
        <span className="sign" aria-hidden="true">
          {open ? '−' : '+'}
        </span>
      </button>

      {open && <LoopDetail loop={loop} detail={detail} />}

      <Sources id={loop.id} />
    </div>
  );
}

/** The expanded half: the figures the row already carries, every named place,
 *  and the altitude profile once the document detail arrives. */
function LoopDetail({ loop, detail }: { loop: Loop; detail?: RouteDetail | null }) {
  const profile = detail ? profileFromDetail(detail) : undefined;
  const descent = elevationFigure(loop.descent_m);
  const extended: { label: string; value: string }[] = [];
  if (descent) extended.push({ label: 'down', value: `${descent.value} m` });
  if (loop.highest_m !== null && loop.lowest_m !== null) {
    extended.push({
      label: 'heights',
      value: `${Math.round(loop.lowest_m)}–${Math.round(loop.highest_m)} m`,
    });
  }
  if (loop.surface_dominant) {
    extended.push({ label: 'mostly', value: loop.surface_dominant.replace('_', ' ') });
  }
  if (loop.off_road_share !== null) {
    extended.push({
      label: 'off-road',
      value: `${Math.round(loop.off_road_share * 100)}%`,
    });
  }
  const namedPois = loop.pois.filter((poi) => poi.name);

  const startName = loop.start_names?.[0] ?? null;

  return (
    <div className="route-detail">
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
                  // The index is in the key because a route can pass the same
                  // place twice — an out-and-back does it by definition, and
                  // two peaks can share a name. Name+type alone collides.
                  .map((poi, index) => (
                    <span
                      className="poi vv-body-sm"
                      key={`${poi.name}-${poi.type}-${index}`}
                    >
                      {poi.name}
                    </span>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      {extended.length > 0 && (
        <div className="detail-figures">
          {extended.map((item) => (
            <div key={item.label} className="detail-figure">
              <span className="vv-label">{item.label}</span>
              <span className="vv-data">{item.value}</span>
            </div>
          ))}
        </div>
      )}

      {/* A route the network holds in pieces says so, before anyone plans
          around a line that is not continuous on the ground. */}
      {loop.continuous === false && loop.pieces !== null && (
        <p className="detail-note vv-body-sm">
          Mapped in {loop.pieces} pieces — the route exists, our network has
          gaps in it.
        </p>
      )}

      {namedPois.length > 3 && (
        <div className="detail-pois">
          <span className="vv-label">Everything it passes</span>
          <div className="poi-list">
            {namedPois.map((poi, index) => (
              <span
                className="poi vv-body-sm"
                key={`${poi.name}-${poi.type}-${index}`}
              >
                {poi.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {profile ? (
        <div className="detail-profile">
          <ElevationProfile profile={profile} quality={detail?.profile_quality} />
          <div className="profile-labels">
            <span className="vv-label">start {Math.round(profile.startM)} m</span>
            <span className="vv-label">max {Math.round(profile.maxM)} m</span>
            <span className="vv-label">end {Math.round(profile.endM)} m</span>
          </div>
        </div>
      ) : detail === undefined ? (
        <p className="detail-note vv-body-sm">Fetching the altitude profile…</p>
      ) : (
        <p className="detail-note vv-body-sm">
          No altitude profile for this route — absent is not zero.
        </p>
      )}
    </div>
  );
}
