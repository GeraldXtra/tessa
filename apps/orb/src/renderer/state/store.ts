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

import type { ConnectionStatus, SphereTier } from '../../shared/ipc-contract.ts';

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

export type DrawerId = 'agenda' | 'jobs' | 'transcript';

/** Exactly one drawer, or none. Spec §8.1 — panels are overlays, not columns. */
export const drawerStore = createStore<DrawerId | null>(null);

/** What the sphere actually settled on, after probe and any demotions. */
export const tierStore = createStore<SphereTier>('med');
