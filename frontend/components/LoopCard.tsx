'use client';

import type { KeyboardEvent } from 'react';

import { SAC_ORDER } from '@/lib/difficulty';
import { distance, distanceFigure, elevationFigure } from '@/lib/format';
import type { LineStatus } from '@/lib/mapTurn';
import {
  type DrawnProfile,
  sharesFromDistribution,
  sharesFromSpans,
} from '@/lib/profile';
import type { Loop, RouteDetail } from '@/lib/types';

import { Icon } from './brand';
import { Feedback } from './Feedback';
import { RouteProfile } from './MapChrome';
import { Sources } from './Sources';

interface Props {
  loop: Loop;
  selected: boolean;
  /** The transcript and the saved list: a tap on the body or on "+ info"
   *  raises the map with this route's profile under it. Absent in the map
   *  layer's route panel — that card IS the selection, so there is nothing
   *  for a tap on it to pick, and without a handler it is not a button. */
  onSelect?: (loop: Loop) => void;
  /** The map panel's card: open on arrival with the profile, and its toggle
   *  reads "Less", which lowers the layer. */
  expanded?: boolean;
  onClose?: () => void;
  /** The document detail of a SAVED route, fetched by the parent: undefined
   *  = not asked yet, null = there is none. A drawn route carries its own
   *  profile and spans and needs no detail. */
  detail?: RouteDetail | null;
  /** This card's map line: undefined = not asked yet, 'ok' = drawn on
   *  select, 'missing' = the route is no longer saved (settled), 'error' =
   *  the fetch failed and a click retries. Said on the card, because the
   *  silent alternative was drawing the SIBLINGS under this card's name. */
  line?: LineStatus;
  /** Saved state + toggle. Absent when signed out — favorites are account
   *  data, so the mark only exists with an account. */
  favorited?: boolean;
  onToggleFavorite?: (loop: Loop, on: boolean) => void;
  /** The stored turn that offered this route. With both, the open card asks
   *  whether the route was right — the vote is about (this ask, this route).
   *  Absent for a saved route or a still-streaming turn. */
  messageId?: string;
  conversationId?: string;
}

/** sac_scale in words. The scale runs past what a walker should be offered,
 *  so the top band is deliberately blunt. */
const SAC_LABEL: Record<string, string> = {
  hiking: 'T1 hiking',
  mountain_hiking: 'T2 mountain',
  demanding_mountain_hiking: 'T3 demanding',
  alpine_hiking: 'T4 alpine',
  demanding_alpine_hiking: 'T5 alpine',
  difficult_alpine_hiking: 'T6 alpine',
};

/** Both grades draw on the same six squares: T1..T6 for walking, S0..S5 for
 *  riding, filled to the rank. */
const DOTS = 6;

function sacRank(grade: string | null): number {
  return grade ? SAC_ORDER.indexOf(grade) + 1 : 0;
}

export interface Grade {
  /** Squares filled, 0..6. Zero with a code says "unknown", never "easy". */
  rank: number;
  code: string;
  title: string;
}

export function walkGrade(loop: Loop): Grade {
  const rank = sacRank(loop.sac_scale);
  if (rank === 0) {
    return { rank: 0, code: '?', title: 'Walking grade unknown — not graded on enough of its length' };
  }
  return { rank, code: `T${rank}`, title: SAC_LABEL[loop.sac_scale!] ?? `T${rank}` };
}

/** The three-valued bike state — the access conjunction along the walked
 *  sequence, with the WHY in metres when it forbids. `null` is unknown,
 *  which is not yes and must never render as silence. */
export function rideGrade(loop: Loop): Grade {
  if (loop.mtb_rideable === false) {
    const blocked = loop.bike_blocked_m;
    const why =
      blocked != null && blocked > 0
        ? ` · ${blocked >= 1000 ? `${(blocked / 1000).toFixed(1)} km` : `${Math.round(blocked)} m`} blocked`
        : '';
    return { rank: 0, code: '✕', title: `Not rideable${why}` };
  }
  if (loop.mtb_scale !== null && loop.mtb_scale !== undefined) {
    const scale = Number(loop.mtb_scale);
    if (Number.isFinite(scale)) {
      return {
        rank: Math.min(DOTS, Math.max(0, scale) + 1),
        code: `S${scale}`,
        title: `Mountain-bike scale S${scale}`,
      };
    }
  }
  if (loop.mtb_rideable === true) {
    return { rank: 0, code: '✓', title: 'Rideable end to end, not graded' };
  }
  return { rank: 0, code: '?', title: 'Bike access unknown — unknown is not yes' };
}

