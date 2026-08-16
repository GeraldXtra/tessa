/**
 * One 1 Hz clock for the whole telemetry layer.
 *
 * ─── why this exists at all ───
 * The diagnosis this composition is built to answer is that NOTHING ON SCREEN
 * CHANGES on a timescale the owner can perceive. The sphere breathes, the pulse
 * fires every five seconds, and every other element is static text. An
 * instrument does not read as advanced because it is dense — it reads as
 * advanced because it is live.
 *
 * So the column's derived values — uptime, daemon age, the axis window, "4s
 * ago" — are recomputed once a second against this. The daemon still only
 * speaks every five seconds; what ticks between beats is elapsed time, which is
 * a real quantity this surface owns and does not have to be told.
 *
 * ─── one timer, not fifteen ───
 * Every consumer reads the same store. Fifteen components each holding their
 * own `setInterval` would be fifteen wakeups a second on a two-core machine,
 * and they would drift apart so that two "ago" values a line apart disagreed.
 *
 * ─── it is NOT animation ───
 * §R.7's motion budget forbids anything on the rails animating continuously.
 * A value that changes once a second is telemetry, not motion: no transition,
 * no keyframe, no compositor work — one text node's contents replaced. That
 * distinction is what lets the surface feel alive without violating the rule
 * that the sphere is the only thing that moves at rest.
 */

import { createStore } from './store.ts';

/** Wall clock, republished once a second. Read it; never write it. */
export const tickStore = createStore<number>(Date.now());

let timer: number | null = null;

/**
 * Start the clock. Idempotent, and aligned to the second boundary so the whole
 * column updates on the same edge rather than at whatever millisecond the app
 * happened to mount on.
 */
export function startTick(): () => void {
  if (timer !== null) return () => {};
  const align = 1000 - (Date.now() % 1000);
  let interval: number | null = null;
  const begin = window.setTimeout(() => {
    tickStore.set(Date.now());
    interval = window.setInterval(() => tickStore.set(Date.now()), 1000);
    timer = interval;
  }, align);
  timer = begin;
  return () => {
    window.clearTimeout(begin);
    if (interval !== null) window.clearInterval(interval);
    timer = null;
  };
}

/* ─────────────────────────────────────────────────────────── formatting ── */

/**
 * Elapsed seconds as the owner reads them. `2h 36m 04s`.
 *
 * Seconds are always two digits and always present below a day, because a
 * field that changes width every tick makes a right-aligned column jitter —
 * and jitter is the difference between "live" and "broken".
 */
export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '';
  const s = Math.floor(seconds % 60);
  const m = Math.floor((seconds / 60) % 60);
  const h = Math.floor((seconds / 3600) % 24);
  const d = Math.floor(seconds / 86400);
  if (d > 0) return `${d}d ${String(h).padStart(2, '0')}h ${String(m).padStart(2, '0')}m`;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${s}s`;
}

/** Short relative age: `4s`, `2m`, `1h`. Empty when the input is not a time. */
export function formatAgo(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

/** `14:39` in local time, from an ISO string. Empty if unparseable. */
export function formatClock(iso: string): string {
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return '';
  const d = new Date(at);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
