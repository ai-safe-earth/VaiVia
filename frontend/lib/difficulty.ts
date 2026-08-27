/**
 * Difficulty banding for the map: which of the palette's data-encoding
 * colours a route line wears (lime = easy, amber = moderate, flare = hard,
 * muted = ungraded — BRAND-SPEC amendment 2026-08-27).
 *
 * The band follows the EXIGENT grade (sac_max / mtb_scale), the same owner
 * rule the card squares follow: on anything user-facing the grade is a
 * safety promise, and "a T2 walk with a T4 move" is a T4.
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

export type DifficultyBand = 'easy' | 'moderate' | 'hard' | 'ungraded';

function bandOfRank(rank: number): DifficultyBand {
  if (rank <= 0) return 'ungraded';
  if (rank <= 2) return 'easy';
  if (rank <= 4) return 'moderate';
  return 'hard';
}

/** T1–T2 / S0–S1 easy, T3–T4 / S2–S3 moderate, T5–T6 / S4+ hard. */
export function loopBand(
  loop: Pick<Loop, 'activity' | 'sac_max' | 'mtb_scale'>,
): DifficultyBand {
  if (loop.activity === 'mtb') {
    if (loop.mtb_scale === null || loop.mtb_scale === undefined) return 'ungraded';
    const s = Number(loop.mtb_scale);
    if (Number.isNaN(s)) return 'ungraded';
    if (s <= 1) return 'easy';
    if (s <= 3) return 'moderate';
    return 'hard';
  }
  const rank = loop.sac_max ? SAC_ORDER.indexOf(loop.sac_max) + 1 : 0;
  return bandOfRank(rank);
}

/** Trails carry a 1–4 difficulty_level rather than a SAC grade. */
export function trailBand(level: number | null | undefined): DifficultyBand {
  if (level == null) return 'ungraded';
  if (level <= 1) return 'easy';
  if (level <= 2) return 'moderate';
  return 'hard';
}
