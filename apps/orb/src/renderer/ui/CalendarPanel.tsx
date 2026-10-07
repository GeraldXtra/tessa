import { useState } from 'react';

import type { CalendarToday } from '../../shared/ipc-contract.ts';
import { calendarStore, connectionStore, useStore } from '../state/store.ts';
import { tickStore } from '../state/tick.ts';
import { Loading, NoData } from './bits.tsx';
import { DAYS, MONTHS, hm, pad2 } from './format.ts';
import type { CalMode } from './layout.ts';

type Tab = 'today' | 'next' | 'due' | 'alarms';

const TABS: readonly (readonly [Tab, string])[] = [
  ['today', 'TODAY'],
  ['next', 'NEXT'],
  ['due', 'DUE'],
  ['alarms', 'ALARMS'],
];

const EMPTY: Readonly<Record<Exclude<Tab, 'today'>, string>> = {
  next: 'Needs the daemon. It only answers for today.',
  due: 'Deadlines are not built yet.',
  alarms: 'Alarms and timers are not built yet.',
};

function eventTime(iso: string, allDay: boolean): string {
  if (allDay) return 'ALL DAY';
  const t = Date.parse(iso);
  return Number.isFinite(t) ? hm(new Date(t)) : '';
}

function todayBody(cal: CalendarToday | null, linked: boolean) {
  if (cal === null) return linked ? <Loading rows={2} /> : <NoData why="No daemon link, so nothing has been asked for today." />;
  if (!cal.connected) {
    return <NoData why={`Google Calendar is not connected.${cal.reason ? ` (${cal.reason})` : ''}`} />;
  }
  if (cal.events.length === 0) return <div className="dim">No events today.</div>;
  return (
    <>
      {cal.events.slice(0, 3).map((e) => (
        <div key={e.id} className="row">
          <time>{eventTime(e.start, e.allDay)}</time>
          <span>{e.title}</span>
          <em />
        </div>
      ))}
      {cal.stale ? <div className="dim">Cached, {Math.round(cal.ageSeconds / 60)} min old.</div> : null}
    </>
  );
}

export function CalendarPanel({ mode }: { mode: CalMode }) {
  const now = new Date(useStore(tickStore));
  const cal = useStore(calendarStore);
  const linked = useStore(connectionStore).phase === 'connected';
  const [tab, setTab] = useState<Tab>('today');
  if (mode === 'hidden') return null;

  const y = now.getFullYear();
  const mo = now.getMonth();
  const td = now.getDate();
  const startDow = (new Date(y, mo, 1).getDay() + 6) % 7;
  const dim = new Date(y, mo + 1, 0).getDate();
  const prev = new Date(y, mo, 0).getDate();
  const hasToday = cal !== null && cal.connected && cal.events.length > 0;
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = i - startDow + 1;
    if (d < 1) cells.push(<span key={i} className="o">{prev + d}</span>);
    else if (d > dim) cells.push(<span key={i} className="o">{d - dim}</span>);
    else
      cells.push(
        <span key={i} className={`${d === td ? 'td' : ''}${d === td && hasToday ? ' ev' : ''}`} aria-current={d === td ? 'date' : undefined}>
          {d}
        </span>,
      );
  }
  const nextEvent = hasToday ? cal.events.find((e) => Date.parse(e.end || e.start) >= now.getTime()) ?? null : null;

  return (
    <section className={`cal br ${mode}`} aria-label="Calendar">
      <div className="top">
        <span className="lab">
          {MONTHS[mo]} {y}
        </span>
        <span className="lab">{cal?.connected ? `${cal.events.length} TODAY` : ''}</span>
      </div>
      <div className="big">
        {DAYS[now.getDay()]} {pad2(td)}
      </div>
      {mode === 'full' ? (
        <>
          <div className="grid" aria-hidden="true">
            {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((d, i) => (
              <span key={`h${i}`} className="h">
                {d}
              </span>
            ))}
            {cells}
          </div>
          <div className="tabs" role="tablist" aria-label="Calendar">
            {TABS.map(([k, label]) => (
              <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
                {label}
              </button>
            ))}
          </div>
          <div className={`list${cal !== null && !linked && tab === 'today' ? ' stale' : ''}`}>{tab === 'today' ? todayBody(cal, linked) : <NoData why={EMPTY[tab]} />}</div>
        </>
      ) : (
        <div className="nextline">
          {cal === null ? (
            <span className="dim">{linked ? 'LOADING' : 'NO DATA. No daemon link.'}</span>
          ) : !cal.connected ? (
            <span className="dim">NO DATA. Google Calendar is not connected.</span>
          ) : nextEvent ? (
            <>
              <span className="lab">NEXT</span> {eventTime(nextEvent.start, nextEvent.allDay)} {nextEvent.title}
            </>
          ) : (
            <span className="dim">No more events today.</span>
          )}
          <br />
          <span className="dim">TIMERS: NO DATA</span>
        </div>
      )}
    </section>
  );
}
