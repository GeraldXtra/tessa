/**
 * apps/console/src/pty-host/index.ts — runs inside an Electron utilityProcess.
 *
 * ORDER MATTERS IN THIS FILE.
 *
 * The `worker_threads` probe below runs BEFORE @lydell/node-pty is imported,
 * and that is deliberate. node-pty constructs a Worker for its Windows conout
 * connection on every spawn, so if `new Worker()` throws here, node-pty cannot
 * be hosted in a utilityProcess at all. Importing node-pty first would surface
 * that as an opaque failure deep inside a spawn; probing first turns it into a
 * clean, early, actionable signal that main can act on by dropping down a rung
 * (see src/shared/pty-ipc.ts).
 *
 * This file is built as a SECOND INPUT on the MAIN electron-vite config, not as
 * its own config section, so it inherits main's externalization and node-pty
 * stays external rather than being bundled — which would break its runtime
 * binary resolution.
 */

import { Worker } from 'node:worker_threads'
import type { HostToMain, MainToHost, PtyFromHost, PtyToHost } from '../shared/pty-ipc.ts'

const parent = process.parentPort

function toMain(msg: HostToMain): void {
  parent.postMessage(msg)
}

/**
 * Construct a trivial Worker and confirm it actually starts.
 *
 * Deliberately does more than `new Worker()` in a try/catch: a Worker can be
 * constructed and then fail asynchronously on the V8-platform error, so this
 * waits for the worker to post back before calling it a pass.
 */
function probeWorker(): Promise<{ ok: boolean; error?: string; ms: number }> {
  const t0 = process.hrtime.bigint()
  const ms = (): number => Number(process.hrtime.bigint() - t0) / 1e6

  return new Promise((resolve) => {
    let settled = false
    const done = (ok: boolean, error?: string): void => {
      if (settled) return
      settled = true
      resolve({ ok, error, ms: ms() })
    }

    let w: Worker
    try {
      // eval:true keeps the probe self-contained — no extra file to resolve,
      // which matters because path resolution inside a packaged app is exactly
      // the sort of thing that fails for unrelated reasons and muddies the signal.
      w = new Worker('require("worker_threads").parentPort.postMessage("ok")', {
        eval: true,
      })
    } catch (err) {
      // The synchronous V8-platform throw.
      done(false, `construct threw: ${(err as Error).message}`)
      return
    }

    w.once('message', (m) => {
      void w.terminate()
      done(m === 'ok', m === 'ok' ? undefined : `unexpected message: ${String(m)}`)
    })
    w.once('error', (err: Error) => done(false, `worker error: ${err.message}`))
    w.once('exit', (code) => {
      if (!settled) done(false, `worker exited early with code ${code}`)
    })

    setTimeout(() => done(false, 'worker probe timed out after 5000ms'), 5000).unref()
  })
}

async function main(): Promise<void> {
  const probe = await probeWorker()
  toMain({ t: 'probe', workerOk: probe.ok, error: probe.error, ms: probe.ms })

  if (!probe.ok) {
    // Do not touch node-pty. Main decides which rung to try next.
    return
  }

  // Only now is it safe to load the native addon.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const pty = require('@lydell/node-pty') as typeof import('@lydell/node-pty')

  let term: import('@lydell/node-pty').IPty | null = null

  parent.on('message', (e) => {
    const msg = e.data as MainToHost

    if (msg?.t === 'shutdown') {
      // Kill the shell BEFORE exiting, or cmd.exe and conhost.exe orphan.
      try {
        term?.kill()
      } catch {
        /* already dead */
      }
      term = null
      setTimeout(() => process.exit(0), 150)
      return
    }

    if (msg?.t !== 'spawn') return

    const port = e.ports[0]
    if (!port) {
      toMain({ t: 'spawn-failed', message: 'no MessagePort transferred with spawn' })
      return
    }

    try {
      term = pty.spawn(msg.shell, msg.args, {
        name: 'xterm-256color',
        cols: msg.cols,
        rows: msg.rows,
        cwd: msg.cwd,
        env: process.env as Record<string, string>,
      })
    } catch (err) {
      toMain({ t: 'spawn-failed', message: (err as Error).message })
      return
    }

    const send = (m: PtyFromHost): void => port.postMessage(m)

    port.start()
    send({ t: 'ready', pid: term.pid })
    toMain({ t: 'spawned', pid: term.pid })

    term.onData((d) => send({ t: 'data', b64: Buffer.from(d, 'utf8').toString('base64') }))
    term.onExit(({ exitCode, signal }) => send({ t: 'exit', code: exitCode, signal }))

    port.on('message', (ev) => {
      const cmd = ev.data as PtyToHost
      if (!term) return
      switch (cmd.t) {
        case 'write':
          term.write(Buffer.from(cmd.b64, 'base64').toString('utf8'))
          break
        case 'resize':
          // Guard: node-pty throws on non-positive dimensions, and a renderer
          // mid-layout can legitimately report 0 before first paint.
          if (cmd.cols > 0 && cmd.rows > 0) term.resize(cmd.cols, cmd.rows)
          break
        case 'kill':
          term.kill()
          break
      }
    })
  })
}

void main().catch((err: unknown) => {
  toMain({ t: 'spawn-failed', message: `pty-host fatal: ${String(err)}` })
})
