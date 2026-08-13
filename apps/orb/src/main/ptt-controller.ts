/**
 * Push-to-talk. The Orb's half of it, which is the trigger and nothing else.
 *
 * ─── the Orb does not touch the microphone ───
 * CONTRACT §5.3 puts `cmd.voice.pushToTalk` here and audio capture in the
 * daemon. This file sends `{ action: "start" | "stop" }` and renders what comes
 * back. It does not open a device, does not hold a buffer, and the Orb's own
 * microphone permission stays denied in index.ts — deliberately written before
 * any code that could want one.
 *
 * ─── two modes, and why toggle is the default ───
 * HOLD is the safe shape: the claim cannot outlive the key. TOGGLE is the one
 * Gerald will actually use, and a safety property nobody uses is worth nothing.
 * So toggle is the default and hold is one flag away.
 *
 * The cost of toggle, stated plainly: a dropped keystroke, a crash between
 * press and press, or simply forgetting, leaves the microphone claimed. That is
 * a privacy failure, not an inconvenience, and the daemon agrees — it audits
 * `voice.stream.open` at RED tier precisely because "the microphone has been
 * live for nine hours" must be impossible to miss.
 *
 * Three guards, none of which is sufficient alone:
 *
 *   1. A HARD CAP on a single claim (CLAIM_MAX_MS). The only guard that acts
 *      without Gerald, and therefore the load-bearing one. It fires a real
 *      `stop` at the daemon, not merely a UI reset, and it announces itself —
 *      an auto-release he does not know about would leave him talking to a
 *      machine that stopped listening.
 *   2. RELEASE ON DISCONNECT. A claim that cannot be revoked must not be
 *      displayed as live. See the pendingVoice drain in ws-client.
 *   3. VISIBILITY, in the status bar. Necessary, and insufficient by itself
 *      because it only works when he is looking at it.
 *
 * All three fail toward "not recording", which is the direction the daemon's
 * own handler documents for the same reason.
 *
 * ─── hold cannot be global ───
 * Electron's `globalShortcut` has a press callback and NO release callback.
 * There is no key-up to observe, so hold is only implementable from the
 * renderer's keydown/keyup while the Orb has focus. Registering the chord
 * globally would also swallow the keydown before the renderer ever saw it. So
 * the two are mutually exclusive by construction: toggle holds the global
 * chord, hold gives it up and works focused-only. Reported rather than hidden,
 * because "hold does nothing when the window is behind something" is exactly
 * the kind of surprise that makes a feature untrustworthy.
 */

import type { MicState, OrbNotification, PttMode } from '../shared/ipc-contract.ts';

/**
 * Longest a single claim may last, in ms.
 *
 * 90 s is a judgement, and here is the whole of it: it is far longer than any
 * plausible single utterance to a personal assistant, and far shorter than the
 * time it takes a forgotten open microphone to become a real problem. It is a
 * cap on ONE claim, not a session limit — pressing the chord again re-claims
 * immediately, so the cost of being wrong in the short direction is one
 * keypress, while the cost of being wrong in the long direction is an open
 * microphone nobody knows about.
 */
export const CLAIM_MAX_MS = 90_000;

export interface PttControllerOptions {
  /** Returns false if there is no socket; the daemon's reply arrives later. */
  send: (action: 'start' | 'stop') => boolean;
  onState: (state: MicState) => void;
  notify: (note: OrbNotification) => void;
  log: (message: string) => void;
}

export class PttController {
  private readonly opts: PttControllerOptions;

  /** DAEMON-CONFIRMED. Never set from a local intention. */
  private claimed = false;
  private since: number | null = null;
  private mode: PttMode = 'toggle';
  private lastError: string | null = null;

  private chord = '';
  private chordRegistered = false;

  /** True between sending `start` and the daemon answering. */
  private startInFlight = false;
  /** Physical key state, for hold mode. */
  private keyDown = false;

  private capTimer: NodeJS.Timeout | null = null;
  private noteSeq = 0;

  constructor(options: PttControllerOptions) {
    this.opts = options;
  }

  get state(): MicState {
    return {
      claimed: this.claimed,
      mode: this.mode,
      since: this.since,
      chord: this.chord,
      chordRegistered: this.chordRegistered,
      lastError: this.lastError,
    };
  }

  setChord(chord: string, registered: boolean): void {
    this.chord = chord;
    this.chordRegistered = registered;
    this.publish();
  }

