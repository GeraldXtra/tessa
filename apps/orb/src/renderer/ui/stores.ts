import type { AgentState } from '@tessa/protocol';

import type { DaemonHealth, DiskUsage, JobView, PingResult } from '../../shared/ipc-contract.ts';
import { createStore } from '../state/store.ts';

export type NoteKind = '' | 'wait' | 'threat';

export interface Note {
  id: number;
  src: string;
  msg: string;
  kind: NoteKind;
  at: number;
  until: number;
}

export const NOTES_LOG_MAX = 100;
export const notesStore = createStore<readonly Note[]>([]);
let noteSeq = 0;

export function pushNote(src: string, msg: string, kind: NoteKind = ''): void {
  const at = Date.now();
  noteSeq += 1;
  const note: Note = { id: noteSeq, src, msg, kind, at, until: at + (kind === 'threat' ? 9000 : 6000) };
  notesStore.set([note, ...notesStore.get()].slice(0, NOTES_LOG_MAX));
}

export const debugStore = createStore<boolean>(true);

export const dockShownStore = createStore<boolean>(false);

export const memTotalStore = createStore<number>(0);

export const threatStore = createStore<{ at: number; seen: boolean } | null>(null);

export const MACHINE_HISTORY = 60;

export interface MachineView {
  cpu: readonly number[];
  mem: readonly number[];
  at: number;
}

export const machineStore = createStore<MachineView | null>(null);

export function pushMachine(cpu: number, mem: number): void {
  const prev = machineStore.get();
  machineStore.set({
    cpu: [...(prev?.cpu ?? []), cpu].slice(-MACHINE_HISTORY),
    mem: [...(prev?.mem ?? []), mem].slice(-MACHINE_HISTORY),
    at: Date.now(),
  });
}

export const diskStore = createStore<DiskUsage | null>(null);

export const pingStore = createStore<PingResult | null>(null);

export const BEATS_MAX = 40;
export const beatsStore = createStore<readonly number[]>([]);

export function pushBeat(at: number): void {
  beatsStore.set([...beatsStore.get(), at].slice(-BEATS_MAX));
}

export const lastHealthStore = createStore<DaemonHealth | null>(null);

export const auditLoadedStore = createStore<{ at: number; rows: number; limit: number } | null>(null);

export const stateSinceStore = createStore<{ state: AgentState; at: number }>({ state: 'idle', at: Date.now() });

export interface JobSeen {
  view: JobView;
  firstAt: number;
  endedAt: number | null;
}

export const jobsSeenStore = createStore<ReadonlyMap<string, JobSeen>>(new Map());

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'needsReview']);

export function noteJobs(views: readonly JobView[]): void {
  const now = Date.now();
  const next = new Map(jobsSeenStore.get());
  for (const v of views) {
    const prev = next.get(v.jobId);
    const ended = TERMINAL.has(v.status) ? (prev?.endedAt ?? now) : null;
    next.set(v.jobId, { view: v, firstAt: prev?.firstAt ?? now, endedAt: ended });
  }
  for (const [id, seen] of next) {
    if (seen.endedAt === null && !views.some((v) => v.jobId === id)) next.set(id, { ...seen, endedAt: now });
  }
  while (next.size > 64) {
    const oldest = next.keys().next().value;
    if (oldest === undefined) break;
    next.delete(oldest);
  }
  jobsSeenStore.set(next);
}

export const frontCardStore = createStore<{ id: string; shownAt: number; at: number } | null>(null);

export const ANSWER_LOCK_MS = 600;

export interface EngineView {
  fps: number;
  p95: number;
  scale: number;
  changeP95: number;
  changeCount: number;
  gpu: string;
  path: string;
}

export const engineViewStore = createStore<EngineView | null>(null);
