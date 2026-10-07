import { useMemo } from 'react';

import { auditStore, railStore, transcriptStore, useStore, type RailId } from '../state/store.ts';
import { jobsStore } from '../state/plasma-inputs.ts';
import { Val } from './bits.tsx';
import { isHerAction } from './format.ts';
import { auditLoadedStore, debugStore, engineViewStore, jobsSeenStore, machineStore, notesStore, threatStore } from './stores.ts';

export const DRAWERS: readonly { id: RailId; name: string }[] = [
  { id: 'trace', name: 'TRACE' },
  { id: 'sentinel', name: 'SENTINEL' },
  { id: 'pulse', name: 'PULSE' },
  { id: 'jobs', name: 'JOBS' },
  { id: 'chat', name: 'CHAT' },
  { id: 'arsenal', name: 'ARSENAL' },
  { id: 'recall', name: 'RECALL' },
  { id: 'signal', name: 'SIGNAL' },
];

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'needsReview']);

function sameDay(iso: string, now: Date): boolean {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return false;
  const d = new Date(t);
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

function useSubLabels(): Record<RailId, string> {
  const audit = useStore(auditStore);
  const loaded = useStore(auditLoadedStore);
  const threat = useStore(threatStore);
  const machine = useStore(machineStore);
  const jobs = useStore(jobsStore);
  const seen = useStore(jobsSeenStore);
  const lines = useStore(transcriptStore);
  const notes = useStore(notesStore);
  const now = new Date();
  const dayKey = `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`;
  const today = useMemo(() => audit.filter((a) => isHerAction(a) && sameDay(a.ts, new Date())).length, [audit, dayKey]);
  const cpu = machine?.cpu[machine.cpu.length - 1];
  const running = jobs.filter((j) => !TERMINAL.has(j.status) && j.status !== 'queued').length;
  return {
    trace: loaded || audit.length > 0 ? `${today} TODAY` : 'NO DATA',
    sentinel: threat && !threat.seen ? 'THREAT' : 'NO DATA',
    pulse: cpu === undefined ? 'NO DATA' : `CPU ${Math.round(cpu * 100)}%`,
    jobs: seen.size === 0 ? 'NO DATA' : running > 0 ? `${running} RUNNING` : 'IDLE',
    chat: `${lines.length} MSGS`,
    arsenal: 'NO DATA',
    recall: 'NO DATA',
    signal: `${notes.length} NOTES`,
  };
}

export function Rails({ cardWaiting, onToggle }: { cardWaiting: boolean; onToggle: (id: RailId) => void }) {
  const open = useStore(railStore);
  const threat = useStore(threatStore);
  const sub = useSubLabels();
  return (
    <nav className="rails" aria-label="Rails">
      {DRAWERS.map((d, i) => (
        <button
          key={d.id}
          type="button"
          className={`rail${d.id === 'sentinel' && threat && !threat.seen ? ' threat' : ''}`}
          data-rail-id={d.id}
          aria-expanded={open === d.id && !cardWaiting}
          aria-disabled={cardWaiting}
          disabled={cardWaiting}
          title={`Alt+${i + 1}`}
          onClick={() => onToggle(d.id)}
        >
          <b>{d.name}</b>
          <Val className="small" text={sub[d.id]} />
        </button>
      ))}
      <Debug />
    </nav>
  );
}

function Debug() {
  const on = useStore(debugStore);
  const view = useStore(engineViewStore);
  if (!on) return null;
  return (
    <div className="dbg">
      <b>{view && view.fps > 0 ? Math.round(view.fps) : '--'}</b> FPS
      <br />
      <b>{view && view.p95 > 0 ? view.p95.toFixed(1) : '--'}</b> MS P95
      <br />
      SCALE <b>{view ? Math.round(view.scale * 100) : '--'}</b>
      <br />
      CHANGE <b>{view && view.changeCount > 0 ? Math.round(view.changeP95) : '--'}</b> MS
    </div>
  );
}
