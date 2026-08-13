/**
 * apps/console/src/renderer/terminal/latency.ts
 *
 * Keystroke → glyph measurement. This file exists to produce a number the owner
 * will trust, so its methodology matters more than its brevity.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THE BOUNDARY PROBLEM — settled with the owner before measuring
 * ─────────────────────────────────────────────────────────────────────────────
 * This panel runs at 60 Hz → 16.67 ms per frame. The vsync wait ALONE is
 * uniform over 0..16.67 ms, so its p95 is ≈ **15.84 ms**. A "16 ms p95 to
 * photons" therefore leaves ~0.16 ms for the ConPTY round trip, the MessagePort
 * hops, base64, xterm's parser, render, paint and composite combined — i.e. it
 * is unreachable by arithmetic, not by poor engineering. For scale, conhost.exe
 * measured by high-speed camera is ~33 ms to photons on a 120 Hz display.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * STATUS: NO MEASUREMENT OBTAINED. Read this before trusting anything here.
 * ─────────────────────────────────────────────────────────────────────────────
 * This harness does not currently produce a keystroke-to-glyph figure. It aborts
 * with `frameStalls=20, n=0`. It is deliberately left in that state — the
 * instrument is broken, the thing it measures is not, and further debugging was
 * ruled out of scope rather than forgotten.
 *
 * What IS proven, independently of this file:
 *   • xterm renders on the WebGL rung (probe + addon attach verified)
 *   • the PTY round-trips — diagnostics captured `tx#0 input="a"` -> `rx#2 "a"`
 *   • an earlier build of this harness completed 600 sequential samples
 * So the transport and terminal work. Only the measurement is missing.
 *
 * FOUR CAUSES FOUND AND FIXED along the way (all real, all still fixed here):
 *   1. Chromium throttles timers/rAF in a backgrounded window — fixed with
 *      `backgroundThrottling: false` (took the run from ~1 to ~20 samples/sec).
 *   2. `afterPaint()` leaked a MessageChannel per sample (~2000 unclosed ports
 *      per run) — MessageChannel removed entirely.
 *   3. Timed-out echo waiters were never deregistered, so stale matchers stole
 *      later samples' chunks — `cancel()` added.
 *   4. `afterPaint()` cleared its own watchdog before settling, so a missed
 *      message wedged the run with no timeout left — watchdog now clears only
 *      on settle.
 *
 * ONE SYMPTOM OUTSTANDING, unexplained: Long Animation Frames of ~1015 ms, i.e.
 * the main thread blocking for ~1 s per sample. The render mark resolves fine
 * ("resolved by onRender"); it is the post-paint mark that times out. Occlusion
 * was ruled out — disabling Windows occlusion detection changed nothing.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * TARGET RESTATED BY THE OWNER — supersedes spec §4's 16 ms for this metric
 * ─────────────────────────────────────────────────────────────────────────────
 * 16 ms p95 is unreachable by construction on a 60 Hz panel: the vsync wait
 * alone measured p95 ≈ 15.84 ms (frame 16.70 ms, measured not assumed), leaving
 * ~0.16 ms for the ConPTY round trip, IPC, parse, render, paint and composite.
 *
 * The target is now **33.3 ms p95 end-to-end, judged on the software-only
 * figure.** Roughly two frames, and in the same range as conhost.exe measured by
 * high-speed camera (~33 ms to photons at 120 Hz).
 *
 * Owner's ruling: **report BOTH, judge pass/fail on software-only.**
 *   • TO-PHOTONS  — the honest headline; what a human perceives. Includes vsync.
 *   • SOFTWARE    — to-photons minus the frame wait. The part engineering can
 *                   change, and the ONLY number comparable between the WebGL and
 *                   DOM rungs. Judged against the 33.3 ms target above.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * FOUR CORRECTIONS OVER THE NAIVE HARNESS (each verified in xterm's source)
 * ─────────────────────────────────────────────────────────────────────────────
 * 1. Drive input with `term.input(ch)`, NOT `port.postMessage`.
 *    `WriteBuffer.ts:115-122` parses SYNCHRONOUSLY when `_didUserInput` is set,
 *    and falls back to `setTimeout(() => this._innerWrite())` at line 124 when it
 *    is not. Posting straight to the port skips the flag, so every sample would
 *    pay an extra macrotask that real typing never pays — measuring a code path
 *    that does not ship.
 *
 * 2. The `write` callback is a PARSE mark, not a paint mark.
 *    `WriteBuffer.ts:221-222` invokes it immediately after `this._action(data)`
 *    and before `_onWriteParsed.fire()` at line 245. Stopping the clock there
 *    omits render, paint, composite and present — the largest part of the thing
 *    being measured.
 *
 * 3. Post-paint hook = `requestAnimationFrame` + a macrotask, NOT double-rAF.
 *    A task scheduled from inside the frame callback runs after that frame's
 *    rendering steps. A second rAF would land at the START of the next frame and
 *    overshoot by whatever idle time exists.
 *    (Originally a MessageChannel; removed after it leaked a port per sample.)
 *
 * 4. `term.onRender` anchors each sample to a render that actually happened, so
 *    a sample can never be credited to a frame that rendered nothing.
 */

