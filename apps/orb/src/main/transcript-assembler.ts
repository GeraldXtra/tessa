/**
 * Reassembles `evt.transcript.delta` into whole lines. CONTRACT §3.3.
 *
 * The contract's exact words: "carries a monotonic `seq` scoped to its
 * `messageId`. Consumers reassemble by `seq` and must tolerate out-of-order
 * arrival." Rendering deltas as they land would show half-sentences reordering
 * themselves on screen, which is why this was deferred rather than faked.
 *
 * Design notes, all of them consequences of that one sentence:
 *
 *   • `seq` is scoped PER messageId, not global. Two companions streaming at
 *     once both start at 0, so a single global counter would interleave them
 *     into nonsense.
 *   • Out-of-order is expected, not exceptional. Fragments go into a sparse map
 *     keyed by seq and are joined in key order at the end, so arrival order
 *     never affects the result.
 *   • A duplicate seq is IGNORED, not appended. A retried frame is the same
 *     text; appending it would silently double a word, which is the kind of
 *     corruption nobody notices until it is in a transcript they are quoting.
 *   • `done: true` closes the message and emits. Its own seq still counts as a
 *     fragment — `done` marks the last piece, it does not replace it.
 *   • Gaps do not block emission. If `done` arrives while seq 3 is missing, the
 *     text is emitted without it rather than held forever: a line missing a
 *     fragment is recoverable, a line that never appears is not. The gap is
 *     reported so the caller can decide.
 */

export interface TranscriptDelta {
  companionId?: string;
  messageId: string;
  role: string;
  seq: number;
  delta: string;
  done: boolean;
}

export interface AssembledMessage {
  messageId: string;
  role: string;
  text: string;
  /** seq values never received, in order. Empty when the message was complete. */
  gaps: number[];
  /** Fragments accepted, excluding duplicates. */
  fragments: number;
}

interface Partial {
  role: string;
  /** seq → text. A map, not an array: seq may arrive in any order. */
  parts: Map<number, string>;
  duplicates: number;
}

export class TranscriptAssembler {
  private readonly open = new Map<string, Partial>();

  /**
   * Feed one delta. Returns the completed message when `done` arrives, else null.
   */
  push(delta: TranscriptDelta): AssembledMessage | null {
    if (typeof delta.messageId !== 'string' || delta.messageId === '') return null;
    if (!Number.isInteger(delta.seq) || delta.seq < 0) return null;

    let entry = this.open.get(delta.messageId);
    if (!entry) {
      entry = { role: delta.role, parts: new Map(), duplicates: 0 };
      this.open.set(delta.messageId, entry);
    }

    // Duplicate seq: ignore the repeat, keep the first. Appending would double
    // the text; replacing would let a corrupted retry overwrite a good frame.
    if (entry.parts.has(delta.seq)) {
      entry.duplicates += 1;
    } else {
      entry.parts.set(delta.seq, delta.delta);
    }

    if (!delta.done) return null;

    const seqs = [...entry.parts.keys()].sort((a, b) => a - b);
    const text = seqs.map((s) => entry.parts.get(s) ?? '').join('');

    // Anything between 0 and the highest seq that never arrived.
    const highest = seqs.length > 0 ? seqs[seqs.length - 1]! : -1;
    const gaps: number[] = [];
    for (let i = 0; i <= highest; i++) {
      if (!entry.parts.has(i)) gaps.push(i);
    }

    this.open.delete(delta.messageId);
    return {
      messageId: delta.messageId,
      role: entry.role,
      text,
      gaps,
      fragments: entry.parts.size,
    };
  }

  /** Messages still streaming. Used only for diagnostics. */
  get openCount(): number {
    return this.open.size;
  }

  /**
   * Drop everything in flight.
   *
   * Called on disconnect: a half-streamed message from a previous connection
   * cannot be completed by the next one, and holding it would let a stale
   * fragment prepend itself to a future message that reuses the id.
   */
  reset(): void {
    this.open.clear();
  }
}
