/**
 * SAC grades in catalogue order — the rank the card's difficulty squares
 * fill to.
 *
 * Colour is not a difficulty encoding: every route line is --vv-lime and
 * selection is width and opacity (BRAND-SPEC amendment 2026-08-28), so the
 * band ramp this module used to hold — lime/amber/flare/muted, mtb
 * near-black — is gone with it.
 */

/** SAC grades in catalogue order — index+1 is the rank the squares fill to. */
export const SAC_ORDER = [
  'hiking',
  'mountain_hiking',
  'demanding_mountain_hiking',
  'alpine_hiking',
  'demanding_alpine_hiking',
  'difficult_alpine_hiking',
];