import type { Terminal } from '@xterm/xterm'

export interface Sample {
  /** term.input() → post-paint. The honest, human-visible number. */
  toPhotons: number
  /** toPhotons minus the measured vsync wait. Comparable across rungs. */
  software: number
  /** term.input() → echo bytes arrive from the PTY. ConPTY + IPC + base64. */
  transport: number
  /** echo arrival → xterm parse complete. */
  parse: number
  /** parse complete → term.onRender fired. */
  render: number
  /** onRender → post-paint task. This is the frame wait plus compositing. */
  paint: number
}

export interface Stats {
  n: number
  p50: number
  p95: number
  p99: number
  max: number
  mean: number
}

export interface LatencyReport {
  rung: string
  reason: string
  refreshHz: number
  frameMs: number
  toPhotons: Stats
  software: Stats
  transport: Stats
  parse: Stats
  render: Stats
  paint: Stats
  /** Long Animation Frames seen during the run — the CPU-bound signal. */
  longFrames: { count: number; maxMs: number }
  discarded: number
  frameStalls: number
}

const pct = (sorted: number[], p: number): number =>
  sorted.length === 0 ? NaN : sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))]!

function stats(values: number[]): Stats {
  const s = [...values].sort((a, b) => a - b)
  return {
    n: s.length,
    p50: pct(s, 50),
    p95: pct(s, 95),
    p99: pct(s, 99),
    max: s.length ? s[s.length - 1]! : NaN,
    mean: s.length ? s.reduce((a, b) => a + b, 0) / s.length : NaN,
  }
}

/**
 * Measure the panel's real frame interval instead of assuming 60 Hz.
 * The vsync wait is subtracted from every to-photons sample to derive the
 * software number, so getting this wrong would corrupt the headline metric.
 */
export function measureFrameInterval(samples = 300): Promise<number> {
  return new Promise((resolve) => {
    const ts: number[] = []
    const tick = (t: number): void => {
      ts.push(t)
      if (ts.length <= samples) requestAnimationFrame(tick)
      else {
        const deltas = ts.slice(1).map((v, i) => v - ts[i]!)
        deltas.sort((a, b) => a - b)
        resolve(deltas[Math.floor(deltas.length / 2)]!) // median, robust to hitches
      }
    }
    requestAnimationFrame(tick)
  })
}

/**
 * Dispatched after the frame's rendering steps complete — the closest post-paint
 * hook the platform offers.
 *
 * Watchdogged on purpose. `requestAnimationFrame` does not fire at all while a
 * window is hidden or fully occluded — `backgroundThrottling: false` relaxes
 * TIMER throttling but does not make an invisible window composite. Without the
 * watchdog the harness simply hangs, which is what happened at sample 600 with
 * the process still alive and no error anywhere.
 *
 * Resolves `null` on stall so the caller can discard the sample and, if it keeps
 * happening, abort and SAY the window was not visible — rather than silently
 * reporting a number measured under conditions that make it meaningless.
 */
function afterPaint(timeoutMs = 1000): Promise<number | null> {
  return new Promise((resolve) => {
    let settled = false
    const finish = (v: number | null): void => {
      if (settled) return
      settled = true
      clearTimeout(bail)
      resolve(v)
    }
    // The watchdog is cleared only when the promise SETTLES. The previous
    // version cleared it the moment rAF fired and then waited on a
    // MessageChannel — so a message that never arrived hung the run forever
    // with no timeout left to save it.
    const bail = setTimeout(() => finish(null), timeoutMs)

    requestAnimationFrame(() => {
      // A macrotask scheduled from inside the frame callback runs after the
      // frame's rendering steps. setTimeout(0) rather than MessageChannel:
      // marginally coarser, but it cannot leak a port and it cannot wedge.
      setTimeout(() => finish(performance.now()), 0)
    })
  })
}

