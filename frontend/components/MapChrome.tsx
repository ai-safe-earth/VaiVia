'use client';

/**
 * The route profile: the altitude curve with the ground it runs over drawn
 * as a band beneath it, so "62% gravel" is a place on the route and not a
 * sentence. One drawing for every card that shows a profile — the map
 * layer's route panel and the saved-routes list are the same card.
 *
 * The x-axis is metres along the route. A drawn route's spans and its
 * profile both cumulate the same walked lengths, so the band and the curve
 * index the same metres by construction; a saved route carries no spans, and
 * its band is the document's distribution laid out as a summary bar (said in
 * the markup as data-summary, never passed off as position).
 */

import { useId, useState } from 'react';

import {
  SURFACE_ORDER,
  type DrawnProfile,
  type SurfaceClass,
  surfaceClass,
  thin,
} from '@/lib/profile';

const W = 600;
const TOP = 8;
const PLOT_H = 92;
const BAND_Y = TOP + PLOT_H + 8;
const BAND_H = 12;
const H = BAND_Y + BAND_H;
const MAX_POINTS = 240;

const CLASS_LABEL: Record<SurfaceClass, string> = {
  paved: 'paved',
  gravel: 'gravel',
  ground: 'ground',
  untagged: 'untagged',
};

function km(metres: number): string {
  return `${(metres / 1000).toFixed(1)} km`;
}

export function RouteProfile({ profile }: { profile: DrawnProfile }) {
  const hatch = useId();
  const [hover, setHover] = useState<number | null>(null);
  const { series, spans, shares, quality } = profile;

  const n = series.distance_m.length;
  const total = series.distance_m[n - 1] || 1;
  const idx = thin(
    Array.from({ length: n }, (_, i) => i),
    MAX_POINTS,
  );
  const low = Math.min(...series.elevation_m);
  const high = Math.max(...series.elevation_m);
  const range = high - low || 1;
  const x = (i: number) => (series.distance_m[i]! / total) * W;
  const y = (i: number) => TOP + PLOT_H - ((series.elevation_m[i]! - low) / range) * PLOT_H;
  const line = idx.map((i, k) => `${k ? 'L' : 'M'}${x(i).toFixed(1)} ${y(i).toFixed(1)}`).join(' ');
  const area = `${line} L${W} ${TOP + PLOT_H} L0 ${TOP + PLOT_H} Z`;

  // The band: positional from spans, a summary bar from shares otherwise.
  const band: { from: number; to: number; cls: SurfaceClass }[] = [];
  if (spans && spans.length > 0) {
    let from = 0;
    for (const span of spans) {
      band.push({ from, to: span.to_m, cls: surfaceClass(span.surface) });
      from = span.to_m;
    }
  } else {
    let from = 0;
    for (const cls of SURFACE_ORDER) {
      const share = shares[cls] ?? 0;
      if (share <= 0) continue;
      band.push({ from, to: from + share * total, cls });
      from += share * total;
    }
  }
  const legend = SURFACE_ORDER.filter((cls) => (shares[cls] ?? 0) >= 0.01);

  const hoverAt = hover === null ? null : idx[hover]!;
  const hoverSpan =
    hoverAt === null || !spans ? null : spans.find((s) => s.to_m >= series.distance_m[hoverAt]!);

  return (
    <div className="detail-profile">
      <div
        className="profile"
        onPointerMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
          setHover(Math.round(ratio * (idx.length - 1)));
        }}
        onPointerLeave={() => setHover(null)}
      >
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`Altitude profile over ${km(total)}: from ${Math.round(series.elevation_m[0]!)} m to ${Math.round(series.elevation_m[n - 1]!)} m, highest ${Math.round(high)} m`}
        >
          <defs>
            <pattern id={hatch} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="2" height="6" className="hatch" />
            </pattern>
          </defs>
          <path className="area" d={area} />
          <path className="curve" d={line} />
          <g className="band" data-summary={spans && spans.length > 0 ? undefined : 'true'}>
            {band.map((b, k) => (
              <rect
                key={k}
                className={`surface-${b.cls}`}
                x={(b.from / total) * W}
                y={BAND_Y}
                width={Math.max(0, ((b.to - b.from) / total) * W)}
                height={BAND_H}
                fill={b.cls === 'untagged' ? `url(#${hatch})` : undefined}
              />
            ))}
          </g>
          {hoverAt !== null && (
            <g className="hover">
              <line x1={x(hoverAt)} x2={x(hoverAt)} y1={TOP} y2={BAND_Y + BAND_H} />
              <circle cx={x(hoverAt)} cy={y(hoverAt)} r="4" />
            </g>
          )}
        </svg>
        {hoverAt !== null && (
          <span
            className="profile-tip vv-label"
            style={{ left: `${(series.distance_m[hoverAt]! / total) * 100}%` }}
          >
            {km(series.distance_m[hoverAt]!)} · {Math.round(series.elevation_m[hoverAt]!)} m
            {hoverSpan ? ` · ${hoverSpan.surface?.replace('_', ' ') ?? 'untagged'}` : ''}
          </span>
        )}
      </div>
      {legend.length > 0 && (
        <div className="profile-legend">
          {legend.map((cls) => (
            <span key={cls} className="legend-chip vv-label">
              <i className={`swatch surface-${cls}`} aria-hidden="true" />
              {CLASS_LABEL[cls]} {Math.round((shares[cls] ?? 0) * 100)}%
            </span>
          ))}
        </div>
      )}
      {quality === 'approximate' && (
        <p className="profile-caveat vv-body-sm">
          Stitched across the gaps of a multi-part route — read the shape, not
          the distances.
        </p>
      )}
    </div>
  );
}
