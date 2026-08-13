import { useEffect, useState } from 'react'
import TerminalView from './terminal/Terminal.tsx'

type Bridge = 'checking' | 'ok' | 'failed'

/**
 * Step 3 — xterm on the real PTY.
 *
 * The Step 2 scaffolding is deliberately GONE: the `<pre>` byte sink, the
 * per-byte React state, and the self-driving auto-start/echo/resize timers.
 * All three were confounds for the latency harness — React re-rendering on
 * every PTY chunk put the reconciler inside the measurement path, and the
 * background timers fired mid-sample. Their job (proving the transport) is
 * done and is now covered by the SELFCHECK lines the terminal emits.
 */
export default function App(): React.JSX.Element {
  const [bridge, setBridge] = useState<Bridge>('checking')

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <main className="shell">
      <header className="top">
        <h1 className="brand">ZOEY CONSOLE</h1>
        <span className="phase">
          PHASE 1A · STEP 3 · bridge <b data-state={bridge}>{bridge}</b> · node{' '}
          <b data-state={nodeLeaks.length === 0 ? 'ok' : 'failed'}>
            {nodeLeaks.length === 0 ? 'sandboxed' : 'LEAKED'}
          </b>
        </span>
      </header>
      <TerminalView />
    </main>
  )
}