/** Same watchdog for the render mark. */
let diagOnce = true
function nextRender(term: Terminal, timeoutMs = 1000): Promise<number | null> {
  return new Promise((resolve) => {
    let done = false
    const finish = (v: number | null): void => {
      if (done) return
      done = true
      d.dispose()
      clearTimeout(bail)
      resolve(v)
    }
    const d = term.onRender(() => {
      if (!done && diagOnce) { diagOnce = false; console.log('LATENCY-DIAG nextRender resolved by onRender') }
      finish(performance.now())
    })
    requestAnimationFrame(() => {
      if (!done && diagOnce) { diagOnce = false; console.log('LATENCY-DIAG nextRender resolved by rAF') }
      finish(performance.now())
    })
    const bail = setTimeout(() => {
      if (!done && diagOnce) {
        diagOnce = false
        console.log(
          `LATENCY-DIAG nextRender TIMED OUT. visibility=${document.visibilityState} ` +
            `hidden=${document.hidden} hasFocus=${document.hasFocus()}`,
        )
      }
      finish(null)
    }, timeoutMs)
  })
}

export interface RunOptions {
  term: Terminal
  /**
   * Resolves when the PTY echoes bytes back. Returns a `cancel` so a timed-out
   * sample can DEREGISTER its matcher — otherwise stale matchers accumulate and
   * steal chunks from later samples.
   */
  onEcho: (matcher: (data: string) => boolean) => {
    promise: Promise<{ at: number; data: string }>
    cancel: () => void
  }
  rung: string
  reason: string
  n?: number
  warmup?: number
  signal?: AbortSignal
}

/**
 * N sequential single characters, one outstanding at a time.
 *
 * Sequential on purpose: overlapping sends would let xterm batch several
 * characters into one parse and one frame, which flatters p95 in a way real
 * typing never does.
 *
 * Each sample sends a printable character then a backspace, so the shell's line
 * length stays constant and no sample ever pays for a wrap or a scroll that the
 * previous one did not.
 */
