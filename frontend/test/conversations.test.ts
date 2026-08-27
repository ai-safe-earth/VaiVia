/** Resume hydration: stored result_refs -> the same cards a live answer had. */
import { describe, expect, it } from 'vitest';

import { toChatMessages } from '@/lib/conversations';
import type { Loop } from '@/lib/types';

const loop = (id: string): Loop => ({ id, name: id, activity: 'hiking' }) as Loop;

const ROWS = [
  { role: 'user', content: 'a loop hike' },
  {
    role: 'assistant',
    content: 'Here are two.',
    result_refs: { loop_ids: ['a', 'b'] },
  },
  { role: 'system', content: 'plumbing' },
  { role: 'assistant', content: 'No refs on this turn.' },
];

describe('toChatMessages', () => {
  it('rehydrates cards in stored ref order and keeps prose-only turns', () => {
    const byId = new Map([
      ['b', loop('b')],
      ['a', loop('a')],
    ]);
    const messages = toChatMessages(ROWS, byId);
    expect(messages).toHaveLength(3); // system turn dropped
    expect(messages[1].results?.loops?.map((l) => l.id)).toEqual(['a', 'b']);
    expect(messages[2].results).toBeUndefined();
  });

  it('an id that left the catalogue drops out of the turn, never the turn itself', () => {
    const byId = new Map([['a', loop('a')]]);
    const messages = toChatMessages(ROWS, byId);
    expect(messages[1].results?.loops?.map((l) => l.id)).toEqual(['a']);
    expect(messages[1].content).toBe('Here are two.');
  });

  it('with no hydration the transcript is prose-only, not empty', () => {
    const messages = toChatMessages(ROWS);
    expect(messages).toHaveLength(3);
    expect(messages[1].results).toBeUndefined();
  });
});
