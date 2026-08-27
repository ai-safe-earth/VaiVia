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
  hiking: 'T1 hiking',
  mountain_hiking: 'T2 mountain',
  demanding_mountain_hiking: 'T3 demanding',
  alpine_hiking: 'T4 alpine',
  demanding_alpine_hiking: 'T5 alpine',
  difficult_alpine_hiking: 'T6 alpine',
};

function sacRank(grade: string | null): number {
  return grade ? SAC_ORDER.indexOf(grade) + 1 : 0;
}

/** The three-valued bike state — the access conjunction along the walked
 *  sequence, with the WHY in metres when it forbids. `null` is unknown,
 *  which is not yes and must never render as silence. */
export function bikeState(loop: Loop): string {
  if (loop.activity === 'mtb' && loop.mtb_scale !== null) return `S${loop.mtb_scale}`;
  if (loop.mtb_rideable === true) return 'Rideable';
  if (loop.mtb_rideable === false) {
    const blocked = loop.bike_blocked_m;
    return blocked != null && blocked > 0
      ? `Not rideable · ${blocked >= 1000 ? `${(blocked / 1000).toFixed(1)} km` : `${Math.round(blocked)} m`} blocked`
      : 'Not rideable';
  }
  return 'Bike: unknown';
}

/** The exigent warning: only when the hardest metre exceeds the character
 *  grade — "a T2 walk with a T4 move" must say T4 where the label says T2. */