/** The exigent warning: only when the hardest metre exceeds the character
 *  grade — "a T2 walk with a T4 move" must say T4 where the label says T2. */
export function exigentWarning(loop: Loop): string | null {
  const character = sacRank(loop.sac_scale);
  const exigent = sacRank(loop.sac_max);
  if (exigent === 0 || exigent <= character) return null;
  return `${SAC_LABEL[loop.sac_max!]!.split(' ')[0]} move`;
}

function GradeMark({ label, grade }: { label: string; grade: Grade }) {
  return (
    <span className="grade" title={grade.title}>
      <span className="vv-label">{label}</span>
      <span className="grade-dots" aria-hidden="true">
        {Array.from({ length: DOTS }, (_, i) => (
          <i key={i} className={i < grade.rank ? 'on' : undefined} />
        ))}
      </span>
      <span className="grade-code vv-body-sm">{grade.code}</span>
    </span>
  );
}

export function LoopCard({
  loop,
  selected,
  onSelect,
  expanded = false,
  onClose,
  detail,
  line,
  favorited = false,
  onToggleFavorite,
  messageId,
  conversationId,
}: Props) {
  const activate = onSelect ? () => onSelect(loop) : undefined;
  const activateByKey = onSelect
    ? (event: KeyboardEvent) => {
        // The card's own keys only, never a descendant's: Enter on the toggle
        // and a space typed into the feedback field both bubble to here.
        if (event.target !== event.currentTarget) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect(loop);
        }
      }
    : undefined;
  const length = distanceFigure(loop.distance_m);
  const climb = elevationFigure(loop.ascent_m);
  // Destination routes carry their destination's name; OSM relations their
  // own. The rest genuinely have no name, so show what the route IS rather
  // than an id or a made-up label.
  const heading =
    loop.name ??
    (loop.ref ? `Sentiero ${loop.ref}` : `${distance(loop.distance_m)} ${loop.activity} loop`);
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
      role={onSelect ? 'button' : undefined}
      tabIndex={onSelect ? 0 : undefined}
      className="route-card"
      data-route-id={loop.id}
      aria-pressed={onSelect ? selected : undefined}
      onClick={activate}
      onKeyDown={activateByKey}
    >
      <div className="route-kind-row">
        <span>
          <span className="route-kind vv-label">{shapeLabel}</span>
          {loop.kind === 'drawn' && (
            <span className="route-origin vv-label">drawn for you</span>
          )}
        </span>
        <span className="route-marks">
          {loop.car_free && (
            <span
              className="train"
              role="img"
              aria-label="Reachable by train"
              title="Reachable by train — the start is a station or a bus stop"
            >
              <Icon name="station" />
            </span>
          )}
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
        </span>
      </div>

      {line === 'missing' && (
        <p className="line-note vv-body-sm">No map line — this route is no longer saved.</p>
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

      {/* Line 2: how it will treat you — the walking and riding grades as
          marks, the exigent warning, and the way to the profile. */}
      <div className="route-line2">
        <GradeMark label="walk" grade={walkGrade(loop)} />
        {warning && (
          <span
            className="exigent vv-label"
            title="The hardest metre walked exceeds the route's character grade — what you must be able to handle, whatever the label says."
          >
            ⚠ {warning}
          </span>
        )}
        <GradeMark label="ride" grade={rideGrade(loop)} />
        {(onSelect || onClose) && (
          <button
            type="button"
            className="detail-toggle"
            aria-expanded={expanded}
            onClick={(event) => {
              event.stopPropagation();
              if (expanded) onClose?.();
              else onSelect?.(loop);
            }}
          >
            <span>{expanded ? 'Less' : 'info'}</span>
            <span className="sign" aria-hidden="true">
              {expanded ? '−' : '+'}
            </span>
          </button>
        )}
      </div>

      {expanded && (
        <LoopDetail
          loop={loop}
          detail={detail}
          messageId={messageId}
          conversationId={conversationId}
        />
      )}
    </div>
  );
}

