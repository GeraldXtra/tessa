/**
 * Minimum dwell for agent states.
 *
 * ─── why ───
 * A local tool call holds `working` for perhaps 50 ms. The sphere's parameters
 * are smoothed with a 180 ms time constant, which is a first-order low-pass, so
 * in 50 ms they travel `1 - e^(-50/180)` = 24% of the way toward the new state
 * and then ease straight back. That cannot strobe — it is a soft swell about
 * 400 ms long — but at 24% amplitude it is indistinguishable from ordinary
 * frame-to-frame variation. The state happened and nobody saw it.
 *
 * 400 ms is ~2 time constants, which reaches ~87% of the target: unmistakable,
 * and still short enough not to misrepresent how long a fast tool actually ran.
 *
 * ─── queue, never discard ───
 * `working -> idle` in 50 ms must show BOTH, in order. Dropping the
 * intermediate would mean the surface deciding which of the daemon's facts are
 * worth showing, which is the same instinct as rendering a partial transcript.
 *
 * ─── but bounded, and honest about what it drops ───
 * Every queued state adds 400 ms of lag, so an unbounded queue puts the sphere
 * arbitrarily far behind reality — a sphere confidently showing what happened
 * four seconds ago is worse than one that skipped a step. The queue is capped,
 * and on overflow the MIDDLE is dropped: the head is what he is about to see
 * next and the tail is the current truth, so the ends carry the meaning and the
 * middle of a burst is transient by definition. Drops are reported, never
 * silent — a surface that quietly discards events is how a wrong shape survives.
 *
 * ─── what this costs, stated up front ───
 * This DELAYS real state changes, and spec §4's "state change -> visible" budget
 * measures exactly that interval. With the dwell active that figure includes a
 * deliberate wait, so it stops being a measurement of the renderer. The
 * consumer therefore reports the two halves separately — see App.tsx.
 */

/** Minimum time a state stays on screen before a newer one may replace it. */
export const DWELL_MS = 400;

/**
 * Pending states, excluding the one currently showing.
 *
 * Three. With the showing state that is at most 4 x 400 ms = 1.6 s of lag,
 * which is late but still recognisably a reaction. Four would be two full
 * seconds and the sphere would be narrating the past.
 */
export const QUEUE_CAP = 3;

export interface DwellRelease {
  state: string;
  /** When the daemon frame for this state arrived, for the split measurement. */
  arrivedAt: number;
  /** How long it waited in the queue. 0 when shown immediately. */
  queuedMs: number;
}

export interface StateDwellOptions {
  /** Hand a state to the store. Called at most once per DWELL_MS. */
  release: (r: DwellRelease) => void;
  /** Diagnostics; drops in particular must be visible. */
  report?: (line: string) => void;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => number;
  clearTimer?: (id: number) => void;
}

interface Pending {
  state: string;
  arrivedAt: number;
}

export class StateDwell {
  private readonly opts: StateDwellOptions;
  private readonly now: () => number;
  private readonly setTimer: (fn: () => void, ms: number) => number;
  private readonly clearTimer: (id: number) => void;

  private queue: Pending[] = [];
  private showing: string | null = null;
  private timer: number | null = null;

  constructor(options: StateDwellOptions) {
    this.opts = options;
    this.now = options.now ?? (() => performance.now());
    this.setTimer = options.setTimer ?? ((fn, ms) => window.setTimeout(fn, ms));
    this.clearTimer = options.clearTimer ?? ((id) => window.clearTimeout(id));
  }

  submit(state: string): void {
    const arrivedAt = this.now();

    // The daemon repeats each state several times per turn — one run saw
    // `speaking` broadcast seven times. A repeat is not a change, and queueing
    // it would hold the sphere for 400 ms per duplicate showing nothing new.
    const latest = this.queue.length > 0 ? this.queue[this.queue.length - 1]?.state : this.showing;
    if (state === latest) return;

    if (this.timer === null) {
      this.show({ state, arrivedAt }, 0);
      return;
    }

    if (this.queue.length >= QUEUE_CAP) {
      // Head is next on screen, tail is the truth. Drop between them.
      const victim = Math.floor(this.queue.length / 2);
      const [dropped] = this.queue.splice(victim, 1);
      this.opts.report?.(
        `DWELL dropped '${dropped?.state ?? '?'}' from the middle of a burst ` +
          `(cap ${QUEUE_CAP}); head and tail kept`,
      );
    }
    this.queue.push({ state, arrivedAt });
  }

  /** Pending count, for tests and diagnostics. */
  get depth(): number {
    return this.queue.length;
  }

  dispose(): void {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.queue = [];
  }

  private show(item: Pending, queuedMs: number): void {
    this.showing = item.state;
    this.opts.release({ state: item.state, arrivedAt: item.arrivedAt, queuedMs });
    this.timer = this.setTimer(() => this.onDwellElapsed(), DWELL_MS);
  }

  private onDwellElapsed(): void {
    this.timer = null;
    const next = this.queue.shift();
    if (!next) return;
    this.show(next, this.now() - next.arrivedAt);
  }
}
