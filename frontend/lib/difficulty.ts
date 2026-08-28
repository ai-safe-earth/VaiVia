/**
 * Difficulty banding for the map: which of the palette's data-encoding
 * colours a route line wears (lime = easy, amber = moderate, flare = hard,
 * muted = ungraded — BRAND-SPEC amendment 2026-08-27). The bands are
 * hike-only: every mtb route is the 'mtb' band (near-black --vv-ground),
 * encoding activity rather than grade (owner decision 2026-08-27).
 *
 * The hike band follows the EXIGENT grade (sac_max), the same owner rule the
 * card squares follow: on anything user-facing the grade is a safety
 * promise, and "a T2 walk with a T4 move" is a T4.
 */

import type { Loop } from './types';

/** SAC grades in catalogue order — index+1 is the rank the squares fill to. */
export const SAC_ORDER = [
  'hiking',
  'mountain_hiking',
  'demanding_mountain_hiking',
  'alpine_hiking',
  'demanding_alpine_hiking',
  'difficult_alpine_hiking',
];

export type DifficultyBand = 'easy' | 'moderate' | 'hard' | 'ungraded' | 'mtb';

function bandOfRank(rank: number): DifficultyBand {
  if (rank <= 0) return 'ungraded';
  if (rank <= 2) return 'easy';
  if (rank <= 4) return 'moderate';
  return 'hard';
}

/** T1–T2 easy, T3–T4 moderate, T5–T6 hard; every mtb route is 'mtb' —
 *  activity, not grade, is what the map encodes for bikes (owner decision
 *  2026-08-27). */
export function loopBand(
  loop: Pick<Loop, 'activity' | 'sac_max'>,
): DifficultyBand {
  if (loop.activity === 'mtb') return 'mtb';
  const rank = loop.sac_max ? SAC_ORDER.indexOf(loop.sac_max) + 1 : 0;
  return bandOfRank(rank);
}

/** Trails carry a 1–4 difficulty_level rather than a SAC grade; an mtb trail
 *  is 'mtb' like every mtb loop ('mixed' stays level-banded). */
export function trailBand(
  level: number | null | undefined,
  activity?: string | null,
): DifficultyBand {
  if (activity === 'mtb') return 'mtb';
  if (level == null) return 'ungraded';
  if (level <= 1) return 'easy';
  if (level <= 2) return 'moderate';
  return 'hard';
}
