/**
 * The approval queue. CONTRACT §4.1 `evt.permission.request`.
 *
 * Rendering lives in ApprovalCard.tsx; every rule that could get someone's file
 * deleted or someone's tweet posted lives here, where it can be read in one
 * sitting without JSX in the way.
 *
 * THE FIVE RULES THIS FILE EXISTS TO HOLD
 *
 *  1. ONE DECISION PER REQUEST. `sent` is set before the IPC call and is never
 *     cleared. A double click, a held Enter, a stuck key: the second attempt
 *     finds `sent` already set and does nothing. Main enforces the same rule
 *     independently against its own pending map — this is the fast layer, not
 *     the guarantee.
 *  2. NO CROSS-CONTAMINATION. Edits are stored ON the entry, keyed by arg name,
 *     never in a module-level "current edit" that a second card could inherit.
 *     Two pending requests share no mutable state at all.
 *  3. NOTHING IS SILENTLY DROPPED. The queue is bounded, but reaching the bound
 *     surfaces a banner rather than discarding a red action the owner has not
 *     seen. A request he never saw is a request he cannot reject.
 *  4. EXPIRY IS A REJECTION, AND IT IS SILENT ON THE WIRE. CONTRACT §5.1: a
 *     surface may send `approve` or `deny` and never `expired`. When the
 *     daemon's window lapses the card invalidates locally and sends NOTHING —
 *     the daemon owns that transition and will resolve it itself.
 *  5. AN INVALIDATED CARD CANNOT BE APPROVED. Disconnection, expiry and a
 *     daemon-side resolution all set `invalidated`, and every one of them
 *     disables both buttons. A card offering APPROVE against a request that no
 *     longer exists is a lie the owner would act on.
 */

import type {
  ApprovalClearReason,
  ApprovalDecision,
  PermissionRequest,
} from '../../shared/ipc-contract.ts';
import { createStore } from './store.ts';

/**
 * How many cards are DRAWN at once. Beyond this they queue and are counted.
 *
 * Three, on a 768px-tall screen, over a sphere that must stay visible. The
 * fourth card would push the stack past the status bar; a stack that scrolls is
 * a stack whose bottom card can be approved without being read.
 */
export const STACK_VISIBLE = 3;

/**
 * Hard ceiling on the queue. Not a display limit — a sanity limit.
 *
 * Thirty-two simultaneous red-tier requests is not a workload, it is a fault or
 * an attack, and the surface says so instead of quietly growing. Nothing is
 * discarded below this; at it, arrivals are refused and counted so the number
 * on screen is true.
 */
export const QUEUE_MAX = 32;

export interface ApprovalEntry {
  request: PermissionRequest;
  /**
   * Edited values, by arg name. A key that is absent has not been touched.
   *
   * Strings only. A non-string argument is rendered read-only, because a text
   * box that silently reinterprets `443` as `"443"` changes the action without
   * the owner seeing a difference.
   */
  edits: Readonly<Record<string, string>>;
  /** The decision this surface has already sent. Rule 1. */
  sent: ApprovalDecision | null;
  /** The daemon's `evt.permission.resolved` decision, if one arrived. */
  resolved: string | null;
  /** Why this card can no longer be acted on. Rule 5. */
  invalidated: ApprovalClearReason | null;
}

/** Oldest first — the order they arrived, which is the order they expire. */
export const approvalsStore = createStore<readonly ApprovalEntry[]>([]);

/** Arrivals refused because the queue was full. Shown, never hidden. */
export const approvalOverflowStore = createStore<number>(0);

function update(requestId: string, change: (entry: ApprovalEntry) => ApprovalEntry): void {
  const list = approvalsStore.get();
  let hit = false;
  const next = list.map((entry) => {
    if (entry.request.requestId !== requestId) return entry;
    hit = true;
    return change(entry);
  });
  if (hit) approvalsStore.set(next);
}

/**
 * A new card. Idempotent by `requestId`.
 *
 * CONTRACT §3.3 makes commands idempotent by id; events carry no such promise,
 * and a reconnect that replays a pending request is a perfectly ordinary thing
 * for the daemon to do. Two cards for one action would let the owner approve
 * the same tweet twice, so a repeat updates the existing entry's request in
 * place — preserving his edits and, critically, preserving `sent`.
 */
