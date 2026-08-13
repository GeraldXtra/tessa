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
      //
      // `term.kill()` alone is NOT sufficient on Windows — measured, twice: a
      // revoke reported success while the shell (pid 14764) was still alive,
      // and killing the host process does not reap it either. What freed the
      // tree in Step 2 was the whole app exiting, which masked this.
      //
      // So the pid is reaped explicitly with `taskkill /T` (whole tree, so
      // conhost goes too). This is the Job-Object problem in miniature; a real
      // Job Object with KILL_ON_JOB_CLOSE is the Phase-2 answer.
      const pid = term?.pid
      if (typeof pid === 'number') {
        try {
          const { execFile } = require('node:child_process') as typeof import('node:child_process')
          // ORDER MATTERS. taskkill runs BEFORE term.kill().
          //
          // The other way round — measured — produces an EMPTY claim list:
          // term.kill() closes the pseudoconsole, the grandchild dies of
          // CTRL_CLOSE_EVENT, and taskkill then reports "process not found"
          // on stderr with nothing on stdout. Main is left believing the tree
          // was just the shell, and a surviving grandchild would sit outside
          // the set it must observe before reporting `killed`.
          execFile('taskkill', ['/F', '/T', '/PID', String(pid)], (_err, stdout) => {
            try {
              term?.kill()
            } catch {
              /* taskkill already got it */
            }
            term = null
            // taskkill /T names every process it terminated, e.g.
            //   SUCCESS: The process with PID 24476 (child process of PID 18792) ...
            // That list is the only cheap enumeration of the tree we get, and
            // main needs it: the grandchild is invisible to main otherwise.
            const claimed = new Set<number>([pid])
            for (const m of String(stdout ?? '').matchAll(/PID (\d+)/g)) {
              if (m[1]) claimed.add(Number(m[1]))
            }
            toMain({ t: 'reaped', pids: [...claimed] })
            // Long enough for the message to leave before the process does.
            setTimeout(() => process.exit(0), 120)
          })
          return
        } catch {
          /* fall through to the plain exit below */
        }
      }
      // Fallback: no pid, or taskkill could not be launched at all. Do what can
      // still be done, and let main's ladder observe the result either way.
      try {
        term?.kill()
      } catch {
        /* already dead */
      }
      term = null
      setTimeout(() => process.exit(0), 150)
      return
    }

    // DEV HARNESS ONLY. Same term.write() the renderer's keystrokes reach.
    if (msg?.t === 'devInput') {
      try {
        term?.write(Buffer.from(msg.b64, 'base64').toString('utf8'))
      } catch (err) {
        toMain({ t: 'log', message: `devInput failed: ${(err as Error).message}` })
      }
      return
    }

    if (msg?.t === 'devResize') {
      try {
        if (msg.cols > 0 && msg.rows > 0) term?.resize(msg.cols, msg.rows)
        toMain({ t: 'log', message: `devResize -> ${msg.cols}x${msg.rows}` })
      } catch (err) {
        toMain({ t: 'log', message: `devResize failed: ${(err as Error).message}` })
      }
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
        // TERM and COLORTERM are set HERE because ConPTY does not set them.
        //
        // node-pty's `name` option sets the pty's terminal TYPE; on Windows it
        // does not export anything into the child's environment, and
        // `process.env` has no TERM on Windows. Measured consequence: a raw tee
        // of a real `claude` session contained ZERO colour of any depth — no
        // 38;2 truecolor, no 38;5 palette, not even basic SGR 30-37. The program
        // detected no colour support and correctly emitted none. Every
        // colour-capable program in the Console was rendering monochrome.
        //
        // COLORTERM=truecolor is claimed on evidence, not hope: xterm 6.0.0's
        // public cell API exposes `isFgRGB()`/`isBgRGB()` and documents the
        // value as "a hex value representing a 'true color': 0xRRGGBB"
        // (typings/xterm.d.ts), and addon-webgl resolves cells through
        // `toColorRGB`. The 24-bit path exists in both the buffer model and the
        // GPU renderer on this machine.
        //
        // This is UPSTREAM of the missing 16-colour ANSI ramp in
        // packages/tokens: that decides which colours xterm paints, this decides
        // whether a program asks for colour at all. The ramp stays on xterm's
        // built-in defaults per Gerald's Phase 1a ruling.
        env: { ...process.env, TERM: 'xterm-256color', COLORTERM: 'truecolor' } as Record<
          string,
          string
        >,
      })
    } catch (err) {
      toMain({ t: 'spawn-failed', message: (err as Error).message })
      return
    }

    const send = (m: PtyFromHost): void => port.postMessage(m)

    port.start()
    send({ t: 'ready', pid: term.pid })

    // DEV HARNESS ONLY: delay the `spawned` reply so main's spawn timeout can be
    // exercised for real. Absent in every normal launch.
    const announce = (): void => toMain({ t: 'spawned', pid: term?.pid ?? -1 })
    if (typeof msg.stallSpawnMs === 'number' && msg.stallSpawnMs > 0) {
      toMain({ t: 'log', message: `DEV: stalling 'spawned' by ${msg.stallSpawnMs} ms` })
      setTimeout(announce, msg.stallSpawnMs)
    } else {
      announce()
    }

    // DEV HARNESS ONLY: tee, not redirect. The renderer still gets every byte.
    let tee: ((d: string) => void) | null = null
    if (msg.capturePath) {
      const fs = require('node:fs') as typeof import('node:fs')
      const fd = fs.openSync(msg.capturePath, 'a')
      tee = (d: string) => {
        try {
          fs.writeSync(fd, Buffer.from(d, 'utf8'))
        } catch {
          /* evidence capture must never break the terminal */
        }
      }
      toMain({ t: 'log', message: `DEV: teeing PTY output to ${msg.capturePath}` })
    }

    term.onData((d) => {
      tee?.(d)
      send({ t: 'data', b64: Buffer.from(d, 'utf8').toString('base64') })
    })
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
