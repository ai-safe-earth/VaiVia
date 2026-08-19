'use client';

/**
 * Map chrome: the layer tabs along the top, and the panel along the bottom.
 *
 * Everything except the route layer is INACTIVE, and says so in the spec's own
 * vocabulary rather than a bespoke disabled style: an unavailable tab is
 * --vv-muted, which is exactly what the brand system defines it as. Each one is
 * a real `disabled` button, so it is skipped by the keyboard and announced as
 * unavailable rather than merely looking greyed out.
 *
 * What each is waiting on is recorded in handoff.md.
 */

interface Layer {
  id: string;
  label: string;
  /** Null when the layer works; otherwise what it is waiting for. */
  blocked: string | null;
}

const LAYERS: Layer[] = [
  { id: 'route', label: 'Route', blocked: null },
  {
    id: 'places',
    label: 'Places',
    blocked: 'POIs are not returned with map geometry yet',
  },
  {
    id: 'hazards',
    label: 'Hazards',
    blocked: 'hazards are per trail, not per segment, so there is nothing to draw',
  },
  {
    id: 'coverage',
    label: 'Coverage',
    blocked: 'the API does not expose where coverage stops',
  },
];

export function MapLayerTabs() {
  return (
    <nav className="map-tabs" aria-label="Map layers">
      {LAYERS.map((layer) => (
        <button
          key={layer.id}
          type="button"
          className={layer.blocked ? 'unavailable' : 'active'}
          disabled={Boolean(layer.blocked)}
          title={layer.blocked ?? undefined}
        >
          {layer.label}
        </button>
      ))}
    </nav>
  );
}

export interface Profile {
  /** Metres above sea level, evenly spaced along the route. */
  samples: number[];
  startM: number;
  maxM: number;
  endM: number;
}

/**
 * The elevation profile: 2px-gap lime bars, three labels beneath.
 *
 * INACTIVE — the payload carries total ascent, not a series, so there is
 * nothing to plot. The labels keep their places with an em dash rather than a
 * number: a zero would be a measurement, and we do not have one.
 */
export function ElevationPanel({ profile }: { profile?: Profile }) {
  const peak = profile ? Math.max(...profile.samples, 1) : 1;

  return (
    <section className="map-panel" aria-label="Elevation profile">
      {profile ? (
        <div className="profile">
          {profile.samples.map((metres, index) => (
            <i key={index} style={{ height: `${(metres / peak) * 100}%` }} />
          ))}
        </div>
      ) : (
        <p className="profile-pending vv-body-sm">
          No profile yet — the route carries its total climb, not a height at each
          step.
        </p>
      )}

      <div className="profile-labels">
        <span className="vv-label">
          start {profile ? `${Math.round(profile.startM)} m` : '—'}
        </span>
        <span className="vv-label">
          max {profile ? `${Math.round(profile.maxM)} m` : '—'}
        </span>
        <span className="vv-label">
          end {profile ? `${Math.round(profile.endM)} m` : '—'}
        </span>
      </div>
    </section>
  );
}
