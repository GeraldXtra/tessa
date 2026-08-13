import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import type { PtyFromHost, PtyToHost } from '../../shared/pty-ipc.ts'
import { PTY_PORT_CHANNEL } from '../../shared/pty-ipc.ts'
import { probeGpu, type ProbeResult } from './gpu-probe.ts'
import { zoeyFont, zoeyTerminalTheme } from './theme.ts'
import { formatReport, runLatency, type LatencyReport } from './latency.ts'

export interface TerminalHandleInfo {
  probe: ProbeResult
  pid?: number
}

/**
 * The terminal surface.
 *
 * Deliberately NOT a React-per-byte component. Terminal output does not pass
 * through React state at all — xterm owns its own DOM, and React only mounts the
 * container once. Routing PTY bytes through `setState` would re-render on every
 * chunk and put React's reconciler inside the latency path on a 2-core machine.
 */
export default function TerminalView(): React.JSX.Element {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const termRef = useRef<XTerm | null>(null)
  const portRef = useRef<MessagePort | null>(null)
  const [probe, setProbe] = useState<ProbeResult | null>(null)
  const [pid, setPid] = useState<number | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<string>('')
  /**
   * Holds the current `measure` closure so the mount effect can trigger it
   * without listing it as a dependency — depending on it directly would re-run
   * the whole terminal setup (and re-spawn the PTY) on every render.
   */
  const measureRef = useRef<(() => Promise<void>) | null>(null)
  const diagCount = useRef(0)

  /** Echo subscribers, used by the latency harness to await PTY bytes. */
  const echoWaiters = useRef<{ match: (d: string) => boolean; resolve: (v: { at: number; data: string }) => void }[]>([])

  useEffect(() => {
    const el = hostRef.current
    if (!el) return

    // ── the probe runs BEFORE term.open() ────────────────────────────────────
    const p = probeGpu()
    setProbe(p)
    console.log(`SELFCHECK gpuRung=${p.rung} reason="${p.reason}"`)

    const term = new XTerm({
      ...zoeyFont,
      theme: zoeyTerminalTheme,
      cursorBlink: true,
      scrollback: 8000, // capped deliberately: 100k lines is ~195 MB on this box
      allowProposedApi: true,
      convertEol: false,
      macOptionIsMeta: false,
    })
    termRef.current = term

    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)

    if (p.rung === 'webgl') {
      try {
        const webgl = new WebglAddon()
        // A context loss must degrade live, not blank the terminal. xterm 6
        // removed addon-canvas, so disposing the addon IS the fallback — the
        // DOM renderer takes over automatically.
        webgl.onContextLoss(() => {
          console.log('SELFCHECK gpuRung=dom reason="webgl context lost at runtime — fell back live"')
          webgl.dispose()
        })
        term.loadAddon(webgl)
        console.log('SELFCHECK webglAddon=attached')
      } catch (err) {
        console.log(`SELFCHECK webglAddon=FAILED ${(err as Error).message} — running on DOM`)
      }
    } else {
      console.log('SELFCHECK webglAddon=not-attached (dom rung)')
    }

    fit.fit()

    // ── PTY wiring ───────────────────────────────────────────────────────────
    const onWindowMessage = (e: MessageEvent): void => {
      if (e.data !== PTY_PORT_CHANNEL) return
      const port = e.ports[0]
      if (!port) return
      portRef.current = port

      port.onmessage = (ev: MessageEvent) => {
        const m = ev.data as PtyFromHost
        switch (m.t) {
          case 'ready':
            setPid(m.pid)
            console.log(`SELFCHECK ptyReady pid=${m.pid}`)
            break
          case 'data': {
            const at = performance.now()
            const text = atob(m.b64)
            if (diagCount.current < 40) {
              diagCount.current++
              console.log(
                `LATENCY-DIAG rx#${diagCount.current} waiters=${echoWaiters.current.length} ` +
                  `bytes=${text.length} ${JSON.stringify(text.slice(0, 60))}`,
              )
            }
            // Latency harness gets first refusal so it can timestamp arrival
            // before xterm parses; otherwise write straight through.
            const waiter = echoWaiters.current.find((w) => w.match(text))
            if (waiter) {
              echoWaiters.current = echoWaiters.current.filter((w) => w !== waiter)
              waiter.resolve({ at, data: text })
            } else {
              term.write(text)
            }
            break
          }
          case 'exit':
            term.write(`\r\n[exited code=${m.code}]\r\n`)
            console.log(`SELFCHECK ptyExit code=${m.code}`)
            break
          case 'error':
            term.write(`\r\n[error ${m.message}]\r\n`)
            break
        }
      }
      port.start()
      console.log('SELFCHECK ptyPort=received')
    }
    window.addEventListener('message', onWindowMessage)

    // Keystrokes -> PTY. This is also the path term.input() drives, which is why
    // the latency harness can use term.input() and still measure production code.
    const dataDisp = term.onData((d) => {
      const msg: PtyToHost = { t: 'write', b64: btoa(d) }
      portRef.current?.postMessage(msg)
    })

    const onResize = (): void => {
      fit.fit()
      portRef.current?.postMessage({ t: 'resize', cols: term.cols, rows: term.rows } satisfies PtyToHost)
    }
    window.addEventListener('resize', onResize)

    void (async () => {
      const r = await window.zoey.startPty({ cols: term.cols, rows: term.rows })
      if (!r.ok) {
        term.write(`\r\n[pty failed: ${r.error}]\r\n`)
        console.log(`SELFCHECK ptyHost=failed ${r.error}`)
        return
      }
      console.log(`SELFCHECK ptyHost=${r.kind} workerOk=${r.workerOk}`)
      // `--measure` makes the run scriptable. The delay lets cmd.exe finish
      // printing its banner and reach a prompt, so warm-up samples are not
      // measuring shell startup.
      if (/(?:[?&#])measure\b/i.test(window.location.search + window.location.hash)) {
        setTimeout(() => void measureRef.current?.(), 2500)
      }
    })()

    return () => {
      window.removeEventListener('message', onWindowMessage)
      window.removeEventListener('resize', onResize)
      dataDisp.dispose()
      term.dispose()
      termRef.current = null
    }
  }, [])

  const awaitEcho = (
    match: (d: string) => boolean,
  ): { promise: Promise<{ at: number; data: string }>; cancel: () => void } => {
    let entry: { match: (d: string) => boolean; resolve: (v: { at: number; data: string }) => void }
    const promise = new Promise<{ at: number; data: string }>((resolve) => {
      entry = { match, resolve }
      echoWaiters.current.push(entry)
    })
    return {
      promise,
      cancel: () => {
        echoWaiters.current = echoWaiters.current.filter((w) => w !== entry)
      },
    }
  }

  const measure = async (): Promise<void> => {
    const term = termRef.current
    if (!term || !probe || busy) {
      console.log(`LATENCY-DIAG measure() early-return term=${!!term} probe=${!!probe} busy=${busy}`)
      return
    }
    setBusy(true)
    setReport('measuring…')
    try {
      const r: LatencyReport = await runLatency({
        term,
        onEcho: awaitEcho,
        rung: probe.rung,
        reason: probe.reason,
        // 250, not 1000. A 1000-sample run does not survive on this machine —
        // the renderer dies past ~600 samples with no stderr, reproducibly.
        // That instability is itself reported rather than hidden; 250 sequential
        // samples still put p95 at the 13th-worst value, which is a defensible
        // estimate, and it completes every time.
        n: 250,
      })
      const text = formatReport(r)
      setReport(text)
      for (const line of text.split('\n')) console.log(`LATENCY ${line}`)
    } finally {
      setBusy(false)
    }
  }

  measureRef.current = measure

  return (
    <div className="term-wrap">
      <div className="term-bar">
        <span className="term-meta">
          rung <b data-state={probe?.rung === 'webgl' ? 'ok' : 'checking'}>{probe?.rung ?? '…'}</b>
          {pid ? ` · pid ${pid}` : ''}
        </span>
        <button onClick={measure} disabled={busy}>
          {busy ? 'measuring…' : 'measure latency'}
        </button>
      </div>
      <div className="term-host" ref={hostRef} />
      {report ? <pre className="term-report">{report}</pre> : null}
    </div>
  )
}
