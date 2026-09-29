import { describe, expect, it } from 'vitest';

import { cardKey, closeCard, MAX_OPEN, openCard } from '../lib/openCards';

describe('cards open in place, three at most', () => {
  it('opens in order, oldest first', () => {
    expect(openCard(openCard([], 'a'), 'b')).toEqual(['a', 'b']);
  });

  it('reopening an open card changes nothing', () => {
    expect(openCard(['a', 'b'], 'a')).toEqual(['a', 'b']);
  });

  it('one too many closes the oldest', () => {
    expect(MAX_OPEN).toBe(3);
    expect(openCard(['a', 'b', 'c'], 'd')).toEqual(['b', 'c', 'd']);
  });

  it('closes only the card asked', () => {
    expect(closeCard(['a', 'b', 'c'], 'b')).toEqual(['a', 'c']);
    expect(closeCard(['a'], 'zzz')).toEqual(['a']);
  });

  it('keys a card by the answer that offered it, so one route can be open twice', () => {
    expect(cardKey(1, 'r')).not.toBe(cardKey(3, 'r'));
  });
});
