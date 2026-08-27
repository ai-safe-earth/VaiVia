// @vitest-environment jsdom
/**
 * The real ChatPanel, rendered: a click on any card draws THAT card's
 * answer, or says the line is unavailable — never another answer's cache.
 *
 * This is the component-level pin for the "any card shows any map" defect:
 * the pure rules live in lib/mapTurn.ts with their own tests, but a guard
 * can be wired to the wrong entry point (a click, not a reveal) while every
 * pure test stays green. Here the wiring itself is what is asserted.
 */

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPanel } from '@/components/ChatPanel';
import type { ChatMessage, Loop } from '@/lib/types';

const api = vi.hoisted(() => ({
  fetchRouteGeoJson: vi.fn<(id: string) => Promise<GeoJSON.Feature | null>>(),
  fetchRouteDetail: vi.fn(async () => null),
  fetchTrailGeoJson: vi.fn(async () => null),
  sendChat: vi.fn(),
  sendFeedback: vi.fn(async () => undefined),
}));

vi.mock('@/lib/api', () => ({
  AuthRequiredError: class AuthRequiredError extends Error {},
  ...api,
}));
vi.mock('@/lib/supabaseClient', () => ({ isAuthConfigured: () => true }));

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

const A = [mkLoop('a1', 'Route A1'), mkLoop('a2', 'Route A2')];
const B = [mkLoop('b1', 'Route B1'), mkLoop('b2', 'Route B2')];

/** Two answered turns, as a resumed-or-scrolled transcript renders them. */
const TRANSCRIPT: ChatMessage[] = [
  { role: 'user', content: 'first ask' },
  {
    role: 'assistant',
    content: 'answer A',
    results: { kind: 'loop_search', answered_count: 2, loops: A },
  },
  { role: 'user', content: 'second ask' },
  {
    role: 'assistant',
    content: 'answer B',
    results: { kind: 'loop_search', answered_count: 2, loops: B },
  },
];

/** The ids the last emission drew, its selection — or null for a cleared map. */
function lastDrawn(onGeometry: ReturnType<typeof vi.fn>) {
  const emitted = onGeometry.mock.calls.at(-1)?.[0] as
    | GeoJSON.FeatureCollection
    | null
    | undefined;
  if (emitted == null) return null;
  return {
    ids: emitted.features.map((f) => f.properties?.id),
    selected: emitted.features.filter((f) => f.properties?.selected).map((f) => f.properties?.id),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchRouteGeoJson.mockImplementation(async (id) => line(id));
  // jsdom has no scrollIntoView; the transcript calls it on every render.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

function renderPanel() {
  const onGeometry = vi.fn();
  const { container } = render(
    <ChatPanel onGeometry={onGeometry} initialMessages={TRANSCRIPT} />,
  );
  const card = (id: string) =>
    container.querySelector<HTMLElement>(`[data-route-id="${id}"]`)!;
  return { onGeometry, container, card };
}

describe('a card click draws its own answer', () => {
  it('clicking cards across answers always draws the clicked answer, selection marked', async () => {
    const { onGeometry, card } = renderPanel();

    fireEvent.click(card('a1'));
    await waitFor(() =>
      expect(lastDrawn(onGeometry)).toEqual({ ids: ['a1', 'a2'], selected: ['a1'] }),
    );

    fireEvent.click(card('b1'));
    await waitFor(() =>
      expect(lastDrawn(onGeometry)).toEqual({ ids: ['b1', 'b2'], selected: ['b1'] }),
    );

    // The defect: answer B owns the cache; clicking A2 up the transcript
    // used to emit B's features wearing A2's name and fit the map to them.
    fireEvent.click(card('a2'));
    await waitFor(() =>
      expect(lastDrawn(onGeometry)).toEqual({ ids: ['a1', 'a2'], selected: ['a2'] }),
    );
  });

  it('a card whose line failed says so and never draws its siblings', async () => {
    api.fetchRouteGeoJson.mockImplementation(async (id) => {
      if (id === 'a2') throw new Error('route geometry failed: 503');
      return line(id);
    });
    const { onGeometry, card, container } = renderPanel();

    fireEvent.click(card('a1'));
    await waitFor(() =>
      expect(lastDrawn(onGeometry)).toEqual({ ids: ['a1'], selected: ['a1'] }),
    );
    // The failure is on the card, not silently absent from the map.
    await waitFor(() =>
      expect(card('a2').textContent).toContain('Map line unavailable'),
    );

    // Clicking the failed card retries; still failing, the map CLEARS rather
    // than framing a1 under a2's name.
    fireEvent.click(card('a2'));
    await waitFor(() => expect(lastDrawn(onGeometry)).toBeNull());
    expect(container.textContent).toContain('Map line unavailable');
  });

  it('a payload claiming to be another route is never drawn under this card', async () => {
    api.fetchRouteGeoJson.mockImplementation(async (id) =>
      id === 'a1' ? line('zzz') : line(id),
    );
    const { onGeometry, card } = renderPanel();

    fireEvent.click(card('a1'));
    // a1's payload is recorded as an error (id mismatch); with a1 selected
    // the map draws nothing, and the card carries the unavailable note.
    await waitFor(() => expect(lastDrawn(onGeometry)).toBeNull());
    await waitFor(() =>
      expect(card('a1').textContent).toContain('Map line unavailable'),
    );
  });

  it('a 404 line reads as left-the-catalogue, and the rest still draw', async () => {
    api.fetchRouteGeoJson.mockImplementation(async (id) =>
      id === 'a2' ? null : line(id),
    );
    const { onGeometry, card } = renderPanel();

    fireEvent.click(card('a1'));
    await waitFor(() =>
      expect(lastDrawn(onGeometry)).toEqual({ ids: ['a1'], selected: ['a1'] }),
    );
    await waitFor(() =>
      expect(card('a2').textContent).toContain('left the catalogue'),
    );
  });
});
