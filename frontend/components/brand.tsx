'use client';

/**
 * The logo mark and the icon set, inlined from assets/brand/.
 *
 * They are inline SVG rather than <img> for one reason: every icon is drawn in
 * `currentColor` at 1.75px, so colour comes from CSS (--vv-text by default,
 * --vv-flare for hazard and exposure, --vv-lime for the active route). An
 * <img> would freeze the stroke colour at whatever the file says.
 *
 * Geometry is copied verbatim from assets/brand/icons/*.svg — if a shape needs
 * to change, change it there first.
 */

export type IconName =
  | 'trail'
  | 'rifugio'
  | 'station'
  | 'summit'
  | 'lake'
  | 'funivia'
  | 'time'
  | 'ascent'
  | 'hazard'
  | 'exposure';

const PATHS: Record<IconName, React.ReactNode> = {
  trail: <polyline points="3,17 7,9 11,13 17,3" strokeLinejoin="round" />,
  rifugio: (
    <>
      <polyline points="3,9 10,3.5 17,9" />
      <rect x="5" y="9" width="10" height="7.5" />
    </>
  ),
  station: (
    <>
      <rect x="4.5" y="3" width="11" height="10" rx="2" />
      <line x1="4.5" y1="9" x2="15.5" y2="9" />
      <line x1="6" y1="13" x2="4" y2="17" />
      <line x1="14" y1="13" x2="16" y2="17" />
    </>
  ),
  summit: <polyline points="2,16 8,5 12,11 14.5,7.5 18,16" strokeLinejoin="round" />,
  lake: (
    <>
      <line x1="3" y1="8" x2="17" y2="8" />
      <line x1="3" y1="12" x2="17" y2="12" />
      <line x1="5" y1="16" x2="15" y2="16" />
    </>
  ),
  funivia: (
    <>
      <line x1="3" y1="5" x2="17" y2="9" />
      <circle cx="8" cy="9.5" r="2.5" />
      <line x1="10" y1="17" x2="17" y2="17" />
    </>
  ),
  time: (
    <>
      <circle cx="10" cy="10" r="6.5" />
      <line x1="10" y1="6" x2="10" y2="10.5" />
      <line x1="10" y1="10.5" x2="13" y2="12.5" />
    </>
  ),
  ascent: (
    <>
      <line x1="10" y1="17" x2="10" y2="4" />
      <polyline points="6,8 10,4 14,8" />
    </>
  ),
  hazard: (
    <>
      <line x1="10" y1="3" x2="10" y2="12" />
      <circle cx="10" cy="16" r="1.4" fill="currentColor" stroke="none" />
    </>
  ),
  exposure: (
    <>
      <rect x="10" y="2.5" width="10.6" height="10.6" transform="rotate(45 10 2.5)" />
      <line x1="10" y1="7" x2="10" y2="10.5" />
    </>
  ),
};

interface IconProps {
  name: IconName;
  /** 22 or 26 per the spec; 22 is the in-line size, 26 the standalone one. */
  size?: 22 | 26;
}

export function Icon({ name, size = 22 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}

/** POI types the graph emits, mapped onto the ten icons we have. Anything
 *  unmapped renders as text alone — inventing an icon for it would be adding
 *  to a closed set. */
const POI_ICONS: Record<string, IconName> = {
  lake: 'lake',
  bathing_water: 'lake',
  hut: 'rifugio',
  shelter: 'rifugio',
  station: 'station',
  peak: 'summit',
  summit: 'summit',
  viewpoint: 'summit',
  aerialway: 'funivia',
  cable_car: 'funivia',
  gondola: 'funivia',
};

export function poiIcon(type: string): IconName | null {
  return POI_ICONS[type] ?? null;
}

/**
 * Two nodes and one edge: the data model — two sources, matched by proximity,
 * never merged. Minimum 16px, and the lime block is always the upper node.
 */
export function Mark({ size = 17 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 60 60"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <line
        x1="16"
        y1="44"
        x2="44"
        y2="16"
        stroke="var(--vv-text)"
        strokeWidth="5"
      />
      <rect x="9" y="37" width="14" height="14" fill="var(--vv-text)" />
      <rect x="37" y="9" width="14" height="14" fill="var(--vv-lime)" />
    </svg>
  );
}