  setMode(mode: PttMode): void {
    if (mode === this.mode) return;
    // Changing mode while claimed would leave the claim owned by rules that no
    // longer apply — a hold claim has no press to end it once the mode is
    // toggle. Release first, then switch.
    if (this.claimed || this.startInFlight) this.request('stop');
    this.mode = mode;
    this.keyDown = false;
    this.opts.log(`ptt mode → ${mode}`);
    this.publish();
  }

  /* ── input edges ───────────────────────────────────────────────────────── */

  /** The global chord fired. globalShortcut gives a press and nothing else. */
  chordPressed(): void {
    if (this.mode === 'hold') return; // the chord is not registered in hold mode
    this.toggle();
  }

  /** Renderer keydown/keyup. Only meaningful in hold mode. */
  keyEdge(edge: 'down' | 'up'): void {
    if (this.mode === 'toggle') {
      // Auto-repeat would otherwise toggle many times per second while held.
      if (edge === 'down' && !this.keyDown) this.toggle();
      this.keyDown = edge === 'down';
      return;
    }
    if (edge === 'down') {
      if (this.keyDown) return;
      this.keyDown = true;
      this.request('start');
    } else {
      this.keyDown = false;
      this.request('stop');
    }
  }

  /**
   * The window lost focus. In hold mode the key-up will never arrive — the
   * keyboard belongs to something else now — so the claim has to end here or it
   * ends never.
   */
  focusLost(): void {
    if (this.mode === 'hold' && (this.keyDown || this.claimed || this.startInFlight)) {
      this.keyDown = false;
      this.opts.log('focus lost while holding — releasing the claim');
      this.request('stop');
    }
  }

  /** App is quitting. Best effort: a claim must not outlive the surface. */
  shutdown(): void {
    this.clearCap();
    if (this.claimed || this.startInFlight) this.opts.send('stop');
  }

  private toggle(): void {
    // `startInFlight` counts as claimed for this decision. Two presses in the
    // round-trip window would otherwise send start twice and leave the second
    // press expecting a stop it never sent.
    if (this.claimed || this.startInFlight) this.request('stop');
    else this.request('start');
  }

  /* ── daemon replies — the only thing that moves `claimed` ───────────────── */

  private request(action: 'start' | 'stop'): void {
    if (action === 'start') this.startInFlight = true;
    this.opts.send(action);
    this.publish();
  }

  onAck(action: 'start' | 'stop', active: boolean): void {
    this.startInFlight = false;
    this.lastError = null;
    const was = this.claimed;
    this.claimed = active;

    if (active && !was) {
      this.since = Date.now();
      this.armCap();
    } else if (!active) {
      this.since = null;
      this.clearCap();
    }
    void action;
    this.publish();
  }

  onRefused(action: 'start' | 'stop', detail: string): void {
    this.startInFlight = false;
    this.lastError = detail;

    // Fail closed on BOTH actions. A refused `start` never claimed anything; a
    // refused `stop` means the surface can no longer vouch for the claim, and
    // continuing to show MIC LIVE would be asserting something unverified.
    const was = this.claimed;
    this.claimed = false;
    this.since = null;
    this.clearCap();
    this.keyDown = false;

    if (action === 'start') {
      this.notifyOnce('warn', 'Microphone not claimed', detail);
    } else if (was) {
      this.notifyOnce('error', 'Could not release the microphone', detail);
    }
    this.publish();
  }

  /* ── the cap ───────────────────────────────────────────────────────────── */

  private armCap(): void {
    this.clearCap();
    this.capTimer = setTimeout(() => {
      this.capTimer = null;
      if (!this.claimed && !this.startInFlight) return;
      this.opts.log(`claim exceeded ${CLAIM_MAX_MS / 1000}s — auto-releasing`);
      this.notifyOnce(
        'warn',
        'Microphone released',
        `A single push-to-talk claim is capped at ${CLAIM_MAX_MS / 1000}s. Press ${
          this.chord || 'the chord'
        } again to keep talking.`,
      );
      this.request('stop');
    }, CLAIM_MAX_MS);
  }

  private clearCap(): void {
    if (this.capTimer) {
      clearTimeout(this.capTimer);
      this.capTimer = null;
    }
  }

  private notifyOnce(level: OrbNotification['level'], title: string, body: string): void {
    this.noteSeq += 1;
    this.opts.notify({ id: `ptt-${this.noteSeq}`, level, title, body });
  }

  private publish(): void {
    this.opts.onState(this.state);
  }
}
