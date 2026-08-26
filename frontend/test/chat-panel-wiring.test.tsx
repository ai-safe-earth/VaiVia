// @vitest-environment jsdom
/**
 * The wiring the mutants survived.
 *
 * The adversarial review of the first cut showed that chat-panel.test.tsx
 * was one-sided: an "always takeover" mutant, an inverted retry guard, a
 * dropped selectTrail reset and a blank map on every post-click answer all
 * passed the suite. Each test here exists to kill one named mutant — if it
 * looks oddly specific, that is the mutant it is for.
 */

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPanel } from '@/components/ChatPanel';
import type { ChatMessage, Loop, Trail } from '@/lib/types';

const api = vi.hoisted(() => ({
  fetchRouteGeoJson: vi.fn<(id: string) => Promise<GeoJSON.Feature | null>>(),
  fetchRouteDetail: vi.fn(async () => null),
  fetchTrailGeoJson: vi.fn(async () => null),
  sendChat: vi.fn(),
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

function mkTrail(id: string, name: string): Trail {
  return {
    id,
    name,
    activity: 'hiking',
    difficulty: 'easy',
    difficulty_level: 1,
    difficulty_notes: null,
    landscape_description: null,
    total_distance_m: 8000,
    elevation_gain_m: null,
    elevation_loss_m: null,
    duration_hike_min: null,
    duration_mtb_min: null,
    best_seasons: [],
    seasonal_hazards: [],
    pois: [],
  };
}

const A = [mkLoop('a1', 'Route A1'), mkLoop('a2', 'Route A2'), mkLoop('a3', 'Route A3')];
const B = [mkLoop('b1', 'Route B1'), mkLoop('b2', 'Route B2')];

const TRANSCRIPT: ChatMessage[] = [
  { role: 'user', content: 'first ask' },
  {
    role: 'assistant',
    content: 'answer A',
    // fold 2 of 3: a3 waits behind "show more".
    results: { kind: 'loop_search', answered_count: 2, loops: A },
  },
  { role: 'user', content: 'second ask' },
  {
    role: 'assistant',
    content: 'answer B',
    results: { kind: 'loop_search', answered_count: 2, loops: B },
  },
];

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

function geoCalls(id: string): number {
  return api.fetchRouteGeoJson.mock.calls.filter(([called]) => called === id).length;
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchRouteGeoJson.mockImplementation(async (id) => line(id));
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

function renderPanel(messages: ChatMessage[] = TRANSCRIPT) {
  const onGeometry = vi.fn();
  const onDetail = vi.fn();
  const view = render(
    <ChatPanel onGeometry={onGeometry} onDetail={onDetail} initialMessages={messages} />,
  );
  const card = (id: string) =>
    view.container.querySelector<HTMLElement>(`[data-route-id="${id}"]`)!;
  return { onGeometry, onDetail, view, card };
}

describe('the mutants the review named', () => {
  it('a same-answer click restyles without refetching (kills always-takeover)', async () => {
    const { onGeometry, card } = renderPanel();

    fireEvent.click(card('a1'));
    await waitFor(() => expect(lastDrawn(onGeometry)?.selected).toEqual(['a1']));
    const fetchesAfterFirst = api.fetchRouteGeoJson.mock.calls.length;

    fireEvent.click(card('a2'));
    await waitFor(() => expect(lastDrawn(onGeometry)?.selected).toEqual(['a2']));
    // A takeover would clear the cache and refetch the fold; a restyle
    // touches the network not at all.
    expect(api.fetchRouteGeoJson.mock.calls.length).toBe(fetchesAfterFirst);
  });

  it('clicking a failed card dispatches the retry (kills inverted needsFetch)', async () => {
    api.fetchRouteGeoJson.mockImplementation(async (id) => {
      if (id === 'a2') throw new Error('route geometry failed: 503');
      return line(id);
    });
    const { onGeometry, card } = renderPanel();

    fireEvent.click(card('a1'));
    await waitFor(() => expect(lastDrawn(onGeometry)?.selected).toEqual(['a1']));
    expect(geoCalls('a2')).toBe(1);

    fireEvent.click(card('a2'));
    // The retry went out — the error is retryable, unlike a settled 404.
    await waitFor(() => expect(geoCalls('a2')).toBe(2));
  });

  it('a line is never fetched twice concurrently (kills the duplicate GET)', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    api.fetchRouteGeoJson.mockImplementation(async (id) => {
      await gate;
      return line(id);
    });
    const { onGeometry, card } = renderPanel();

    // First click starts the fold's fetches; second click lands while they
    // are still in flight and must not re-dispatch the same ids.
    fireEvent.click(card('a1'));
    fireEvent.click(card('a2'));
    release();
    await waitFor(() => expect(lastDrawn(onGeometry)).not.toBeNull());
    expect(geoCalls('a1')).toBe(1);
    expect(geoCalls('a2')).toBe(1);
  });

  it('picking a trail hands the map back: the next loop click is a takeover (kills dropped selectTrail reset)', async () => {
    const trail = mkTrail('t1', 'Trail T1');
    const withTrail: ChatMessage[] = [
      ...TRANSCRIPT,
      { role: 'user', content: 'a named trail?' },
      {
        role: 'assistant',
        content: 'a trail',
        results: { kind: 'trail_search', answered_count: 1, trails: [trail] },
      },
    ];
    const { onGeometry, view, card } = renderPanel(withTrail);

    fireEvent.click(card('a1'));
    await waitFor(() => expect(lastDrawn(onGeometry)?.selected).toEqual(['a1']));
    const before = api.fetchRouteGeoJson.mock.calls.length;

    fireEvent.click(view.getByText('Trail T1'));
    await waitFor(() => expect(api.fetchTrailGeoJson).toHaveBeenCalledWith('t1'));

    // The trail cleared the loop cache AND the answer ownership, so this
    // click must refetch answer A rather than restyle an emptied cache
    // into a partial or blank map.
    fireEvent.click(card('a1'));
    await waitFor(() => expect(lastDrawn(onGeometry)?.selected).toEqual(['a1']));
    expect(api.fetchRouteGeoJson.mock.calls.length).toBe(before + 2);
  });

  it('a click-takeover restores the revealed reach, and reveal heals holes', async () => {
    const { onGeometry, view, card } = renderPanel();

    // Reveal a3 (fold is 2 of 3): answer A owns the map, all three drawn.
    fireEvent.click(view.getByText(/Show 1 more/));
    await waitFor(() =>
      expect(lastDrawn(onGeometry)).toEqual({ ids: ['a1', 'a2', 'a3'], selected: [] }),
    );

    // Answer B takes the map over…
    fireEvent.click(card('b1'));
    await waitFor(() => expect(lastDrawn(onGeometry)?.selected).toEqual(['b1']));

    // …and clicking a revealed card of A restores everything the user can
    // SEE — three cards, three lines — not just the fold of two.
    fireEvent.click(card('a3'));
    await waitFor(() =>
      expect(lastDrawn(onGeometry)).toEqual({ ids: ['a1', 'a2', 'a3'], selected: ['a3'] }),
    );
  });

  it('a fresh answer draws over a standing selection (kills the blank-map blocker)', async () => {
    api.sendChat.mockImplementation(async function* () {
      yield { type: 'conversation', conversationId: 'conv-1' };
      yield {
        type: 'results',
        results: { kind: 'loop_search', answered_count: 2, loops: B },
      };
      yield { type: 'done', usage: { input_tokens: 0, output_tokens: 0 } };
    });
    const onlyA = TRANSCRIPT.slice(0, 2);
    const { onGeometry, onDetail, view, card } = renderPanel(onlyA);

    // The normal reading flow: click a card, then ask a follow-up.
    fireEvent.click(card('a1'));
    await waitFor(() => expect(lastDrawn(onGeometry)?.selected).toEqual(['a1']));

    fireEvent.change(view.getByLabelText('Your message'), {
      target: { value: 'something shorter' },
    });
    fireEvent.submit(view.container.querySelector('form')!);

    // The new answer must arrive to a MAP, not to the empty state: the
    // stale a1 selection blanked every post-click answer until the results
    // branch learned to reset the selection like a reveal-takeover does.
    await waitFor(() =>
      expect(lastDrawn(onGeometry)).toEqual({ ids: ['b1', 'b2'], selected: [] }),
    );
    // The elevation panel let go of a1's detail with it.
    expect(onDetail).toHaveBeenLastCalledWith(null);
  });

  it("coming back from hidden redraws this panel's own selection", async () => {
    const onGeometry = vi.fn();
    const view = render(
      <ChatPanel onGeometry={onGeometry} initialMessages={TRANSCRIPT.slice(0, 2)} />,
    );
    const a1 = view.container.querySelector<HTMLElement>('[data-route-id="a1"]')!;
    fireEvent.click(a1);
    await waitFor(() => expect(lastDrawn(onGeometry)?.selected).toEqual(['a1']));
    const drawnBefore = lastDrawn(onGeometry);
    const emissions = onGeometry.mock.calls.length;

    view.rerender(
      <ChatPanel onGeometry={onGeometry} initialMessages={TRANSCRIPT.slice(0, 2)} hidden />,
    );
    view.rerender(
      <ChatPanel onGeometry={onGeometry} initialMessages={TRANSCRIPT.slice(0, 2)} />,
    );
    // Unhide replays the selection so the favorites view's last route does
    // not stay on the map under the transcript.
    await waitFor(() => expect(onGeometry.mock.calls.length).toBeGreaterThan(emissions));
    expect(lastDrawn(onGeometry)).toEqual(drawnBefore);
  });
});

describe('an emptied follow-up says so', () => {
  it('clears the previous picture and shows the No matches notice', async () => {
    api.sendChat.mockImplementation(async function* () {
      yield { type: 'conversation', conversationId: 'conv-1' };
      yield {
        type: 'results',
        results: { kind: 'loop_search', answered_count: 5, loops: [] },
      };
      yield { type: 'done', usage: { input_tokens: 0, output_tokens: 0 } };
    });
    const { onGeometry, onDetail, view, card } = renderPanel(TRANSCRIPT.slice(0, 2));

    // The previous answer is on the map.
    fireEvent.click(card('a1'));
    await waitFor(() => expect(lastDrawn(onGeometry)?.selected).toEqual(['a1']));

    fireEvent.change(view.getByLabelText('Your message'), {
      target: { value: 'only ones with a waterfall' },
    });
    fireEvent.submit(view.container.querySelector('form')!);

    // The follow-up matched nothing: the old lines leave the map...
    await waitFor(() => expect(onGeometry).toHaveBeenLastCalledWith(null));
    expect(onDetail).toHaveBeenLastCalledWith(null);
    // ...and the transcript says so instead of staying silent.
    await waitFor(() =>
      expect(view.getByTestId('no-matches')).toBeTruthy(),
    );
  });

  it('a clarify keeps the previous picture (the question is about it)', async () => {
    api.sendChat.mockImplementation(async function* () {
      yield { type: 'conversation', conversationId: 'conv-1' };
      yield {
        type: 'results',
        results: { kind: 'clarify', clarification: 'Which area?' },
      };
      yield { type: 'token', delta: 'Which area?' };
      yield { type: 'done', usage: { input_tokens: 0, output_tokens: 0 } };
    });
    const { onGeometry, view, card } = renderPanel(TRANSCRIPT.slice(0, 2));

    fireEvent.click(card('a1'));
    await waitFor(() => expect(lastDrawn(onGeometry)?.selected).toEqual(['a1']));
    const emissionsBefore = onGeometry.mock.calls.length;

    fireEvent.change(view.getByLabelText('Your message'), {
      target: { value: 'hm' },
    });
    fireEvent.submit(view.container.querySelector('form')!);

    await waitFor(() => expect(view.queryByText('Which area?')).toBeTruthy());
    // No new geometry emission: the map still shows the routes being asked about.
    expect(onGeometry.mock.calls.length).toBe(emissionsBefore);
    expect(view.queryByTestId('no-matches')).toBeNull();
  });
});

describe('a click during the in-flight line batch', () => {
  it('never blanks the map; the batch draws the selection when it lands', async () => {
    // The initial batch hangs until we release it.
    const gate: Record<string, (v: GeoJSON.Feature | null) => void> = {};
    api.fetchRouteGeoJson.mockImplementation(
      (id: string) =>
        new Promise((resolve) => {
          gate[id] = resolve;
        }),
    );
    api.sendChat.mockImplementation(async function* () {
      yield { type: 'conversation', conversationId: 'conv-1' };
      yield {
        type: 'results',
        results: { kind: 'loop_search', answered_count: 2, loops: B },
      };
      yield { type: 'done', usage: { input_tokens: 0, output_tokens: 0 } };
    });
    const { onGeometry, view, card } = renderPanel([]);

    fireEvent.change(view.getByLabelText('Your message'), {
      target: { value: 'loops please' },
    });
    fireEvent.submit(view.container.querySelector('form')!);
    await waitFor(() => expect(card('b1')).toBeTruthy());

    // Click while both lines are still on the wire: no draw may happen —
    // the cache is empty and a draw here blanked the whole map.
    fireEvent.click(card('b1'));
    await Promise.resolve();
    expect(onGeometry).not.toHaveBeenCalledWith(null);
    const emissions = onGeometry.mock.calls.length;

    // The batch lands: its continuation draws both lines, b1 selected.
    gate['b1']?.(line('b1'));
    gate['b2']?.(line('b2'));
    await waitFor(() =>
      expect(lastDrawn(onGeometry)).toEqual({ ids: ['b1', 'b2'], selected: ['b1'] }),
    );
    expect(onGeometry.mock.calls.length).toBeGreaterThan(emissions);
  });
});
