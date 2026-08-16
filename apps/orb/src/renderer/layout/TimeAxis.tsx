/**
 * The time axis. One mark per audit entry over the last few minutes, positioned
 * by timestamp and coloured by tier.
 *
 * Nothing on this surface has ever answered "what has been happening?" — the
 * state light says what she is doing NOW and the transcript says what was said,
 * but the shape of the last few minutes has never been visible anywhere.
 *
 * ─── the failure mode, designed for rather than discovered ───
 * A freshly started daemon writes a burst of audit entries and then goes quiet.
 * The honest rendering of that is a clump on the right and a long flat run —
 * which is TRUE and looks exactly like a broken chart. So the window duration
 * is drawn explicitly, with tick labels: a flat stretch under a ruler that says
 * `-3m -2m -1m` reads as "nothing happened then", which is the fact. Without
 * the ruler it reads as "this widget is broken", which is not.
 *
 * ─── degrades to nothing ───
 * With no entries in the window, the whole component — ruler included — returns
 * null. The ruler exists to explain marks; a ruler with nothing under it is an
 * empty box, and this composition does not have those.
 *
 * ─── it is not animated ───
 * Positions are recomputed on the 1 Hz tick, so marks drift left one pixel or
 * so a second as the window slides. That is telemetry, not motion: no
 * transition, no keyframe, and §R.7's rule that nothing on the rails animates
 * continuously is untouched — this is not on a rail, and it does not animate,
 * it updates.
 */

import { auditStore, useStore } from '../state/store.ts';
import { tickStore } from '../state/tick.ts';

/** How much history the axis shows. Narrows on a narrow window — see App. */
export const AXIS_WINDOW_MS = 3 * 60_000;

interface TimeAxisProps {
  /** Shorter windows on narrow displays, so marks do not overlap into a smear. */
  windowMs?: number;
}

export function TimeAxis({ windowMs = AXIS_WINDOW_MS }: TimeAxisProps) {
  const audit = useStore(auditStore);
  const now = useStore(tickStore);

  const since = now - windowMs;
  const marks = audit
    .map((e) => ({ at: Date.parse(e.ts), tier: e.tier, tool: e.tool }))
    .filter((m) => Number.isFinite(m.at) && m.at >= since && m.at <= now);

  // No marks, no ruler, no node. A ruler over nothing is the empty box this
  // whole composition exists to avoid.
  if (marks.length === 0) return null;

  const minutes = Math.round(windowMs / 60_000);
  const ticks = Array.from({ length: minutes }, (_, i) => i + 1);

  return (
    <div className="axis" aria-label={`Activity over the last ${minutes} minutes`}>
      <div className="axis__track">
        {marks.map((m, i) => (
          <span
            key={`${m.at}-${i}`}
            className="axis__mark"
            data-tier={m.tier}
            style={{ left: `${(((m.at - since) / windowMs) * 100).toFixed(3)}%` }}
            title={m.tool}
          />
        ))}
      </div>
      <div className="axis__ruler">
        {ticks.map((t) => (
          <span
            key={t}
            className="axis__tick"
            style={{ left: `${(100 - (t / minutes) * 100).toFixed(2)}%` }}
          >
            −{t}m
          </span>
        ))}
        <span className="axis__count">
          {marks.length} in {minutes}m
        </span>
      </div>
    </div>
  );
}
