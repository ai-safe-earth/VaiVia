// @vitest-environment jsdom
/** The user half of the eval loop: thumbs render only on stored assistant
 *  turns, a vote posts on click, and a downvote asks what could be improved. */

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPanel } from '@/components/ChatPanel';
import type { ChatMessage } from '@/lib/types';

const api = vi.hoisted(() => ({
  fetchRouteGeoJson: vi.fn(async () => null),
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

const TRANSCRIPT: ChatMessage[] = [
  { role: 'user', content: 'a loop hike' },
  { role: 'assistant', content: 'Here is one.', messageId: 'm1' },
  { role: 'assistant', content: 'still arriving…', streaming: true },
];

function mount() {
  return render(
    <ChatPanel
      onGeometry={vi.fn()}
      initialConversationId="conv-1"
      initialMessages={TRANSCRIPT}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom has no scrollIntoView; the transcript calls it on every render.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

describe('Feedback', () => {
  it('renders thumbs only on stored, finished assistant turns', () => {
    const { container } = mount();
    // One stored turn with a messageId; the streaming turn gets none.
    expect(container.querySelectorAll('.feedback')).toHaveLength(1);
  });

  it('posts a vote on click and asks both questions on a downvote', async () => {
    const { getByLabelText } = mount();
    fireEvent.click(getByLabelText('Bad answer'));
    // The bare downvote carries the (still empty) texts, so a later re-tap
    // can never null what the form stored. The trailing undefined is the
    // route id: this vote is about the answer, not one card in it.
    expect(api.sendFeedback).toHaveBeenCalledWith(
      'm1',
      'conv-1',
      -1,
      undefined,
      undefined,
      undefined,
    );

    const wrong = getByLabelText("What's wrong?");
    fireEvent.change(wrong, { target: { value: 'wrong lake' } });
    fireEvent.change(getByLabelText('How should it be instead?'), {
      target: { value: 'the one by Lecco' },
    });
    fireEvent.submit(wrong.closest('form')!);
    // The submit chains behind the vote POST (ordering fix), so it lands a
    // microtask later.
    await waitFor(() =>
      expect(api.sendFeedback).toHaveBeenLastCalledWith(
        'm1',
        'conv-1',
        -1,
        'wrong lake',
        'the one by Lecco',
        undefined,
      ),
    );
  });

  it('an upvote posts without asking why', () => {
    const { getByLabelText, queryByLabelText } = mount();
    fireEvent.click(getByLabelText('Good answer'));
    expect(api.sendFeedback).toHaveBeenCalledWith(
      'm1',
      'conv-1',
      1,
      undefined,
      undefined,
      undefined,
    );
    expect(queryByLabelText("What's wrong?")).toBeNull();
  });
});
