'use client';

import { hazard } from '@/lib/format';

/**
 * A 6px flare bar and a calm sentence. No icon, no bold, no exclamation: the
 * bar carries the urgency so the words do not have to.
 *
 * The graph gives hazard keys, not dated observations, so the sentence says
 * exactly that much and no more — claiming a date we do not have would be the
 * one failure this block exists to prevent.
 */
export function Hazard({ hazards }: { hazards: string[] }) {
  if (hazards.length === 0) return null;
  const listed = hazards.map(hazard).join(', ');

  return (
    <div className="vv-hazard">
      <div className="vv-hazard-bar" />
      <div className="vv-hazard-body">
        <span className="vv-label vv-label-hazard">Before you go</span>
        <p className="vv-body">
          Reported on this route: {listed}. The reports are crowd-sourced and carry no
          date.
        </p>
      </div>
    </div>
  );
}
