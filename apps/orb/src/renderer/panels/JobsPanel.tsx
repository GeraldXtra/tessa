/**
 * JOBS — the job list.
 *
 * The statuses and tiers below are typed against `JobStatus` and `Tier` from
 * @zoey/protocol rather than being free strings. Both are CLOSED sets
 * (CONTRACT §7.4), so a placeholder that used an undefined status would fail to
 * compile — which is the point of importing them for fake data too.
 *
 * Two of the statuses are worth showing precisely because they are easy to get
 * wrong: `blocked` means an approval is outstanding and the job is still live,
 * while `needsReview` means the 30-minute approval window lapsed unanswered
 * (spec §5 rule 5). Neither is `failed`, and neither is `cancelled`.
 */

import type { JobStatus, Tier } from '@zoey/protocol';

import { Placeholder } from './Placeholder.tsx';

interface JobRow {
  id: string;
  title: string;
  status: JobStatus;
  tier: Tier;
  note: string;
}

const JOBS: readonly JobRow[] = [
  {
    id: 'j1',
    title: 'Index project metadata',
    status: 'running',
    tier: 'green',
    note: 'step 2 of 4',
  },
  {
    id: 'j2',
    title: 'Move invoices to archive',
    status: 'blocked',
    tier: 'amber',
    note: 'awaiting your approval',
  },
  {
    id: 'j3',
    title: 'Draft reply to supplier',
    status: 'needsReview',
    tier: 'red',
    note: 'approval window lapsed',
  },
  {
    id: 'j4',
    title: 'Nightly audit chain verify',
    status: 'succeeded',
    tier: 'green',
    note: '312 entries, chain intact',
  },
  {
    id: 'j5',
    title: 'Fetch model weights',
    status: 'queued',
    tier: 'amber',
    note: 'metered connection — deferred',
  },
];

export function JobsPanel() {
  return (
    <>
      <Placeholder note="Sample jobs. Live data arrives with the scheduler in Phase 5." />

      <ul className="list">
        {JOBS.map((job) => (
          <li key={job.id} className="list__row list__row--stacked">
            <span className="list__primary">{job.title}</span>
            <span className="list__tags">
              <span className="chip chip--job" data-status={job.status}>
                {job.status}
              </span>
              <span className="chip chip--tier" data-tier={job.tier}>
                {job.tier}
              </span>
            </span>
            <span className="list__meta">{job.note}</span>
          </li>
        ))}
      </ul>

      <p className="panel-footnote">
        Tiers are rendered here, never evaluated. The daemon is the only authority on what green,
        amber and red permit — CONTRACT §6.4.
      </p>
    </>
  );
}