export function exigentWarning(loop: Loop): string | null {
  const character = sacRank(loop.sac_scale);
  const exigent = sacRank(loop.sac_max);
  if (exigent === 0 || exigent <= character) return null;
  return `${SAC_LABEL[loop.sac_max!].split(' ')[0]} move`;
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
  const [open, setOpen] = useState(false);
  const length = distanceFigure(loop.distance_m);
  const climb = elevationFigure(loop.ascent_m);
  // Destination routes carry their destination's name; OSM relations their
  // own. The rest genuinely have no name, so show what the route IS rather
  // than an id or a made-up label.
  const heading =
    loop.name ??
    (loop.ref ? `Sentiero ${loop.ref}` : `${distance(loop.distance_m)} ${loop.activity} loop`);

  // The CHARACTER grade — the label the route wears (hardest grade covering
  // ≥5%). The exigent grade appears beside it only when it is harder, as a
  // flare-accented warning: the safety fact must never hide inside the label.
  const character = loop.sac_scale ? (SAC_LABEL[loop.sac_scale] ?? null) : null;
  const warning = exigentWarning(loop);

  const shapeLabel =
    loop.shape === 'loop' || loop.shape === 'circular'
      ? 'Loop'
      : loop.shape === 'out_and_back'
        ? 'There & back'
        : loop.shape === 'destination'
          ? 'Out & back'
          : loop.shape === 'linear'
            ? 'Linear'
            : 'Named route';

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

      {/* Line 1: what it is and how big — name, distance, ascent. */}
      <div className="route-line1">
        <h3 className="route-name vv-title">{heading}</h3>
        <span className="route-figure vv-figure vv-figure-key">
          {length.value}
          <i className="unit vv-label">{length.unit}</i>
        </span>
        {climb && (
          <span className="route-figure vv-figure">
            {climb.value}
            <i className="unit vv-label">{climb.unit}</i>
          </span>
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
    </div>
  );
}

/** The expanded half: the figures, the difficulty told whole, the surface
 *  as a distribution, every place with how far off the line it sits, the
 *  quality warnings carried (never filtered), the altitude profile, and the
 *  document's real attribution. */
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
  if (loop.off_road_share !== null) {
    extended.push({
      label: 'off-road',
      value: `${Math.round(loop.off_road_share * 100)}%`,
    });
  }
  const startName = loop.start_names?.[0] ?? null;
  const namedPois = loop.pois.filter((poi) => poi.name);
  const difficulty = detail?.difficulty as
    | { sac_scale?: string; sac_max?: string; graded_share?: number; rule?: string }
    | undefined
    | null;
  const surfaceRows = surfaceDistribution(detail);
  const warnings =
    (detail?.quality as { warnings?: string[] } | undefined | null)?.warnings ?? [];

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

      {(startName || loop.car_free) && (
        <div className="detail-block">
          <span className="vv-label">Starts at</span>
          <p className="fact vv-body-sm">
            {startName ?? 'an unnamed trailhead'}
            {loop.car_free ? ' · reachable by train' : ''}
          </p>
        </div>
      )}

      {/* The difficulty block WHOLE: both grades, how much of the route is
          graded at all, and the rule that produced the number — the rule
          ships with the figure so nobody has to guess how it was derived. */}
      {difficulty && (difficulty.sac_scale || difficulty.sac_max) && (
        <div className="detail-block">
          <span className="vv-label">Difficulty</span>
          <p className="fact vv-body-sm">
            {difficulty.sac_scale
              ? `character ${SAC_LABEL[difficulty.sac_scale] ?? difficulty.sac_scale}`
              : 'ungraded character'}
            {difficulty.sac_max
              ? ` · hardest metre ${SAC_LABEL[difficulty.sac_max] ?? difficulty.sac_max}`
              : ''}
            {typeof difficulty.graded_share === 'number'
              ? ` · graded on ${Math.round(difficulty.graded_share * 100)}% of its length`
              : ''}
          </p>
          {difficulty.rule && (
            <p className="detail-note vv-body-sm">{difficulty.rule}</p>
          )}
        </div>
      )}

      {/* "62% unpaved" is a fact; "unpaved" alone is a claim. Untagged length
          reports as unknown rather than being renormalised away. */}
      {surfaceRows.length > 0 && (
        <div className="detail-block">
          <span className="vv-label">Underfoot</span>
          <p className="fact vv-body-sm">{surfaceRows.join(' · ')}</p>
        </div>
      )}

      {loop.continuous === false && loop.pieces !== null && (
        <p className="detail-note vv-body-sm">
          Mapped in {loop.pieces} pieces — the route exists, our network has
          gaps in it.
        </p>
      )}

      {warnings.length > 0 && (
        <div className="detail-block">
          <span className="vv-label vv-label-hazard">Quality notes</span>
          {warnings.map((warning) => (
            <p key={warning} className="detail-note vv-body-sm">
              {warning}
            </p>
          ))}
        </div>
      )}

      {/* Places from the DOCUMENT when the detail has arrived — each with how
          far off the line it sits, which is the fact the card's chip list
          could never carry. The loop row's unpositioned chips are the
          fallback while the detail loads. */}
      {detail?.places?.length ? (
        <div className="detail-pois">
          <span className="vv-label">Along the way</span>
          <div className="poi-list">
            {detail.places
              .filter((place) => place.name)
              .slice(0, 12)
              .map((place, index) => (
                <span
                  className="poi vv-body-sm"
                  key={`${place.id}-${index}`}
                  title={`${Math.round(Number(place.offset_m))} m off the line`}
                >
                  {String(place.name)}
                </span>
              ))}
          </div>
        </div>
      ) : namedPois.length > 0 ? (
        <div className="detail-pois">
          <span className="vv-label">Along the way</span>
          <div className="poi-list">
            {namedPois.slice(0, 8).map((poi, index) => (
              <span
                className="poi vv-body-sm"
                key={`${poi.name}-${poi.type}-${index}`}
              >
                {poi.name}
              </span>
            ))}
          </div>
        </div>
      ) : null}

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

      <Sources id={loop.id} attribution={detail?.attribution} />
    </div>
  );
}

/** Top surface shares as readable rows, unknown kept visible. Exported for
 *  the tests: the rendering rule, not a re-implementation of it. */
export function surfaceDistribution(detail: RouteDetail | null | undefined): string[] {
  const distributionRaw = (
    detail?.surface as { distribution?: Record<string, number> } | undefined
  )?.distribution;
  if (!distributionRaw) return [];
  return Object.entries(distributionRaw)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 4)
    .filter(([, share]) => share >= 0.01)
    .map(
      ([surface, share]) =>
        `${Math.round(share * 100)}% ${surface === 'unknown' ? 'untagged' : surface.replace('_', ' ')}`,
    );
}
