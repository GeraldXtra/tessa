import type { JobView } from '../shared/ipc-contract.ts';

export type JobEvent =
  | { kind: 'created'; jobId: string; title: string; steps: number }
  | { kind: 'progress'; jobId: string; stepIndex: number | null; pct: number | null }
  | { kind: 'updated'; jobId: string; status: string; stepIndex: number | null }
  | { kind: 'completed'; jobId: string; status: string };

const TERMINAL: ReadonlySet<string> = new Set(['succeeded', 'failed', 'cancelled', 'needsReview']);
export const KEEP_TERMINAL_MS = 2500;
const MAX_ROWS = 32;

interface Row {
  jobId: string;
  title: string;
  status: string;
  steps: number;
  stepIndex: number;
  pct: number | null;
  endedAt: number | null;
}

function progressOf(row: Row): number {
  if (row.status === 'succeeded') return 1;
  if (row.pct !== null) return Math.max(0, Math.min(1, row.pct / 100));
  if (row.steps > 0) return Math.max(0, Math.min(1, row.stepIndex / row.steps));
  return 0;
}

export class JobTable {
  private readonly rows = new Map<string, Row>();

  apply(event: JobEvent, now: number): void {
    let row = this.rows.get(event.jobId);
    if (!row) {
      row = { jobId: event.jobId, title: '', status: 'queued', steps: 0, stepIndex: 0, pct: null, endedAt: null };
      this.rows.set(event.jobId, row);
      if (this.rows.size > MAX_ROWS) {
        const oldest = this.rows.keys().next().value;
        if (oldest !== undefined) this.rows.delete(oldest);
      }
    }
    if (event.kind === 'created') {
      row.title = event.title;
      row.steps = event.steps;
    } else if (event.kind === 'progress') {
      if (event.stepIndex !== null) row.stepIndex = event.stepIndex;
      if (event.pct !== null) row.pct = event.pct;
      if (row.status === 'queued') row.status = 'running';
    } else {
      row.status = event.status;
      if (event.kind === 'updated' && event.stepIndex !== null) row.stepIndex = event.stepIndex;
    }
    if (TERMINAL.has(row.status) && row.endedAt === null) row.endedAt = now;
  }

  view(now: number): JobView[] {
    const out: JobView[] = [];
    for (const [id, row] of this.rows) {
      if (row.endedAt !== null && now - row.endedAt > KEEP_TERMINAL_MS) {
        this.rows.delete(id);
        continue;
      }
      out.push({ jobId: row.jobId, title: row.title, status: row.status, progress: progressOf(row) });
    }
    return out;
  }

  clear(): void {
    this.rows.clear();
  }
}
