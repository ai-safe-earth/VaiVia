// @vitest-environment jsdom
/**
 * The route card, opened in place (owner decision 2026-09-29).
 *
 * Closed, it is a button: the body tap and "+ info" both open it. Open, it
 * carries its own map and detail, is no longer a button, and only its close
 * button — top left — closes it.
 */

import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LoopCard, rideGrade, routeEnds, walkGrade } from '@/components/LoopCard';
import type { Loop } from '@/lib/types';

const api = vi.hoisted(() => ({ sendFeedback: vi.fn(async () => undefined) }));
vi.mock('@/lib/api', () => api);

const LOOP: Loop = {
  id: 'p1',
  activity: 'hike',
  kind: 'generated',
  shape: 'loop',
  name: 'Panel route',
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

/** A drawn card: profile and spans inline, as the planner streams them. */
const DRAWN: Loop = {
  ...LOOP,
  id: 'd1',
  kind: 'drawn',
  sac_scale: 'mountain_hiking',
  sac_max: 'alpine_hiking',
  mtb_rideable: false,
  bike_blocked_m: 1600,
  car_free: true,
  start_names: ['Lecco'],
  profile: { distance_m: [0, 500, 1000, 1500, 2000], elevation_m: [200, 300, 450, 380, 210] },
  spans: [
    { to_m: 600, surface: 'asphalt', highway: 'residential', sac: null },
    { to_m: 1400, surface: 'gravel', highway: 'track', sac: 'hiking' },
    { to_m: 2000, surface: null, highway: 'path', sac: 'mountain_hiking' },
  ],
};

afterEach(cleanup);

describe('the card opens in place', () => {
  it('shows its map, profile and numbers when open', () => {
    const view = render(<LoopCard loop={DRAWN} expanded onClose={() => undefined} />);
    expect(view.container.querySelector('.route-detail')).toBeTruthy();
    expect(view.container.querySelector('.route-detail [data-testid="card-map"]')).toBeTruthy();
    expect(view.container.querySelector('.route-detail .profile svg')).toBeTruthy();
  });

  it('stays shut until opened', () => {
    const view = render(<LoopCard loop={LOOP} onOpen={() => undefined} />);
    expect(view.container.querySelector('.route-detail')).toBeNull();
    expect(view.container.querySelector('.card-close')).toBeNull();
  });

  it('closed, the body and "+ info" both open it', () => {
    const onOpen = vi.fn();
    const view = render(<LoopCard loop={LOOP} onOpen={onOpen} />);
    fireEvent.click(view.container.querySelector('[data-route-id="p1"]')!);
    fireEvent.click(view.container.querySelector('.detail-toggle')!);
    expect(onOpen).toHaveBeenCalledTimes(2);
    expect(onOpen).toHaveBeenCalledWith(LOOP);
  });

  it('open, it is not a button: only the close button acts', () => {
    const onOpen = vi.fn();
    const onClose = vi.fn();
    const view = render(<LoopCard loop={LOOP} expanded onOpen={onOpen} onClose={onClose} />);
    const card = view.container.querySelector('[data-route-id="p1"]')!;
    expect(card.getAttribute('role')).toBeNull();
    expect(card.getAttribute('tabindex')).toBeNull();
    expect(view.container.querySelector('.detail-toggle')).toBeNull();

    fireEvent.click(card);
    expect(onOpen).not.toHaveBeenCalled();
    fireEvent.click(view.getByLabelText('Close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('what the card says at a glance', () => {
  it('draws both grades as six squares filled to the rank, with the code', () => {
    expect(walkGrade(DRAWN)).toMatchObject({ rank: 2, code: 'T2' });
    expect(walkGrade(LOOP)).toMatchObject({ rank: 0, code: '?' });
    expect(rideGrade(DRAWN)).toMatchObject({ rank: 0, code: '✕' });
    expect(rideGrade(DRAWN).title).toContain('1.6 km blocked');
    expect(rideGrade({ ...LOOP, mtb_scale: '3' })).toMatchObject({ rank: 4, code: 'S3' });
    expect(rideGrade({ ...LOOP, mtb_rideable: true })).toMatchObject({ rank: 0, code: '✓' });
    expect(rideGrade(LOOP)).toMatchObject({ rank: 0, code: '?' });

    const view = render(<LoopCard loop={DRAWN} onOpen={() => undefined} />);
    const marks = view.container.querySelectorAll('.grade');
    expect(marks).toHaveLength(2);
    expect(marks[0]!.querySelectorAll('.grade-dots i.on')).toHaveLength(2);
    // The exigent T4 move is said beside the T2 label, in flare, never hidden.
    expect(view.container.querySelector('.exigent')!.textContent).toContain('T4 move');
  });

  it('says where it starts and ends, and whether a train reaches it', () => {
    const view = render(<LoopCard loop={DRAWN} onOpen={() => undefined} />);
    expect(view.container.querySelector('.route-kind')!.textContent).toBe(
      'start Lecco · end Lecco',
    );
    expect(view.getByLabelText('Reachable by train')).toBeTruthy();
    // No "drawn for you" and no shape word: the ends say what it is.
    expect(view.container.textContent).not.toContain('drawn for you');
  });

  it('names the ends in the two shapes: start A · end A, start A · end B', () => {
    const at = { ...LOOP, start_names: ['Ponteranica'] };
    expect(routeEnds(at)).toBe('start Ponteranica · end Ponteranica');
    expect(routeEnds({ ...at, shape: 'destination', destination_name: 'Canto Alto' })).toBe(
      'start Ponteranica · end Canto Alto',
    );
    // There and back to a named place is still "start A · end B".
    expect(routeEnds({ ...at, shape: 'out_and_back', name: 'To Canto Alto' })).toBe(
      'start Ponteranica · end Canto Alto',
    );
    // A relation's own name is not where it ends.
    expect(routeEnds({ ...at, shape: 'linear', name: 'Sentiero delle Orobie' })).toBe(
      'start Ponteranica',
    );
    expect(routeEnds(LOOP)).toBeNull();
  });
});

describe('the profile carries the ground', () => {
  it('draws one band segment per span, hatching the untagged stretch, with a legend of shares', () => {
    const view = render(<LoopCard loop={DRAWN} expanded />);
    const band = view.container.querySelectorAll('.profile .band rect');
    expect(band).toHaveLength(3);
    expect(band[0]!.getAttribute('class')).toBe('surface-paved');
    expect(band[1]!.getAttribute('class')).toBe('surface-gravel');
    expect(band[2]!.getAttribute('fill')).toMatch(/^url\(#/);
    expect(view.container.querySelector('.profile .band')!.getAttribute('data-summary')).toBeNull();
    const legend = Array.from(view.container.querySelectorAll('.legend-chip')).map(
      (chip) => chip.textContent,
    );
    expect(legend).toEqual(['paved 30%', 'gravel 40%', 'untagged 30%']);
    // The one grades line, whole: character, hardest metre, ride verdict.
    expect(view.container.querySelector('.route-detail .fact')!.textContent).toBe(
      'walk T2 mountain, hardest T4 move · ride not rideable · 1.6 km blocked',
    );
  });

  it('lays a saved route\'s distribution out as a summary bar, said as such', () => {
    const saved: Loop = { ...LOOP, surface: { asphalt: 0.5, ground: 0.5 } };
    const detail = {
      route_id: 'p1',
      kind: 'generated',
      shape: 'loop',
      profile: { distance_m: [0, 1000, 2000], elevation_m: [100, 200, 100] },
      profile_quality: 'ok' as const,
      measures: { distance_m: 2000, ascent_m: 100, descent_m: 100, lowest_m: 100, highest_m: 200 },
      continuity: { pieces: 1, continuous: true },
      surface: { distribution: { asphalt: 0.5, ground: 0.5 }, dominant: 'asphalt' },
      difficulty: null,
      quality: null,
      places: [],
      attribution: 'OpenStreetMap, ODbL',
    };
    const view = render(<LoopCard loop={saved} expanded detail={detail} />);
    expect(view.container.querySelector('.profile .band')!.getAttribute('data-summary')).toBe(
      'true',
    );
    expect(view.container.querySelectorAll('.profile .band rect')).toHaveLength(2);
  });

  it('says a drawn route has no profile rather than pretending to fetch one', () => {
    const view = render(<LoopCard loop={{ ...DRAWN, profile: null }} expanded />);
    expect(view.container.textContent).toContain('No altitude profile');
    expect(view.container.textContent).not.toContain('Fetching');
  });
});

/** The thumbs on the route itself: the vote carries the route id, so "for
 *  this ask, that route did not belong" survives as a row of its own. */
describe('the open card asks whether the route was right', () => {
  const TURN = { messageId: 'm1', conversationId: 'c1' };

  it('says nothing without a turn — a saved route answers no question', () => {
    const view = render(<LoopCard loop={LOOP} expanded />);
    expect(view.queryByLabelText('Bad route')).toBeNull();
  });

  it('posts the vote against this route and asks what is wrong', () => {
    api.sendFeedback.mockClear();
    const view = render(<LoopCard loop={LOOP} expanded {...TURN} />);
    fireEvent.click(view.getByLabelText('Bad route'));
    expect(api.sendFeedback).toHaveBeenCalledWith(
      'm1',
      'c1',
      -1,
      undefined,
      undefined,
      'p1',
    );
    expect(view.getByLabelText("What's wrong?")).toBeTruthy();
  });

  it('forgets the vote when the same route arrives from another turn', () => {
    const view = render(<LoopCard loop={LOOP} expanded {...TURN} />);
    fireEvent.click(view.getByLabelText('Bad route'));
    expect(view.getByLabelText('Bad route').getAttribute('aria-pressed')).toBe('true');

    view.rerender(
      <LoopCard loop={LOOP} expanded messageId="m2" conversationId="c1" />,
    );
    expect(view.getByLabelText('Bad route').getAttribute('aria-pressed')).toBe('false');
    expect(view.queryByLabelText("What's wrong?")).toBeNull();
  });

  it('keeps its clicks and its typing off the card', () => {
    const onOpen = vi.fn();
    const onClose = vi.fn();
    const view = render(
      <LoopCard loop={LOOP} expanded onOpen={onOpen} onClose={onClose} {...TURN} />,
    );
    fireEvent.click(view.getByLabelText('Bad route'));
    const field = view.getByLabelText("What's wrong?");
    fireEvent.keyDown(field, { key: ' ' });
    expect(onOpen).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
