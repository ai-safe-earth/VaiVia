/** The band a route line wears follows the exigent grade — same owner rule
 *  as the card squares: the hardest metre decides, never the character. */

import { describe, expect, it } from 'vitest';

import { loopBand, trailBand } from '@/lib/difficulty';

const hike = (sac_max: string | null) =>
  ({ activity: 'hike', sac_max, mtb_scale: null }) as Parameters<typeof loopBand>[0];
const mtb = (mtb_scale: string | null) =>
  ({ activity: 'mtb', sac_max: null, mtb_scale }) as Parameters<typeof loopBand>[0];

describe('loopBand', () => {
  it('bands SAC by the exigent grade', () => {
    expect(loopBand(hike('hiking'))).toBe('easy');
    expect(loopBand(hike('mountain_hiking'))).toBe('easy');
    expect(loopBand(hike('demanding_mountain_hiking'))).toBe('moderate');
    expect(loopBand(hike('alpine_hiking'))).toBe('moderate');
    expect(loopBand(hike('demanding_alpine_hiking'))).toBe('hard');
    expect(loopBand(hike('difficult_alpine_hiking'))).toBe('hard');
    expect(loopBand(hike(null))).toBe('ungraded');
    expect(loopBand(hike('not_a_grade'))).toBe('ungraded');
  });

  it('bands MTB by S-scale', () => {
    expect(loopBand(mtb('0'))).toBe('easy');
    expect(loopBand(mtb('1'))).toBe('easy');
    expect(loopBand(mtb('2'))).toBe('moderate');
    expect(loopBand(mtb('3'))).toBe('moderate');
    expect(loopBand(mtb('4'))).toBe('hard');
    expect(loopBand(mtb(null))).toBe('ungraded');
  });
});

describe('trailBand', () => {
  it('bands the 1-4 difficulty_level', () => {
    expect(trailBand(1)).toBe('easy');
    expect(trailBand(2)).toBe('moderate');
    expect(trailBand(3)).toBe('hard');
    expect(trailBand(4)).toBe('hard');
    expect(trailBand(null)).toBe('ungraded');
  });
});
