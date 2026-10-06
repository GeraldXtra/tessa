/**
 * The compose box's state, held OUTSIDE the component.
 *
 * The box lives in the TRACE drawer's footer, and the drawer keeps only the
 * last-opened rail's panel mounted — open SENTINEL and the TRACE panel, box
 * included, unmounts. A draft held in component state would vanish with it.
 * It has to survive four things the brief names: switching rails, the
 * approval card closing the drawer, a reconnect, and the daemon going away
 * entirely. A module store survives all four for free; a `useState` survives
 * none of them.
 *
 * ─── what "in flight" means here, and why there are two of them ───
 *
 *   awaitingAck   the line is on the wire and `res.agent.accepted` has not
 *                 come back. The text is STILL IN THE BOX — it clears on the
 *                 ack and only on the ack (CONTRACT §5.1's acceptance, never
 *                 local optimism). A refusal or a 3 s silence leaves the
 *                 text where it is and puts the reason in the hint line.
 *
 *   inflight      the daemon accepted and is working. Known by `messageId`,
 *                 which is what `cmd.agent.cancel` needs. It ends when a line
 *                 that is not his arrives (her answer, or a system line) or
 *                 when `evt.agent.state` goes idle after having been busy.
 *
 * Nothing in here is rendered as a status. The sphere and the state chip
 * already show thinking; this only decides what Escape does.
 */

import type { TranscriptLine } from '../../shared/ipc-contract.ts';
import { createStore } from './store.ts';

export interface ComposeState {
  draft: string;
  awaitingAck: { text: string; sentAt: number } | null;
  inflight: { messageId: string; ackAt: number; sawBusy: boolean } | null;
  /**
   * What the hint line says instead of ENTER TO SEND. Only ever the daemon's
   * own `message`, the client's own timeout, or the character cap — never a
   * status this surface made up. Cleared on the next edit.
   */
  error: string | null;
  /** Bumped to ask the box to take focus (the chord). */
  focusRequest: number;
}

export const composeStore = createStore<ComposeState>({
  draft: '',
  awaitingAck: null,
  inflight: null,
  error: null,
  focusRequest: 0,
});

export function composePatch(patch: Partial<ComposeState>): void {
  composeStore.set({ ...composeStore.get(), ...patch });
}

export function requestComposeFocus(): void {
  composePatch({ focusRequest: composeStore.get().focusRequest + 1 });
}

/**
 * The daemon accepted `sent`. Remove THAT text from the box — and only that.
 *
 * He may have kept typing in the milliseconds between Enter and the ack. A
 * blanket clear would eat those keystrokes, so: an unchanged box empties, a
 * box he appended to keeps the appendix, and a box he rewrote is left alone.
 */
export function composeAccepted(sent: string, messageId: string, ackAt: number): void {
  const s = composeStore.get();
  let draft = s.draft;
  if (draft === sent) draft = '';
  else if (draft.startsWith(sent)) draft = draft.slice(sent.length).replace(/^\n/, '');
  composeStore.set({
    ...s,
    draft,
    awaitingAck: null,
    inflight: { messageId, ackAt, sawBusy: false },
    error: null,
  });
}

/** The daemon refused, or never answered. The text stays; the reason shows. */
export function composeRefused(error: string): void {
  composePatch({ awaitingAck: null, error });
}

/** A transcript line arrived. Anything that is not his closes the turn in flight. */
export function composeNoteLine(line: TranscriptLine): void {
  const s = composeStore.get();
  if (!s.inflight || line.role === 'user') return;
  composeStore.set({ ...s, inflight: null });
}

/**
 * `evt.agent.state` arrived — the raw arrival, before the dwell, because
 * this is bookkeeping and not a drawing. idle AFTER busy closes the turn; an
 * idle that arrives before any busy state is the daemon's resting repeat and
 * says nothing about the turn we just sent.
 */
export function composeNoteState(state: string): void {
  const s = composeStore.get();
  if (!s.inflight) return;
  if (state !== 'idle') {
    if (!s.inflight.sawBusy) composeStore.set({ ...s, inflight: { ...s.inflight, sawBusy: true } });
    return;
  }
  if (s.inflight.sawBusy) composeStore.set({ ...s, inflight: null });
}
