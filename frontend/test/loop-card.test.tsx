// @vitest-environment jsdom
/**
 * The card the map layer's route panel renders.
 *
 * It is the SAME component as the transcript's card, given two things the
 * transcript never gives it: no onSelect (it is already the selection, so
 * there is nothing to pick) and defaultOpen (the tap that raised the panel
 * was the ask for the numbers). Both are pinned here because a card that
 * silently stays shut, or that stays a focusable button doing nothing, looks
 * fine in every other test.
 */

import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LoopCard } from '@/components/LoopCard';
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

afterEach(cleanup);

describe('the route panel card', () => {
  it('opens its detail on arrival when defaultOpen is set', () => {
    const view = render(<LoopCard loop={LOOP} selected defaultOpen />);
    expect(view.container.querySelector('.route-detail')).toBeTruthy();
  });

  it('stays shut in the transcript, where a tap is what opens it', () => {
    const view = render(<LoopCard loop={LOOP} selected onSelect={() => undefined} />);
    expect(view.container.querySelector('.route-detail')).toBeNull();
  });

  it('is not a button without an onSelect: nothing to press, nothing to focus', () => {
    const view = render(<LoopCard loop={LOOP} selected defaultOpen />);
    const card = view.container.querySelector('[data-route-id="p1"]')!;
    expect(card.getAttribute('role')).toBeNull();
    expect(card.getAttribute('tabindex')).toBeNull();
  });
});

/** The thumbs on the route itself: the vote carries the route id, so "for
 *  this ask, that route did not belong" survives as a row of its own. */
describe('the open card asks whether the route was right', () => {
  const TURN = { messageId: 'm1', conversationId: 'c1' };

  it('says nothing without a turn — a saved route answers no question', () => {
    const view = render(<LoopCard loop={LOOP} selected defaultOpen />);
    expect(view.queryByLabelText('Bad route')).toBeNull();
  });

  it('posts the vote against this route and asks what is wrong', () => {
    api.sendFeedback.mockClear();
    const view = render(<LoopCard loop={LOOP} selected defaultOpen {...TURN} />);
    fireEvent.click(view.getByLabelText('Bad route'));
    expect(api.sendFeedback).toHaveBeenCalledWith(
      'm1',
      'c1',
      -1,
      undefined,
      undefined,
      'p1',
    );
    // The standard follow-up, same as the answer's: what's wrong, and what
    // it should have been.
    expect(view.getByLabelText("What's wrong?")).toBeTruthy();
  });

  it('forgets the vote when the same route arrives from another turn', () => {
    // The map panel's card is keyed by route id, so a route offered by two
    // answers reuses this instance — and a vote carried over would show as
    // cast on a turn nobody judged, then post that turn's stale comment.
    const view = render(<LoopCard loop={LOOP} selected defaultOpen {...TURN} />);
    fireEvent.click(view.getByLabelText('Bad route'));
    expect(view.getByLabelText('Bad route').getAttribute('aria-pressed')).toBe('true');

    view.rerender(
      <LoopCard loop={LOOP} selected defaultOpen messageId="m2" conversationId="c1" />,
    );
    expect(view.getByLabelText('Bad route').getAttribute('aria-pressed')).toBe('false');
    expect(view.queryByLabelText("What's wrong?")).toBeNull();
  });

  it('keeps its clicks and its typing off the card that raises the map', () => {
    const onSelect = vi.fn();
    const view = render(
      <LoopCard loop={LOOP} selected defaultOpen onSelect={onSelect} {...TURN} />,
    );
    fireEvent.click(view.getByLabelText('Bad route'));
    const field = view.getByLabelText("What's wrong?");
    fireEvent.keyDown(field, { key: ' ' });
    expect(onSelect).not.toHaveBeenCalled();
  });
});
