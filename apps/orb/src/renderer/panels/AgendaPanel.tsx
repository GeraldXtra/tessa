/**
 * AGENDA — activity counter, companion status, calendar strip, agenda list.
 *
 * Spec §8 lists these as the Orb's left panel. In the collapsed layout they are
 * one drawer instead of a permanent 240px column (spec §8.1).
 *
 * All data here is invented. The companion state values are taken from
 * AGENT_STATES so that even the placeholder cannot use a state the contract
 * does not define.
 */

import { AGENT_STATES, type AgentState } from '@zoey/protocol';

import { Placeholder } from './Placeholder.tsx';

const COMPANIONS: readonly { name: string; state: AgentState; scope: string }[] = [
  { name: 'Zoey', state: 'idle', scope: 'primary' },
  { name: 'Atlas', state: 'working', scope: 'research' },
];

const WEEK: readonly { day: string; date: string; today: boolean }[] = [
  { day: 'M', date: '10', today: false },
  { day: 'T', date: '11', today: false },
  { day: 'W', date: '12', today: true },
  { day: 'T', date: '13', today: false },
  { day: 'F', date: '14', today: false },
  { day: 'S', date: '15', today: false },
  { day: 'S', date: '16', today: false },
];

const AGENDA: readonly { time: string; title: string }[] = [
  { time: '09:30', title: 'Contract review — protocol v1 sign-off' },
  { time: '13:00', title: 'Orb frame-budget benchmark' },
  { time: '18:45', title: 'Overnight index — OneDrive metadata only' },
];

export function AgendaPanel() {
  return (
    <>
      <Placeholder note="Sample agenda. Live data arrives with the calendar events in Phase 6." />

      <section className="panel-section">
        <h3 className="panel-label">Activity</h3>
        <p className="counter">
          <span className="counter__value">4</span>
          <span className="counter__unit">actions today</span>
        </p>
      </section>

      <section className="panel-section">
        <h3 className="panel-label">Companions</h3>
        <ul className="list">
          {COMPANIONS.map((companion) => (
            <li key={companion.name} className="list__row">
              <span className="list__primary">{companion.name}</span>
              <span className="chip" data-state={companion.state}>
                {companion.state}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel-section">
        <h3 className="panel-label">Week</h3>
        <ol className="week">
          {WEEK.map((entry) => (
            <li key={entry.date} className="week__day" data-today={entry.today}>
              <span className="week__name">{entry.day}</span>
              <span className="week__date">{entry.date}</span>
            </li>
          ))}
        </ol>
      </section>

      <section className="panel-section">
        <h3 className="panel-label">Agenda</h3>
        <ul className="list">
          {AGENDA.map((item) => (
            <li key={item.time} className="list__row list__row--stacked">
              <span className="list__meta">{item.time}</span>
              <span className="list__primary">{item.title}</span>
            </li>
          ))}
        </ul>
      </section>

      <p className="panel-footnote">
        {AGENT_STATES.length} agent states defined by the contract; the chips above use them
        directly.
      </p>
    </>
  );
}
