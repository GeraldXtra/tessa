/**
 * The 48px rail. §R.7, "Rail tabs".
 *
 * Type only — no icons, which §R.7 calls "the whole aesthetic". The vertical
 * rotation, tracking, the three resting/hover/active colours and the 2px active
 * marker on the inner edge are all in app.css; this file owns only which rails
 * exist, in what order, and which one is open.
 *
 * SENTINEL is the one rail that can carry a status colour, and it carries it
 * WHETHER OR NOT its drawer is open — §R.7: "a threat is never hidden behind a
 * closed panel". The mechanism is live below; its input is null until Defender
 * lands, so nothing is lit. See rails/sentinel-status.ts for why that is
 * deliberate rather than unfinished.
 */

import { RAILS } from '../rails/rails.tsx';
import { currentSentinelSource, sentinelStatus } from '../rails/sentinel-status.ts';
import { railStore, useStore } from '../state/store.ts';

export function Rail() {
  const open = useStore(railStore);
  const sentinel = sentinelStatus(currentSentinelSource());

  return (
    <nav className="rail" aria-label="Rails">
      {RAILS.map((rail) => {
        const active = open === rail.id;
        return (
          <button
            key={rail.id}
            type="button"
            className="rail__item"
            // Stable handle for the dev driver. Selecting rails by nth-child
            // breaks the moment the order changes, and §R.3 fixes the order but
            // not forever.
            data-rail-id={rail.id}
            data-active={active}
            // Only ever set on SENTINEL, and only when a real security source
            // says so. `undefined` leaves the rail at --text-muted like the
            // others rather than adding a fourth, quieter status colour.
            data-status={rail.id === 'sentinel' && sentinel ? sentinel : undefined}
            aria-pressed={active}
            title={rail.answers}
            onClick={() => railStore.set(active ? null : rail.id)}
          >
            <span className="rail__label">{rail.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