/** The expanded half, in the map layer's panel: the profile with the ground
 *  under it, the figures, both grades in one line, where it starts, the
 *  quality notes carried (never filtered), the attribution — and the thumbs,
 *  because opening a route is where it is actually judged. */
function LoopDetail({
  loop,
  detail,
  messageId,
  conversationId,
}: {
  loop: Loop;
  detail?: RouteDetail | null;
  messageId?: string;
  conversationId?: string;
}) {
  const series = loop.profile ?? detail?.profile ?? null;
  const profile: DrawnProfile | null =
    series && series.distance_m.length >= 2
      ? {
          series,
          spans: loop.spans,
          shares: loop.spans
            ? sharesFromSpans(loop.spans)
            : sharesFromDistribution(loop.surface ?? detail?.surface?.distribution ?? {}),
          quality: loop.profile ? 'ok' : (detail?.profile_quality ?? null),
        }
      : null;
  // A saved route's profile is still on its way while the detail is; a drawn
  // route never fetches one, so it can only be there or honestly absent.
  const fetching = loop.profile === undefined && detail === undefined;

  const climb = elevationFigure(loop.ascent_m);
  const descent = elevationFigure(loop.descent_m);
  const figures: { label: string; value: string }[] = [];
  if (climb) figures.push({ label: 'up', value: `${climb.value} m` });
  if (descent) figures.push({ label: 'down', value: `${descent.value} m` });
  if (loop.highest_m !== null && loop.lowest_m !== null) {
    figures.push({
      label: 'heights',
      value: `${Math.round(loop.lowest_m)}–${Math.round(loop.highest_m)} m`,
    });
  }

  const walk = walkGrade(loop);
  const ride = rideGrade(loop);
  const warning = exigentWarning(loop);
  const gradedShare = loop.graded_share ?? detail?.difficulty?.graded_share ?? null;
  const walkWords = loop.sac_scale
    ? ` ${(SAC_LABEL[loop.sac_scale] ?? '').split(' ').slice(1).join(' ')}`
    : '';
  const rideWords =
    ride.code === '✕'
      ? ride.title.toLowerCase()
      : ride.code === '✓'
        ? 'rideable'
        : ride.code === '?'
          ? 'unknown'
          : ride.code;
  const startName = loop.start_names?.[0] ?? null;
  const warnings = detail?.quality?.warnings ?? [];

  return (
    <div className="route-detail">
      {profile ? (
        <RouteProfile profile={profile} />
      ) : fetching ? (
        <p className="detail-note vv-body-sm">Fetching the altitude profile…</p>
      ) : (
        <p className="detail-note vv-body-sm">
          No altitude profile for this route — absent is not zero.
        </p>
      )}

      {figures.length > 0 && (
        <div className="detail-figures">
          {figures.map((item) => (
            <div key={item.label} className="detail-figure">
              <span className="vv-label">{item.label}</span>
              <span className="vv-data">{item.value}</span>
            </div>
          ))}
        </div>
      )}

      <p className="fact vv-body-sm">
        walk {walk.code}
        {walkWords}
        {warning ? `, hardest ${warning}` : ''}
        {gradedShare !== null ? ` · graded on ${Math.round(gradedShare * 100)}%` : ''}
        {' · ride '}
        {rideWords}
      </p>

      {startName && (
        <p className="fact vv-body-sm">
          Starts at {startName}
          {loop.car_free ? ' · reachable by train' : ''}
        </p>
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

      <Sources id={loop.id} attribution={detail?.attribution} />

      {/* The card body may be a button, so every click and every typed
          character in here has to stop at the detail. */}
      {messageId && conversationId && (
        <div onClick={(event) => event.stopPropagation()}>
          {/* Keyed on the turn: the map panel's card is keyed by route id, so
              the SAME route offered by two answers reuses this instance — and
              a vote would carry over to a turn nobody judged. */}
          <Feedback
            key={messageId}
            messageId={messageId}
            conversationId={conversationId}
            routeId={loop.id}
          />
        </div>
      )}
    </div>
  );
}
