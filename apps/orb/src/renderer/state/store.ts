/**
 * A ~30-line external store, deliberately not a state library.
 *
 * The reason it exists rather than `useState`: the sphere reads agent state
 * every animation frame from an imperative Three.js loop that lives outside
 * React entirely. A store that both React (via useSyncExternalStore) and a raw
 * rAF callback (via `get()`) can read means the sphere never triggers a render,
 * and React never has to re-render at 30 Hz to keep it fed.
 *
 * Spec §10: two physical cores, shared with the daemon. A reconciler pass per
 * frame is exactly the contention that warning is about.
 */

import { useSyncExternalStore } from 'react';

import type { AgentState } from '@zoey/protocol';

import type {
  AuditEntry,
  ConnectionStatus,
  DaemonHealth,
  MicState,
  OrbNotification,
  PtySession,
  SphereTier,
  TranscriptLine,
} from '../../shared/ipc-contract.ts';

// Re-exported so the components that render notifications keep importing the
// type from the store they read, while there stays exactly ONE definition of
// it — in the IPC contract, which is where main and renderer have to agree.
export type { OrbNotification };

export interface Store<T> {
  get(): T;
  set(next: T): void;
  subscribe(listener: () => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const listeners = new Set<() => void>();

  return {
    get: () => value,
    set(next: T) {
      if (Object.is(next, value)) return;
      value = next;
      for (const listener of listeners) listener();
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export function useStore<T>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}

/* ──────────────────────────────────────────────────────────── the stores */

/**
 * Phase 1 drives this from the Alt+1…6 dev cycler. Phase 2 points it at
 * `evt.agent.state` and nothing downstream changes.
 */
export const agentStateStore = createStore<AgentState>('idle');

export const connectionStore = createStore<ConnectionStatus>({ phase: 'offline' });

/**
 * Last `evt.daemon.health`. Null until the first heartbeat arrives, which is up
 * to 5 s after connecting — so "connected but no beat yet" is a real state and
 * must not be drawn as a stalled one.
 */
export const healthStore = createStore<DaemonHealth | null>(null);

/**
 * The rails, §R.3. Order is fixed and is the order they render.
 *
 * These replace AGENDA / JOBS / TRANSCRIPT wholesale. Those three were built
 * against an earlier reading of the dashboard and are gone — not renamed, not
 * hidden. TRACE is the nearest successor to the old transcript panel, but it is
 * a different thing: provenance-gutted, per-companion, and empty until the
 * voice pipeline produces events.
 */
export const RAIL_IDS = ['sentinel', 'trace'] as const;
export type RailId = (typeof RAIL_IDS)[number];

/** Exactly one rail open, or none. §R.7 — one drawer at a time below 1600px. */
export const railStore = createStore<RailId | null>(null);

/* ───────────────────────────────────────────────────── live data for the rails */

/**
 * Rolling health history, for PULSE's sparklines.
 *
 * The daemon sends a sample every 5 s and keeps no history, so the surface has
 * to accumulate it. 60 samples is five minutes — enough for a sparkline to show
 * a trend, small enough that it costs nothing and is honestly bounded. It is
 * deliberately NOT persisted: a sparkline that survives a restart would imply
 * continuity of measurement across a gap where none was taken.
 */
export const HEALTH_HISTORY_MAX = 60;
export const healthHistoryStore = createStore<readonly DaemonHealth[]>([]);

export function pushHealthSample(sample: DaemonHealth): void {
  const next = [...healthHistoryStore.get(), sample];
  healthHistoryStore.set(next.slice(-HEALTH_HISTORY_MAX));
}

/** SENTINEL: newest first. Seeded by res.audit, extended by evt.audit.appended. */
export const auditStore = createStore<readonly AuditEntry[]>([]);
export const AUDIT_MAX = 200;

/** SENTINEL: the live PTY roster the daemon assembles (CONTRACT §4.2). */
export const ptySessionsStore = createStore<readonly PtySession[]>([]);

/**
 * §R.2 top-right notification stack.
 *
 * Fed by `evt.notification` (CONTRACT §4.1). Nothing emits it yet, so this
 * stays empty and the stack renders nothing at all — the mechanism is built,
 * the light is off.
 */
export const notificationsStore = createStore<readonly OrbNotification[]>([]);
export const NOTIFICATIONS_MAX = 4;

export function pushNotification(note: OrbNotification): void {
  // Deduped by id. The same fact can reach here twice — main pushes the
  // chord-registration failure when it happens, and the renderer also raises it
  // on first seeing `chordRegistered: false`, because the push is sent before
  // the renderer has mounted and loses that race exactly as the audit history
  // did. Two paths, one message.
  const current = notificationsStore.get();
  if (current.some((n) => n.id === note.id)) return;
  // Newest first, bounded. §R.2 calls the stack "dismissible, stacked,
  // auto-fading" — it is not a log, and four is as many as can be read at a
  // glance on a 768px-tall screen without covering the sphere.
  notificationsStore.set([note, ...current].slice(0, NOTIFICATIONS_MAX));
}

/**
 * The microphone claim, as the daemon confirmed it. See MicState — `claimed` is
 * never set from local intent, so this is safe to render as fact.
 */
export const micStore = createStore<MicState>({
  claimed: false,
  mode: 'toggle',
  since: null,
  chord: '',
  chordRegistered: false,
  lastError: null,
});

/** TRACE: completed transcript lines, oldest first. */
export const transcriptStore = createStore<readonly TranscriptLine[]>([]);
export const TRANSCRIPT_MAX = 200;

/** What the sphere actually settled on, after probe and any demotions. */
export const tierStore = createStore<SphereTier>('med');
