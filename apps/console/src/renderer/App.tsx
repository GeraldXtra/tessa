import { useCallback, useEffect, useRef, useState } from 'react'
import { PTY_PORT_CHANNEL, type PtyFromHost, type PtyToHost } from '../shared/pty-ipc.ts'

type Bridge = 'checking' | 'ok' | 'failed'

/**
 * Step 2 — prove the PTY reaches the renderer over a MessagePort.
 *
 * Deliberately NOT xterm. That is Step 3. This renders raw bytes into a <pre>
 * so that if something is wrong, it is wrong in the transport and not hidden
 * behind a terminal emulator's own parsing.
 */
export default function App(): React.JSX.Element {
  const [bridge, setBridge] = useState<Bridge>('checking')
  const [host, setHost] = useState<string>('not started')
  const [probe, setProbe] = useState<string>('—')
  const [out, setOut] = useState<string>('')
  const portRef = useRef<MessagePort | null>(null)
  const preRef = useRef<HTMLPreElement | null>(null)
  // Mirrors `out` so setTimeout closures read the CURRENT value. Without this
  // the echo assertion captures out='' at effect time and always reports MISSING.
  const outRef = useRef<string>('')

  const nodeLeaks = (['require', 'process', 'module', 'global', 'Buffer'] as const).filter(
    (k) => k in globalThis,
  )

  useEffect(() => {
    window.zoey
      ?.ping()
      .then((r) => {
        const s: Bridge = r === 'pong' ? 'ok' : 'failed'
        setBridge(s)
        console.log(`SELFCHECK contextBridge=${s}`)
      })
      .catch(() => setBridge('failed'))

    console.log(
      nodeLeaks.length === 0
        ? 'SELFCHECK nodeAccess=none (sandboxed)'
        : `SELFCHECK nodeAccess=LEAKED ${nodeLeaks.join(',')}`,
    )

    // The port arrives via window.postMessage, not contextBridge — a
    // MessagePort cannot cross the isolated-world boundary as a bridged value.
    const onWindowMessage = (e: MessageEvent): void => {
      if (e.data !== PTY_PORT_CHANNEL) return
      const port = e.ports[0]
      if (!port) return
      portRef.current = port
      port.onmessage = (ev: MessageEvent) => {
        const m = ev.data as PtyFromHost
        switch (m.t) {
          case 'ready':
            console.log(`SELFCHECK ptyReady pid=${m.pid}`)
            break
          case 'data': {
            const chunk = atob(m.b64)
            outRef.current = (outRef.current + chunk).slice(-4000)
            setOut(outRef.current)
            break
          }
          case 'exit':
            setOut((prev) => `${prev}\n[exited code=${m.code}]`)
            console.log(`SELFCHECK ptyExit code=${m.code}`)
            break
          case 'error':
            setOut((prev) => `${prev}\n[error ${m.message}]`)
            break
        }
      }
      port.start()
      console.log('SELFCHECK ptyPort=received')
    }

    window.addEventListener('message', onWindowMessage)
    return () => window.removeEventListener('message', onWindowMessage)
  }, [])

  useEffect(() => {
    if (preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight
  }, [out])

  const send = (m: PtyToHost): void => portRef.current?.postMessage(m)

  const start = useCallback(async () => {
    setHost('starting…')
    const r = await window.zoey.startPty({ cols: 100, rows: 24 })
    if (r.ok) {
      setHost(`${r.kind}${r.pid ? ` pid=${r.pid}` : ''}`)
      setProbe(`worker_threads PASSED (${r.probeMs.toFixed(0)} ms)`)
      console.log(`SELFCHECK ptyHost=${r.kind} workerOk=${r.workerOk}`)
    } else {
      setHost('FAILED')
      setProbe(r.error)
      console.log(`SELFCHECK ptyHost=failed ${r.error}`)
    }
  }, [])

  const runEcho = (): void => send({ t: 'write', b64: btoa('echo ZOEY_STEP2_OK\r') })

  // Self-driving verification so the probe, spawn, echo and resize can be
  // checked from a log without a human clicking. Declared HERE, after `start`
  // and `send` exist — placing it above them is a temporal-dead-zone crash that
  // TypeScript catches as TS2448 and React reports as "Cannot access 'start'
  // before initialization". Removed in Step 3, when xterm owns the session.
  useEffect(() => {
    const t = setTimeout(() => void start(), 600)
    return () => clearTimeout(t)
  }, [start])

  useEffect(() => {
    if (!host.startsWith('utilityProcess')) return
    const a = setTimeout(runEcho, 700)
    const b = setTimeout(() => send({ t: 'resize', cols: 120, rows: 40 }), 1400)
    const c = setTimeout(
      () =>
        console.log(
          outRef.current.includes('ZOEY_STEP2_OK')
            ? 'SELFCHECK ptyEcho=ok'
            : `SELFCHECK ptyEcho=MISSING (${outRef.current.length} bytes seen)`,
        ),
      2400,
    )
    return () => {
      clearTimeout(a)
      clearTimeout(b)
      clearTimeout(c)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host])

  return (
    <main className="shell">
      <h1 className="brand">ZOEY CONSOLE</h1>
      <p className="phase">PHASE 1A · STEP 2 — PTY OVER MESSAGEPORT</p>

      <dl className="facts">
        <dt>contextBridge</dt>
        <dd data-state={bridge}>{bridge}</dd>

        <dt>renderer node access</dt>
        <dd data-state={nodeLeaks.length === 0 ? 'ok' : 'failed'}>
          {nodeLeaks.length === 0 ? 'none — sandboxed' : `LEAKED: ${nodeLeaks.join(', ')}`}
        </dd>

        <dt>pty host</dt>
        <dd data-state={host.startsWith('utilityProcess') ? 'ok' : host === 'FAILED' ? 'failed' : 'checking'}>
          {host}
        </dd>

        <dt>worker probe</dt>
        <dd>{probe}</dd>
      </dl>

      <div className="row">
        <button onClick={start}>start pty</button>
        <button onClick={runEcho}>echo</button>
        <button onClick={() => send({ t: 'resize', cols: 120, rows: 40 })}>resize</button>
        <button onClick={() => send({ t: 'kill' })}>kill</button>
      </div>

      <pre className="out" ref={preRef}>
        {out || '(no output yet — press “start pty”)'}
      </pre>
    </main>
  )
}
