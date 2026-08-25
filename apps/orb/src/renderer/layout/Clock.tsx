/**
 * The reference's top-right clock.
 *
 * Real without a producer, like the calendar grid: the time is a fact this
 * process owns. Driven from the shared 1 Hz tick so it lands on the same edge
 * as every other derived value rather than holding a sixteenth timer.
 *
 * The CALM pill beside it in the reference is NOT built — see the report. No
 * value in `core/` maps to it.
 */

import { useStore } from '../state/store.ts';
import { tickStore } from '../state/tick.ts';

export function Clock() {
  const now = new Date(useStore(tickStore));
  const hh = String(now.getHours()).padStart(2, '0');
  const mm = String(now.getMinutes()).padStart(2, '0');
  return (
    <div className="clock num" aria-label="time">
      {hh}
      <span className="clock__sep">:</span>
      {mm}
    </div>
  );
}