export function approvalArrived(request: PermissionRequest): void {
  const list = approvalsStore.get();
  const existing = list.find((e) => e.request.requestId === request.requestId);
  if (existing) {
    update(request.requestId, (entry) => ({ ...entry, request }));
    return;
  }
  if (list.length >= QUEUE_MAX) {
    approvalOverflowStore.set(approvalOverflowStore.get() + 1);
    return;
  }
  approvalsStore.set([
    ...list,
    { request, edits: {}, sent: null, resolved: null, invalidated: null },
  ]);
}

export function approvalEdited(requestId: string, key: string, value: string): void {
  update(requestId, (entry) => ({ ...entry, edits: { ...entry.edits, [key]: value } }));
}

/** Undo every edit on one card, so "put it back how she said it" is one click. */
export function approvalReverted(requestId: string): void {
  update(requestId, (entry) => ({ ...entry, edits: {} }));
}

/**
 * Record that a decision has gone to main. Rule 1.
 *
 * Returns false when this card has already been decided or can no longer be
 * decided — the caller must not send in that case. Deliberately a
 * check-and-set in one function: two callers doing `if (canSend) send()`
 * against a shared store is the shape of the bug this prevents.
 */
export function approvalClaim(requestId: string, decision: ApprovalDecision): boolean {
  const entry = approvalsStore.get().find((e) => e.request.requestId === requestId);
  if (!entry || entry.sent !== null || entry.invalidated !== null) return false;
  update(requestId, (e) => ({ ...e, sent: decision }));
  return true;
}

/** The daemon answered, or the card must leave. Rule 5. */
export function approvalCleared(
  requestId: string,
  reason: ApprovalClearReason,
  decision?: string,
): void {
  update(requestId, (entry) => ({
    ...entry,
    invalidated: reason,
    resolved: decision ?? entry.resolved,
  }));
}

/** The owner acknowledged a dead card. The only path that removes an entry. */
export function approvalDismissed(requestId: string): void {
  approvalsStore.set(
    approvalsStore.get().filter((entry) => entry.request.requestId !== requestId),
  );
}

/**
 * The link to the daemon is gone. Every pending request went with it.
 *
 * Invalidates rather than removes, on purpose. Silently emptying the stack
 * would leave the owner believing nothing was ever waiting; he needs to see
 * that something WAS pending and is now void, so he can re-issue it.
 */
export function approvalsInvalidateAll(reason: ApprovalClearReason): number {
  const list = approvalsStore.get();
  const live = list.filter((e) => e.invalidated === null);
  if (live.length === 0) return 0;
  approvalsStore.set(
    list.map((entry) => (entry.invalidated === null ? { ...entry, invalidated: reason } : entry)),
  );
  return live.length;
}

/**
 * Sweep expired requests. Rule 4 — invalidate, send nothing.
 *
 * Driven by `expiresAt` from the daemon, compared against wall clock. Returns
 * the ids it expired so the caller can log them; a card vanishing with no
 * record of why is the failure this is written against.
 */
export function approvalsSweepExpired(now: number = Date.now()): string[] {
  const expired: string[] = [];
  for (const entry of approvalsStore.get()) {
    if (entry.invalidated !== null) continue;
    const at = Date.parse(entry.request.expiresAt);
    // An unparseable `expiresAt` must NOT expire the card. Guessing a deadline
    // for a red action is worse than having none: it would revoke the owner's
    // ability to approve something the daemon still holds.
    if (!Number.isFinite(at) || now < at) continue;
    expired.push(entry.request.requestId);
  }
  for (const id of expired) approvalCleared(id, 'expired');
  return expired;
}

/* ─────────────────────────────────────────────────────────────── derivations */

/** Only string args are editable. See ApprovalEntry.edits. */
export function isEditable(value: unknown): value is string {
  return typeof value === 'string';
}

/** The value to SHOW and, if the wire allowed it, to send. */
export function effectiveValue(entry: ApprovalEntry, key: string): string {
  const edit = entry.edits[key];
  if (edit !== undefined) return edit;
  const original = entry.request.args[key];
  return isEditable(original) ? original : '';
}

/** Has this argument been changed from what the daemon sent? */
export function isFieldEdited(entry: ApprovalEntry, key: string): boolean {
  const edit = entry.edits[key];
  if (edit === undefined) return false;
  return edit !== entry.request.args[key];
}

/** Has ANY argument been changed? Gates APPROVE — see ApprovalCard.tsx. */
export function isEdited(entry: ApprovalEntry): boolean {
  return Object.keys(entry.edits).some((key) => isFieldEdited(entry, key));
}

/** Can this card still be acted on at all? */
export function isActionable(entry: ApprovalEntry): boolean {
  return entry.sent === null && entry.invalidated === null;
}
