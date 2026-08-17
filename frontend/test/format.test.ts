import { describe, expect, it } from 'vitest';

import { distance, duration, hazard, poiLabel, primaryDuration } from '../lib/format';

describe('distance', () => {
  it('shows metres below a kilometre', () => {
    expect(distance(850)).toBe('850 m');
  });

  it('shows kilometres with one decimal above', () => {
    expect(distance(12400)).toBe('12.4 km');
  });

  it('does not round a short trail up to 0.0 km', () => {
    expect(distance(120)).toBe('120 m');
  });
});

describe('duration', () => {
  it('formats minutes only', () => {
    expect(duration(45)).toBe('45 min');
  });

  it('formats whole hours without stray minutes', () => {
    expect(duration(120)).toBe('2 h');
  });

  it('formats hours and minutes', () => {
    expect(duration(252)).toBe('4 h 12 min');
  });

  it('returns null for missing or zero durations', () => {
    expect(duration(null)).toBeNull();
    expect(duration(0)).toBeNull();
  });
});

describe('primaryDuration', () => {
  it('uses the ride time for mtb trails', () => {
    expect(
      primaryDuration({ activity: 'mtb', duration_hike_min: 300, duration_mtb_min: 88 }),
    ).toEqual({ label: 'ride', value: '1 h 28 min' });
  });

  it('uses the walk time for hiking trails', () => {
    expect(
      primaryDuration({ activity: 'hike', duration_hike_min: 66, duration_mtb_min: null }),
    ).toEqual({ label: 'walk', value: '1 h 6 min' });
  });

  it('returns null when the relevant duration is missing', () => {
    expect(
      primaryDuration({ activity: 'mtb', duration_hike_min: 60, duration_mtb_min: null }),
    ).toBeNull();
  });
});

describe('labels', () => {
  it('humanises known hazard and poi keys', () => {
    expect(hazard('mud_after_rain')).toBe('mud after rain');
    expect(poiLabel('bathing_water')).toBe('swimming spot');
    expect(poiLabel('hut')).toBe('mountain hut');
  });

  it('falls back gracefully for unknown keys', () => {
    expect(hazard('falling_coconuts')).toBe('falling coconuts');
    expect(poiLabel('space_elevator')).toBe('space elevator');
  });
});
