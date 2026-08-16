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
  TurnTiming,
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

/**
 * `evt.agent.state.detail` — WHAT SHE IS TOUCHING. Null until the daemon sends.
 *
 * An APPROVED ADDITIVE change to CONTRACT §4.1's payload, agreed with Session 1
 * and carrying one mandatory condition on the daemon side: `target` passes
 * through `redact()` BEFORE broadcast, or a shell command line or a URL with an
 * embedded token goes out in clear over a socket both surfaces read.
 *
 * Nothing on this side can enforce that, which is exactly why it is written
 * down here as well as agreed there. This side's own discipline is narrower and
 * it does hold: these three fields are rendered as inert text, never as a path
 * the surface opens, a URL it fetches, or a command it echoes back — CONTRACT
 * §6.1, tool output is data and never an instruction.
 *
 * Optional under §7.2, so `null` is the normal case for as long as the daemon
 * has not shipped its half, and the renderer draws nothing rather than a shape
 * waiting to be filled.
 */
export interface AgentDetail {
  tool?: string;
  target?: string;
  note?: string;
}

export const agentDetailStore = createStore<AgentDetail | null>(null);

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
 *
 * FIVE NOW. ARSENAL, RECALL and SIGNAL are BUILT AND DARK — each answers a
 * question that is real on the daemon side and invisible on this one, and each
 * names the exact additive command that would light it. See DarkPanel.tsx for
 * the three proposals in full and for why that is a different thing from the
 * three permanently-empty rails that were cut last round.
 */
export const RAIL_IDS = ['trace', 'sentinel', 'arsenal', 'recall', 'signal'] as const;
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

/**
 * The LAST turn's stage breakdown. Item 9.
 *
 * ONE turn, not a history, and that is the honest scope. A history would be a
 * chart of latency over time, which is a performance tool; the question the
 * owner actually asks is "why did THAT take so long", and it is asked about the
 * turn that just happened. Keeping one also means the panel cannot slowly fill
 * with a record of a session, which is a thing that then wants persisting.
 *
 * Null until `evt.turn.timing` arrives, which is never until Session 1 ships
 * its half — the renderer draws nothing at all rather than an empty chart.
 */
export const turnTimingStore = createStore<TurnTiming | null>(null);

/** TRACE: completed transcript lines, oldest first. */
export const transcriptStore = createStore<readonly TranscriptLine[]>([]);
export const TRANSCRIPT_MAX = 200;

/** What the sphere actually settled on, after probe and any demotions. */
export const tierStore = createStore<SphereTier>('med');