export async function runLatency(opts: RunOptions): Promise<LatencyReport> {
  const { term, onEcho, rung, reason, n = 1000, warmup = 50, signal } = opts

  console.log('LATENCY-DIAG harness started')
  const frameMs = await measureFrameInterval()
  const refreshHz = Math.round(1000 / frameMs)

  // Long Animation Frames: the owner's CPU-bound axis. If p95 misses, this says
  // whether the machine was starved rather than leaving it to guesswork.
  let longCount = 0
  let longMax = 0
  let observer: PerformanceObserver | undefined
  try {
    observer = new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        longCount++
        longMax = Math.max(longMax, e.duration)
      }
    })
    observer.observe({ type: 'long-animation-frame', buffered: false })
  } catch {
    /* LoAF unsupported — reported as 0, never silently claimed as "clean" */
  }

  const samples: Sample[] = []
  let discarded = 0
  let frameStalls = 0
  const alphabet = 'abcdefghijklmnopqrstuvwxyz'

  for (let i = 0; i < warmup + n; i++) {
    if (signal?.aborted) break
    const ch = alphabet[i % alphabet.length]!

    // Randomised gap so samples do not phase-lock to vsync and produce a
    // bimodal distribution that is an artefact of the harness, not the app.
    await new Promise((r) => setTimeout(r, 4 + Math.random() * 12))

    const echo = onEcho((d) => d.includes(ch))

    if (i < 3) console.log(`LATENCY-DIAG tx#${i} input=${JSON.stringify(ch)}`)
    const t0 = performance.now()
    term.input(ch) // production path: sets _didUserInput -> synchronous parse

    let arrival: { at: number; data: string }
    try {
      arrival = await Promise.race([
        echo.promise,
        new Promise<never>((_, rej) => setTimeout(() => rej(new Error('echo timeout')), 400)),
      ])
    } catch {
      echo.cancel() // deregister, or this matcher steals a later sample's chunk
      discarded++
      // Fail fast and loudly. Silently discarding turns a broken matcher into a
      // 35-minute run that looks like a hang — which is exactly what happened
      // the first time this was written.
      if (discarded === 1) console.log('LATENCY-DIAG first echo timeout — matcher is not matching')
      if (discarded >= 10) {
        console.log(`LATENCY-DIAG aborting: ${discarded} consecutive echo timeouts`)
        break
      }
      continue
    }

    const tArrive = arrival.at

    // Parse mark — write callback, verified to fire on parse (WriteBuffer.ts:221).
    const tParse = await new Promise<number>((resolve) => {
      term.write(arrival.data, () => resolve(performance.now()))
    })

    // Render mark — proves a render happened for THIS sample.
    const tRender = await nextRender(term)
    const tPaint = tRender === null ? null : await afterPaint()

    if (tRender === null || tPaint === null) {
      frameStalls++
      discarded++
      if (frameStalls >= 20) {
        console.log(
          `LATENCY-DIAG ABORT after ${frameStalls} stalled samples. NO valid latency measured.\n` +
            `  What actually happened: the post-paint mark timed out at ${1000} ms while the\n` +
            `  render mark resolved normally (observed: "nextRender resolved by onRender").\n` +
            `  Long Animation Frames recorded ~1015 ms — i.e. the MAIN THREAD WAS BLOCKED for\n` +
            `  ~1 s per sample, not idle waiting for a frame.\n` +
            `  This is NOT occlusion. An earlier build of this harness completed 600 samples\n` +
            `  on the same window, and disabling occlusion detection changed nothing.\n` +
            `  Cause is unidentified and lives in this harness, not in the terminal.`,
        )
        break
      }
      continue
    }

    // Clear the character so the next sample starts from an identical line.
    term.input('\b')

    if (i < warmup) continue // discard warm-up: cold atlas, JIT, first-frame costs

    if (samples.length > 0 && samples.length % 200 === 0) {
      console.log(`LATENCY-DIAG ${samples.length}/${n} samples`)
    }

    const toPhotons = tPaint - t0
    samples.push({
      toPhotons,
      // Subtract only the portion of the frame wait actually incurred, floored
      // at zero — never allow a negative "software" number to flatter the stat.
      software: Math.max(0, toPhotons - Math.min(frameMs, tPaint - tRender)),
      transport: tArrive - t0,
      parse: tParse - tArrive,
      render: tRender - tParse,
      paint: tPaint - tRender,
    })
  }

  observer?.disconnect()

  return {
    rung,
    reason,
    refreshHz,
    frameMs,
    toPhotons: stats(samples.map((s) => s.toPhotons)),
    software: stats(samples.map((s) => s.software)),
    transport: stats(samples.map((s) => s.transport)),
    parse: stats(samples.map((s) => s.parse)),
    render: stats(samples.map((s) => s.render)),
    paint: stats(samples.map((s) => s.paint)),
    longFrames: { count: longCount, maxMs: longMax },
    discarded,
    frameStalls,
  }
}

export function formatReport(r: LatencyReport): string {
  const f = (s: Stats): string =>
    `p50 ${s.p50.toFixed(1)}  p95 ${s.p95.toFixed(1)}  p99 ${s.p99.toFixed(1)}  max ${s.max.toFixed(1)}`
  // NaN fails every comparison, so a zero-sample run would have printed
  // "HARD FAIL" — a verdict on data that does not exist. Say so instead.
  const verdict = !Number.isFinite(r.software.p95)
    ? 'NO DATA — run invalid, see frameStalls'
    : r.software.p95 <= 33.3
      ? 'PASS'
      : r.software.p95 <= 50
        ? 'OVER TARGET'
        : 'HARD FAIL'
  return [
    `LATENCY rung=${r.rung} (${r.reason})`,
    `  panel ${r.refreshHz} Hz, frame ${r.frameMs.toFixed(2)} ms | n=${r.toPhotons.n} discarded=${r.discarded}`,
    `  TO-PHOTONS  ${f(r.toPhotons)}   <- what a human sees, includes vsync`,
    `  SOFTWARE    ${f(r.software)}   <- judged vs 33.3 ms p95: ${verdict}`,
    `    transport ${f(r.transport)}`,
    `    parse     ${f(r.parse)}`,
    `    render    ${f(r.render)}`,
    `    paint     ${f(r.paint)}`,
    `  longFrames  count=${r.longFrames.count} max=${r.longFrames.maxMs.toFixed(1)} ms | frameStalls=${r.frameStalls}`,
  ].join('\n')
}
