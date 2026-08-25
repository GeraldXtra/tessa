/**
 * The TODAY section under the calendar. Session 1's Google Calendar producer.
 *
 * ─── THREE STATES, AND THEY ARE THREE DIFFERENT FACTS ───
 * `res.calendar.today` distinguishes all three so this surface never has to
 * guess, and conflating any two of them would be a lie of exactly the kind this
 * project keeps refusing:
 *
 *   connected, events      render them
 *   connected, none        "no events today"  — a REAL answer, not an absence
 *   not connected          "not connected", and the reason
 *
 * Plus a fourth thing that is not a state: `stale` with `ageSeconds`, meaning
 * these are cached events served because the network is down. The panel says
 * how old they are rather than implying they are current.
 *
 * ─── EVENT TITLES ARE EXTERNAL CONTENT ───
 * CONTRACT §6.2 rates `external` the highest risk there is: fetched from off
 * this machine, written by whoever created the calendar entry. They are
 * rendered as inert text and nothing else — never a link, never a path, never
 * echoed into a command. Session 1's handler already audits anything its
 * injection detector fires on; this side's job is to make sure a title can only
 * ever be READ.
 */

import { useStore } from '../state/store.ts';
import { calendarStore } from '../state/store.ts';

function hhmm(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function Today() {
  const cal = useStore(calendarStore);

  return (
    <section className="today" aria-label="today">
      <h3 className="today__title">today</h3>

      {cal === null ? (
        // Not asked yet, or the daemon has not answered. Distinct from all
        // three real states and drawn as neither.
        <p className="panel-sec__nodata">NO DATA</p>
      ) : !cal.connected ? (
        <>
          <p className="today__off">not connected</p>
          {cal.reason ? <p className="panel-sec__why">{cal.reason}</p> : null}
        </>
      ) : cal.events.length === 0 ? (
        <p className="today__none">no events today</p>
      ) : (
        <ul className="today__list">
          {cal.events.slice(0, 4).map((e) => (
            <li key={e.id} className="today__ev">
              <span className="today__when num">{e.allDay ? 'all day' : hhmm(e.start)}</span>
              {/* Inert text. See the header — this string came from off the
                  machine and is never anything but characters on a screen. */}
              <span className="today__what">{e.title}</span>
            </li>
          ))}
        </ul>
      )}

      {cal?.connected && cal.stale ? (
        <p className="today__stale">
          cached · {Math.round(cal.ageSeconds / 60)}m old
        </p>
      ) : null}
    </section>
  );
}
