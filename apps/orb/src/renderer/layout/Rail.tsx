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
 *
 * ─── WHAT HAPPENS IF HE OPENS A RAIL WHILE AN APPROVAL IS PENDING ───
 * The rail REFUSES, visibly, and says why.
 *
 * The two rules he set collide here and something has to give. "One thing on
 * the right at a time" and "an approval card closes any open drawer" together
 * mean the card owns that column while it exists; but a pending red-tier action
 * MUST NOT be dismissable by opening a panel, so the drawer cannot simply
 * replace it. The three ways out are: dismiss the card (unacceptable — that is
 * a decision made by a stray click on an unrelated panel), stack the drawer
 * beside the card (both fit at 1366 only by cutting the sphere to a 480 px
 * column, at the one moment he most needs to read her state), or refuse.
 *
 * Refusing is the only one that keeps both rules. It is made VISIBLE rather
 * than silent — the items go to `disabled` with the reason in their tooltip and
 * the rail carries a one-word note — because a dead click with no explanation
 * is indistinguishable from a broken build. The block lifts the moment the card
 * is answered, and the drawer stays closed, which is what he asked for.
 */

import { RAILS } from '../rails/rails.tsx';
import { currentSentinelSource, sentinelStatus } from '../rails/sentinel-status.ts';
import { railStore, useStore } from '../state/store.ts';

interface RailProps {
  /** An approval is pending. The rails refuse to open — see the note above. */
  blocked?: boolean;
}

export function Rail({ blocked = false }: RailProps) {
  const open = useStore(railStore);
  const sentinel = sentinelStatus(currentSentinelSource());

  return (
    <nav className="rail" aria-label="Rails" data-blocked={blocked || undefined}>
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
            disabled={blocked}
            title={
              blocked
                ? 'Answer the pending approval first — a red-tier request may ' +
                  'not be dismissed by opening a panel'
                : rail.answers
            }
            onClick={() => railStore.set(active ? null : rail.id)}
          >
            <span className="rail__label">{rail.label}</span>
          </button>
        );
      })}
      {/* Said, not merely done. A disabled control with no reason on screen is
          indistinguishable from a broken one. */}
      {blocked ? <span className="rail__blocked">APPROVAL PENDING</span> : null}
    </nav>
  );
}
