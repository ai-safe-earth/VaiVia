// @vitest-environment jsdom
/**
 * The saved-routes cards keep the chat cards' line discipline.
 *
 * This copy is where the guards went missing once before (the review that
 * produced useRouteDetails), and the card/map review found it again: the
 * chat path learned to record fetch outcomes while favorites still
 * swallowed every failure into a silent nothing. Same rules, same tests.
 */

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FavoritesView } from '@/components/FavoritesView';
import type { Loop } from '@/lib/types';

const api = vi.hoisted(() => ({
  fetchRouteGeoJson: vi.fn<(id: string) => Promise<GeoJSON.Feature | null>>(),
  fetchRouteDetail: vi.fn(async () => null),
  fetchFavorites: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  AuthRequiredError: class AuthRequiredError extends Error {},
  ...api,
}));

function line(id: string): GeoJSON.Feature {
  return {
    type: 'Feature',
    properties: { route_id: id },
    geometry: {
      type: 'LineString',
      coordinates: [
        [9.3, 45.8],
        [9.4, 45.9],
      ],
    },
  };
}

function mkLoop(id: string, name: string): Loop {
  return {
    id,
    activity: 'hike',
    kind: 'generated',
    shape: 'loop',
    name,
    ref: null,
    destination_name: null,
    distance_m: 12000,
    ascent_m: 600,
    descent_m: 600,
    lowest_m: 200,
    highest_m: 800,
    surface_dominant: null,
    pieces: null,
    continuous: null,
    graded_share: null,
    sac_scale: null,
    sac_max: null,
    mtb_rideable: null,
    mtb_scale: null,
    bike_blocked_m: null,
    off_road_share: null,
    score: null,
    start_vertex_id: null,
    start_names: null,
    car_free: null,
    start_lat: null,
    start_lon: null,
    pois: [],
  };
}

const SAVED = { routes: [mkLoop('f1', 'Saved F1')], missing: [] };

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchRouteGeoJson.mockImplementation(async (id) => line(id));
  api.fetchFavorites.mockResolvedValue(SAVED);
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

function renderView() {
  const onGeometry = vi.fn();
  const view = render(
    <FavoritesView
      initial={SAVED}
      onGeometry={onGeometry}
      favorites={new Set(['f1'])}
      onToggleFavorite={() => undefined}
    />,
  );
  const card = view.container.querySelector<HTMLElement>('[data-route-id="f1"]')!;
  return { onGeometry, view, card };
}

describe('a saved card draws its own verified line, or says it cannot', () => {
  it('draws the route marked selected', async () => {
    const { onGeometry, card } = renderView();
    fireEvent.click(card);
    await waitFor(() => expect(onGeometry).toHaveBeenCalled());
    const drawn = onGeometry.mock.calls.at(-1)?.[0] as GeoJSON.Feature;
    expect(drawn.properties).toMatchObject({ route_id: 'f1', selected: true });
  });

  it('a failed line clears the map, says so on the card, and a re-select retries', async () => {
    api.fetchRouteGeoJson.mockRejectedValue(new Error('route geometry failed: 503'));
    const { onGeometry, card } = renderView();

    fireEvent.click(card);
    await waitFor(() => expect(onGeometry).toHaveBeenLastCalledWith(null));
    await waitFor(() => expect(card.textContent).toContain('Map line unavailable'));

    // The store comes back; selecting again asks again and draws.
    api.fetchRouteGeoJson.mockImplementation(async (id) => line(id));
    fireEvent.click(card);
    await waitFor(() => {
      const drawn = onGeometry.mock.calls.at(-1)?.[0] as GeoJSON.Feature | null;
      expect(drawn?.properties).toMatchObject({ route_id: 'f1', selected: true });
    });
  });

  it('a payload for some other route is refused, not drawn under this card', async () => {
    api.fetchRouteGeoJson.mockImplementation(async () => line('zzz'));
    const { onGeometry, card } = renderView();
    fireEvent.click(card);
    await waitFor(() => expect(onGeometry).toHaveBeenLastCalledWith(null));
    await waitFor(() => expect(card.textContent).toContain('Map line unavailable'));
  });
});
