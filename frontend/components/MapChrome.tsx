'use client';

/**
 * The elevation profile drawing, shared by every card that shows one.
 *
 * The panel that used to hold it along the bottom of the map column went with
 * the two-pane layout (2026-08-28): the profile belongs to a route, so it is
 * drawn inside that route's card — in the transcript and in the map layer's
 * route panel, which are the same card. The layer tab strip that sat along the
 * top of the map (Route, plus disabled Places / Hazards / Coverage) was
 * removed 2026-08-27; both return on the map layer's top edge when the API can
 * feed them.
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
