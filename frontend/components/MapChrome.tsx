'use client';

/**
 * Map chrome: the elevation panel along the bottom of the map column.
 *
 * The layer tab strip that used to sit along the top (Route, plus disabled
 * Places / Hazards / Coverage placeholders) was removed 2026-08-27 — what
 * each placeholder was waiting on is recorded in handoff.md; the layers
 * return here when the API can feed them.
 */

export interface Profile {
  /** Metres above sea level, evenly spaced along the route. */
  samples: number[];
  startM: number;
  maxM: number;
  endM: number;
}

/**
 * The bars themselves: 2px-gap lime, height relative to the peak. Shared
 * between the map's elevation panel and the expanded route card, so the two
 * can never drift apart. An 'approximate' profile — stitched across the gaps
 * of a multi-piece route — carries its caveat as part of the drawing: the
 * shape is real, the x-axis is not a true along-route measure.
 */
export function ElevationProfile({
  profile,
  quality,
}: {
  profile: Profile;
  quality?: 'ok' | 'approximate' | null;
}) {
  const peak = Math.max(...profile.samples, 1);
  return (
    <>
      <div className="profile">
        {profile.samples.map((metres, index) => (
          <i key={index} style={{ height: `${(metres / peak) * 100}%` }} />
        ))}
      </div>
      {quality === 'approximate' && (
        <p className="profile-caveat vv-body-sm">
          Stitched across the gaps of a multi-part route — read the shape, not
          the distances.
        </p>
      )}
    </>
  );
}

/**
 * The elevation profile panel under the map: the bars, three labels beneath.
 * With no profile the labels keep their places with an em dash rather than a
 * number: a zero would be a measurement, and we do not have one.
 */
export function ElevationPanel({
  profile,
  quality,
}: {
  profile?: Profile;
  quality?: 'ok' | 'approximate' | null;
}) {
  return (
    <section className="map-panel" aria-label="Elevation profile">
      {profile ? (
        <ElevationProfile profile={profile} quality={quality} />
      ) : (
        <p className="profile-pending vv-body-sm">
          No profile yet — pick a route to see its heights drawn here.
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
