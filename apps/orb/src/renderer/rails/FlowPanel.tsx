/**
 * FLOW — "What is it doing for me?" (§R.3)
 *
 * Shell only, and every section empty. §R.3 wants running jobs, scheduled jobs,
 * triggers, calendar and agenda, the overnight queue, the morning digest, and
 * alarms. The daemon implements none of it: there is no `job.*` traffic, no
 * scheduler, and no calendar. The scheduler is P5.
 *
 * The August 2026 calendar grid that used to live in the old AGENDA drawer was
 * invented, and it is gone rather than ported. A drawn calendar with no source
 * is the most convincing kind of fabricated data, because a calendar looks
 * correct whether or not anything is behind it.
 */

import { NoData, Section } from './primitives.tsx';

export function FlowPanel() {
  return (
    <>
      <Section title="Running">
        <NoData />
      </Section>
      <Section title="Scheduled">
        <NoData />
      </Section>
      <Section title="Triggers">
        <NoData />
      </Section>
      <Section title="Agenda">
        <NoData />
      </Section>
      <Section title="Overnight queue">
        <NoData />
      </Section>
    </>
  );
}
