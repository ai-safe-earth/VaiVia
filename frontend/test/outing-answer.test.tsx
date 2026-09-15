// @vitest-environment jsdom
/**
 * The drawn-answer surfaces (Phase 12 R4): the assumptions strip renders,
 * chips send a typed delta with no free text of their own, and a drawn
 * card's inline geometry reaches the map without a fetch.
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

function drawnLoop(id: string, distance = 9000, ascent: number | null = 400): Loop {
  return {
    id,
    activity: 'foot',
    kind: 'drawn',
    shape: 'loop',
    name: null,
    ref: null,
    destination_name: null,
    distance_m: distance,
    ascent_m: ascent,
    descent_m: ascent,
    lowest_m: 200,
    highest_m: 700,
    surface_dominant: 'gravel',
    pieces: 1,
    continuous: true,
    graded_share: null,
    sac_scale: null,
    sac_max: null,
    mtb_rideable: null,
    mtb_scale: null,
    bike_blocked_m: null,
    off_road_share: 0.8,
    score: 0.7,
    start_vertex_id: 42,
    start_names: ['Trailhead'],
    car_free: false,
    start_lat: 45.85,
    start_lon: 9.4,
    pois: [],
    ordinal: 1,
    geometry: {
      type: 'LineString',
      coordinates: [
        [9.4, 45.85],
        [9.41, 45.86],
        [9.4, 45.85],
      ],
    },
  };
}

const DRAWN_TURN: ChatMessage[] = [
  { role: 'user', content: 'a two hour loop from here' },
  {
    role: 'assistant',
    content: 'I drew 2 routes.',
    results: {
      kind: 'loop_search',
      answered_count: 5,
      drawn: true,
      assumptions: ['we read ~2 h on foot as 6–10 km (our estimate)'],
      loops: [drawnLoop('vv2-aaaaaaaaaaaaaaaa-fwd'), drawnLoop('vv2-bbbbbbbbbbbbbbbb-fwd', 12000, 800)],
    },
  },
];

beforeEach(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPanel(messages: ChatMessage[]) {
  return render(
    <ChatPanel
      initialMessages={messages}
      onGeometry={vi.fn()}
      onDetail={vi.fn()}
      onPick={vi.fn()}
    />,
  );
}

describe('drawn answers', () => {
  it('renders the assumptions strip', () => {
    const { container } = renderPanel(DRAWN_TURN);
    const strip = container.querySelector('.assumptions');
    expect(strip?.textContent).toContain('we read ~2 h on foot as 6–10 km');
  });

  it('offers chips under the latest drawn answer and sends a typed delta', async () => {
    api.sendChat.mockImplementation(async function* () {
      yield { type: 'conversation', conversationId: 'c1' };
      yield { type: 'done', messageId: 'm1', usage: { input_tokens: 0, output_tokens: 0 } };
    });
    const { container } = renderPanel(DRAWN_TURN);
    const chips = [...container.querySelectorAll('.chips button')];
    expect(chips.length).toBeGreaterThanOrEqual(2);
    const shorter = chips.find((b) => b.textContent?.includes('shorter'));
    expect(shorter).toBeTruthy();
    fireEvent.click(shorter!);
    await waitFor(() => expect(api.sendChat).toHaveBeenCalled());
    const [, , opts] = api.sendChat.mock.calls[0];
    // shorter than the shortest card on screen: 9 km × 0.75 ≈ 7
    expect(opts.chip).toEqual({ max_distance_km: 7 });
  });

  it('does not offer chips under a catalogue answer', () => {
    const catalogue: ChatMessage[] = [
      { role: 'user', content: 'a loop' },
      {
        role: 'assistant',
        content: 'Found 2.',
        results: {
          kind: 'loop_search',
          answered_count: 5,
          loops: [drawnLoop('vv2-cccccccccccccccc-fwd')].map((l) => ({
            ...l,
            geometry: undefined,
            ordinal: undefined,
          })),
        },
      },
    ];
    const { container } = renderPanel(catalogue);
    expect(container.querySelector('.chips')).toBeNull();
  });

  it('draws inline geometry without ever fetching', async () => {
    const onGeometry = vi.fn();
    const view = render(
      <ChatPanel
        initialMessages={DRAWN_TURN}
        onGeometry={onGeometry}
        onDetail={vi.fn()}
        onPick={vi.fn()}
      />,
    );
    const card = view.container.querySelector<HTMLElement>(
      '[data-route-id="vv2-aaaaaaaaaaaaaaaa-fwd"]',
    )!;
    fireEvent.click(card);
    await waitFor(() => {
      const emitted = onGeometry.mock.calls.at(-1)?.[0] as GeoJSON.FeatureCollection;
      expect(
        emitted?.features
          .filter((f) => f.properties?.selected)
          .map((f) => f.properties?.id),
      ).toEqual(['vv2-aaaaaaaaaaaaaaaa-fwd']);
    });
    expect(api.fetchRouteGeoJson).not.toHaveBeenCalled();
  });
});
