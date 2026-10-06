/**
 * The typed path's latency probe. DEV ONLY in effect: it only ever reports
 * through `reportMetrics`, which is a no-op sink in a packaged build, and it
 * costs one `requestAnimationFrame` per transcript line while a send is being
 * timed — nothing at rest.
 *
 * Three instants, all on the RENDERER's own clock (`performance.now()`):
 *
 *   keydownAt     Enter went down in the box.
 *   ackAt         `agentSend` resolved with the daemon's acceptance.
 *   paint         the frame that draws the line — captured with a rAF
 *                 requested from the layout effect that committed the line,
 *                 so it fires at the start of the first frame after the DOM
 *                 changed. Pixels reach the glass at most one frame later;
 *                 the number is "frame scheduled", said as such.
 *
 * The user line is timed only because it comes back from the daemon: the
 * transcript renders it from `evt.transcript.message`, not from the text that
 * was sent, so keydown → user-line-painted is a genuine loopback round trip
 * and not a measurement of setState.
 */

import type { TranscriptLine } from '../../shared/ipc-contract.ts';

interface Pending {
  n: number;
  keydownAt: number;
  ackAt: number | null;
  userPaintAt: number | null;
}

let pending: Pending | null = null;
let counter = 0;

export function latencyMarkSent(keydownAt: number): void {
  counter += 1;
  pending = { n: counter, keydownAt, ackAt: null, userPaintAt: null };
}

export function latencyMarkAck(): void {
  if (pending) pending.ackAt = performance.now();
}

/** Called from TracePanel's layout effect with the line that was just committed. */
export function latencyLineCommitted(line: TranscriptLine, report: (line: string) => void): void {
  const p = pending;
  if (!p) return;
  // A second user line while one is already timed is not ours to pair.
  if (line.role === 'user' && p.userPaintAt !== null) return;
  requestAnimationFrame(() => {
    const now = performance.now();
    const ms = (v: number): string => v.toFixed(2);
    if (line.role === 'user') {
      p.userPaintAt = now;
      report(
        `LATENCY-USER n=${p.n} messageId=${line.messageId} via=${line.via ?? '(absent)'} ` +
          `keydownToAck=${p.ackAt === null ? 'na' : ms(p.ackAt - p.keydownAt)} ` +
          `keydownToPaint=${ms(now - p.keydownAt)}`,
      );
      return;
    }
    report(
      `LATENCY-ASSISTANT n=${p.n} messageId=${line.messageId} role=${line.role} ` +
        `via=${line.via ?? '(absent)'} keydownToPaint=${ms(now - p.keydownAt)} ` +
        `userPaintToAssistantPaint=${p.userPaintAt === null ? 'na' : ms(now - p.userPaintAt)}`,
    );
    if (pending === p) pending = null;
  });
}
