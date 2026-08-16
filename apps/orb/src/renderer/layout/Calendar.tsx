/**
 * The left panel — the current month, with today marked.
 *
 * ─── why a calendar is not fabricated data ───
 * Nothing else on this surface is allowed to show a value the daemon has not
 * sent, and that rule is why the reference's events list is NOT built here: the
 * reference showed "Daily Report", "Trending", "Aug 9 Ca…" under its month grid,
 * and every one of those is an invention. There is no calendar producer in
 * `core/`, so there are no events to show.
 *
 * A month grid is a different kind of statement. "August 2026 has 31 days, the
 * 16th is a Sunday, and today is the 16th" is derived entirely from the clock,
 * which this process already owns and which is true without anybody asserting
 * it. It needs no producer because it makes no claim about Zoey — it is the
 * same category of fact as the uptime counter beside it.
 *
 * ─── what it is FOR, which is the honest part ───
 * On its own a month grid is decoration, and it is worth saying so rather than
 * letting it look like a feature. It earns its place by being the anchor the
 * events list attaches to WHEN a calendar producer exists — at which point the
 * grid is already built, already correct, and already placed. Until then it
 * says the date, which is the one thing a glanceable always-on surface at 2am
 * genuinely should say, and it says it in a shape a person reads faster than a
 * line of text.
 *
 * ─── the rollover ───
 * Driven from `tickStore`, so at midnight the highlight moves on its own. A
 * calendar computed once at mount is wrong for as long as the app stays up,
 * which on an always-on surface is the normal case rather than the edge one.
 */

import { tickStore } from '../state/tick.ts';
import { useStore } from '../state/store.ts';

const WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'] as const;

const MONTHS = [
  'JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE',
  'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER',
] as const;

/**
 * The grid, as a flat list of day numbers with nulls for the leading and
 * trailing blanks.
 *
 * Six rows always, never five. A month that starts late needs six and a month
 * that starts early needs five, and letting the row count vary makes the panel
 * change height on the 1st of some months — which moves everything below it on
 * a surface whose whole point is that it sits still.
 */
function monthGrid(year: number, month: number): (number | null)[] {
  const firstWeekday = new Date(year, month, 1).getDay();
  // Day 0 of the NEXT month is the last day of this one. Handles leap years
  // without a leap-year rule, which is the only way to get them right.
  const days = new Date(year, month + 1, 0).getDate();
  const cells: (number | null)[] = [];
  for (let i = 0; i < firstWeekday; i++) cells.push(null);
  for (let d = 1; d <= days; d++) cells.push(d);
  while (cells.length < 42) cells.push(null);
  return cells;
}

export function Calendar() {
  const now = new Date(useStore(tickStore));
  const year = now.getFullYear();
  const month = now.getMonth();
  const today = now.getDate();
  const cells = monthGrid(year, month);

  return (
    <section className="cal" aria-label="calendar">
      <h2 className="cal__month">
        {MONTHS[month]} <span className="cal__year">{year}</span>
      </h2>

      <div className="cal__grid" role="grid">
        {WEEKDAYS.map((d, i) => (
          // The index is part of the key on purpose: 'S' and 'T' each appear
          // twice in a week and a bare letter is not a unique key.
          <span key={`${d}${i}`} className="cal__dow" role="columnheader">
            {d}
          </span>
        ))}
        {cells.map((d, i) =>
          d === null ? (
            <span key={`b${i}`} className="cal__blank" />
          ) : (
            <span
              key={d}
              role="gridcell"
              className="cal__day"
              data-today={d === today ? 'true' : undefined}
              aria-current={d === today ? 'date' : undefined}
            >
              {d}
            </span>
          ),
        )}
      </div>
    </section>
  );
}
