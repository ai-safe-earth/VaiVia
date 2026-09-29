// @vitest-environment jsdom
/**
 * Cards open in place (owner decision 2026-09-29): a tap on the body, the
 * name or "+ info" opens the card inside the transcript with its own map;
 * only its close button closes it; a fourth open card closes the oldest.
 * The map is mocked in test/setup.ts — what is pinned here is which line
 * each open card is handed, and when it is fetched.
 */

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPanel } from '@/components/ChatPanel';
import type { ChatMessage, Loop } from '@/lib/types';

const api = vi.hoisted(() => ({
  fetchRouteGeoJson: vi.fn<(id: string) => Promise<GeoJSON.Feature | null>>(),
  fetchRouteDetail: vi.fn(async () => null),
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

function geoCalls(id: string): number {
  return api.fetchRouteGeoJson.mock.calls.filter(([called]) => called === id).length;
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchRouteGeoJson.mockImplementation(async (id) => line(id));
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

function renderPanel(
  messages: ChatMessage[] = TRANSCRIPT,
  conversationId: string | null = null,
) {
  const onAsk = vi.fn();
  const view = render(
    <ChatPanel
      onAsk={onAsk}
      initialMessages={messages}
      initialConversationId={conversationId}
    />,
  );
  const card = (id: string) =>
    view.container.querySelector<HTMLElement>(`[data-route-id="${id}"]`)!;
  const isOpen = (id: string) => card(id).hasAttribute('data-open');
  const mapLine = (id: string) =>
    card(id).querySelector<HTMLElement>('[data-testid="card-map"]')?.dataset.line;
  const close = (id: string) =>
    fireEvent.click(card(id).querySelector<HTMLElement>('.card-close')!);
  return { onAsk, view, card, isOpen, mapLine, close };
}

describe('a card opens in place', () => {
  it('a body tap opens it with its own map, drawn with its own line', async () => {
    const { card, isOpen, mapLine } = renderPanel();

    fireEvent.click(card('a1'));

    expect(isOpen('a1')).toBe(true);
    await waitFor(() => expect(mapLine('a1')).toBe('ok:a1'));
    expect(isOpen('a2')).toBe(false);
  });

  it('"+ info" is the same gesture as the body tap', async () => {
    const { card, isOpen, mapLine } = renderPanel();

    fireEvent.click(card('a1').querySelector<HTMLElement>('.detail-toggle')!);

    expect(isOpen('a1')).toBe(true);
    await waitFor(() => expect(mapLine('a1')).toBe('ok:a1'));
  });

  it('only the close button closes it: a tap on the open card does nothing', () => {
    const { card, isOpen, close } = renderPanel();

    fireEvent.click(card('a1'));
    fireEvent.click(card('a1'));
    expect(isOpen('a1')).toBe(true);
    // Open, it is no longer a button and has no "+ info".
    expect(card('a1').getAttribute('role')).toBeNull();
    expect(card('a1').querySelector('.detail-toggle')).toBeNull();

    close('a1');
    expect(isOpen('a1')).toBe(false);
  });

  it('stays open when another card opens, across answers too', () => {
    const { card, isOpen } = renderPanel();

    fireEvent.click(card('a1'));
    fireEvent.click(card('b1'));

    expect(isOpen('a1')).toBe(true);
    expect(isOpen('b1')).toBe(true);
  });

  it('a fourth open card closes the oldest', () => {
    const { card, isOpen } = renderPanel();

    for (const id of ['a1', 'a2', 'b1', 'b2']) fireEvent.click(card(id));

    expect(isOpen('a1')).toBe(false);
    expect(['a2', 'b1', 'b2'].every(isOpen)).toBe(true);
  });

  it('stays open when a new answer arrives', async () => {
    api.sendChat.mockImplementation(async function* () {
      yield { type: 'conversation', conversationId: 'conv-1' };
      yield {
        type: 'results',
        results: { kind: 'loop_search', answered_count: 2, loops: B },
      };
      yield { type: 'done', usage: { input_tokens: 0, output_tokens: 0 } };
    });
    const { card, isOpen, view } = renderPanel(TRANSCRIPT.slice(0, 2));

    fireEvent.click(card('a1'));
    fireEvent.change(view.getByLabelText('Your message'), {
      target: { value: 'something shorter' },
    });
    fireEvent.submit(view.container.querySelector('form')!);

    await waitFor(() => expect(card('b1')).toBeTruthy());
    expect(isOpen('a1')).toBe(true);
  });
});

describe("an open card's line", () => {
  it('is fetched once: reopening does not ask again', async () => {
    const { card, mapLine, close } = renderPanel();

    fireEvent.click(card('a1'));
    await waitFor(() => expect(mapLine('a1')).toBe('ok:a1'));
    close('a1');
    fireEvent.click(card('a1'));

    await waitFor(() => expect(mapLine('a1')).toBe('ok:a1'));
    expect(geoCalls('a1')).toBe(1);
  });

  it('is never fetched for a card nobody opened', async () => {
    const { card, mapLine } = renderPanel();

    fireEvent.click(card('a1'));
    await waitFor(() => expect(mapLine('a1')).toBe('ok:a1'));

    expect(geoCalls('a2')).toBe(0);
  });

  it('a failed line says so on the card, and reopening retries', async () => {
    api.fetchRouteGeoJson.mockRejectedValueOnce(new Error('503'));
    const { card, view, mapLine, close } = renderPanel();

    fireEvent.click(card('a1'));
    await waitFor(() => expect(mapLine('a1')).toBe('error'));
    expect(view.getByText(/Map line unavailable/)).toBeTruthy();

    close('a1');
    fireEvent.click(card('a1'));
    await waitFor(() => expect(mapLine('a1')).toBe('ok:a1'));
    expect(geoCalls('a1')).toBe(2);
  });

  it('a payload claiming to be another route is never drawn under this card', async () => {
    api.fetchRouteGeoJson.mockImplementation(async () => line('someone-else'));
    const { card, mapLine } = renderPanel();

    fireEvent.click(card('a1'));

    await waitFor(() => expect(mapLine('a1')).toBe('error'));
  });

  it('a 404 reads as no-longer-saved, settled', async () => {
    api.fetchRouteGeoJson.mockImplementation(async () => null);
    const { card, view, mapLine } = renderPanel();

    fireEvent.click(card('a1'));

    await waitFor(() => expect(mapLine('a1')).toBe('missing'));
    expect(view.getByText(/no longer saved/)).toBeTruthy();
  });
});

describe('the open card asks whether the route was right', () => {
  it('a card from a STORED turn carries the turn, so the thumbs show', () => {
    const stored: ChatMessage[] = [
      { role: 'user', content: 'first ask' },
      {
        role: 'assistant',
        content: 'answer A',
        messageId: 'm1',
        results: { kind: 'loop_search', answered_count: 2, loops: A },
      },
    ];
    const { card } = renderPanel(stored, 'conv-1');

    fireEvent.click(card('a1'));

    expect(card('a1').querySelector('[aria-label="Bad route"]')).toBeTruthy();
  });

  it('a card of a still-streaming turn shows no thumbs (the known gap)', () => {
    const { card } = renderPanel(TRANSCRIPT, 'conv-1');

    fireEvent.click(card('a1'));

    expect(card('a1').querySelector('[aria-label="Bad route"]')).toBeNull();
  });
});

describe('asking', () => {
  it('an ask tells the page, before the answer starts', () => {
    const { onAsk, view } = renderPanel();

    fireEvent.change(view.getByLabelText('Your message'), {
      target: { value: 'something else' },
    });
    fireEvent.submit(view.container.querySelector('form')!);

    expect(onAsk).toHaveBeenCalled();
  });

  it('an empty submit is not an ask', () => {
    const { onAsk, view } = renderPanel();

    fireEvent.submit(view.container.querySelector('form')!);

    expect(onAsk).not.toHaveBeenCalled();
  });

  it('an emptied follow-up shows the No matches notice', async () => {
    api.sendChat.mockImplementation(async function* () {
      yield { type: 'conversation', conversationId: 'conv-1' };
      yield {
        type: 'results',
        results: { kind: 'loop_search', answered_count: 5, loops: [] },
      };
      yield { type: 'done', usage: { input_tokens: 0, output_tokens: 0 } };
    });
    const { view } = renderPanel(TRANSCRIPT.slice(0, 2));

    fireEvent.change(view.getByLabelText('Your message'), {
      target: { value: 'only ones with a waterfall' },
    });
    fireEvent.submit(view.container.querySelector('form')!);

    await waitFor(() => expect(view.getByTestId('no-matches')).toBeTruthy());
  });

  it('hidden hides the transcript and NOT the composer', () => {
    const view = render(<ChatPanel initialMessages={TRANSCRIPT} hidden />);

    // The Saved-routes view owns the transcript; the composer is the bottom
    // edge of the app and stays askable behind it.
    expect(view.container.querySelector<HTMLElement>('.messages')!.style.display).toBe(
      'none',
    );
    expect(view.getByLabelText('Your message')).toBeTruthy();
  });
});
