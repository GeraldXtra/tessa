/**
 * apps/console/src/main/pty-host.ts — supervises the PTY host and owns rung selection.
 *
 * Main never sees terminal bytes. It forks the host, runs the Worker probe,
 * mints a MessageChannel, and hands one end to the host and the other to the
 * renderer. After that the bytes flow host → MessagePort → renderer directly.
 * That is CONTRACT §4.2's "the Console owns the byte stream", applied one level
 * down: even inside the Console, the byte stream avoids the coordinating process.
 */

import { join } from 'node:path'
import { MessageChannelMain, utilityProcess, type BrowserWindow, type UtilityProcess } from 'electron'
import type { HostToMain, MainToHost, PtyHostKind } from '../shared/pty-ipc.ts'
import { PTY_PORT_CHANNEL } from '../shared/pty-ipc.ts'

export interface PtyHostResult {
  kind: PtyHostKind
  workerOk: boolean
  probeMs: number
  probeError?: string
  pid?: number
}

const PROBE_TIMEOUT_MS = 10_000
const SHUTDOWN_GRACE_MS = 1_500

/**
 * The live host, tracked so it can be torn down on quit.
 *
 * This matters more on Windows than it looks. Killing the host process does NOT
 * reap the PTY's children: `cmd.exe` and its `conhost.exe` survive as orphans.
 * Verified during Step 2 — a force-kill of Electron left 2 cmd.exe and 4
 * conhost.exe behind. The shell must be told to die, in order, before the host
 * goes away.
 */
let activeChild: UtilityProcess | null = null

function log(msg: string): void {
  console.log(`[pty-host] ${msg}`)
}

/**
 * Graceful teardown: ask the host to kill its PTY, give it a moment, then kill
 * the host itself. Idempotent and safe to call when nothing is running.
 */
export async function shutdownPtyHost(): Promise<void> {
  const child = activeChild
  if (!child) return
  activeChild = null

  try {
    child.postMessage({ t: 'shutdown' })
  } catch {
    // Host already gone; fall through to the kill below.
  }

  await new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, SHUTDOWN_GRACE_MS)
    child.once('exit', () => {
      clearTimeout(timer)
      resolve()
    })
  })

  try {
    child.kill()
  } catch {
    /* already exited */
  }
  log('host torn down')
}

/**
 * Fork the utilityProcess and wait for its Worker probe verdict.
 *
 * Resolves with the child and the verdict. The child is NOT killed on a failed
 * probe — main decides what to do, and killing here would race that decision.
 */
function forkAndProbe(): Promise<{ child: UtilityProcess; probe: Extract<HostToMain, { t: 'probe' }> }> {
  return new Promise((resolve, reject) => {
    // Built as a second input on the main config, so it lands beside index.js.
    const entry = join(__dirname, 'pty-host.js')

    const child = utilityProcess.fork(entry, [], {
      serviceName: 'zoey-pty-host',
      // stdio inherit so a native-module load failure prints somewhere visible
      // instead of vanishing.
      stdio: 'inherit',
    })

    const timer = setTimeout(() => {
      reject(new Error(`pty-host did not report a probe verdict within ${PROBE_TIMEOUT_MS}ms`))
    }, PROBE_TIMEOUT_MS)

    child.on('message', (msg: HostToMain) => {
      if (msg?.t === 'probe') {
        clearTimeout(timer)
        resolve({ child, probe: msg })
      } else if (msg?.t === 'log') {
        log(msg.message)
      } else if (msg?.t === 'spawn-failed') {
        log(`spawn-failed: ${msg.message}`)
      }
    })

    child.on('exit', (code) => {
      clearTimeout(timer)
      reject(new Error(`pty-host exited before probing (code ${code})`))
    })
  })
}

/**
 * Start a PTY and wire it straight to the renderer.
 *
 * THE GATE: if the Worker probe fails, this throws with the reason rather than
 * limping on. Rungs 2 and 3 (main-process host, forked Node with
 * ELECTRON_RUN_AS_NODE=1) plug in here and speak the identical MessagePort
 * protocol — see src/shared/pty-ipc.ts. They are deliberately not written yet:
 * the probe tells us empirically whether they are needed, and writing an
 * untested fallback for a problem this machine may not have is waste.
 */
export async function startPty(
  win: BrowserWindow,
  opts: { shell: string; args: string[]; cwd: string; cols: number; rows: number },
): Promise<PtyHostResult> {
  const { child, probe } = await forkAndProbe()
  activeChild = child

  log(
    probe.workerOk
      ? `worker_threads probe PASSED in utilityProcess (${probe.ms.toFixed(0)} ms)`
      : `worker_threads probe FAILED in utilityProcess: ${probe.error}`,
  )

  if (!probe.workerOk) {
    activeChild = null
    child.kill()
    throw new Error(
      `utilityProcess cannot host node-pty (worker_threads unavailable: ${probe.error}). ` +
        `Drop to rung 2 (main-process host) or rung 3 (fork with ELECTRON_RUN_AS_NODE=1); ` +
        `both speak the same MessagePort protocol.`,
    )
  }

  // Mint the channel: one end to the host, the other to the renderer.
  const { port1, port2 } = new MessageChannelMain()

  const spawnMsg: MainToHost = {
    t: 'spawn',
    shell: opts.shell,
    args: opts.args,
    cwd: opts.cwd,
    cols: opts.cols,
    rows: opts.rows,
  }
  child.postMessage(spawnMsg, [port1])

  const pid = await new Promise<number | undefined>((resolve) => {
    const timer = setTimeout(() => resolve(undefined), 5000)
    child.on('message', (msg: HostToMain) => {
      if (msg?.t === 'spawned') {
        clearTimeout(timer)
        resolve(msg.pid)
      } else if (msg?.t === 'spawn-failed') {
        clearTimeout(timer)
        log(`spawn-failed: ${msg.message}`)
        resolve(undefined)
      }
    })
  })

  // Hand the renderer its end. From here main is out of the data path.
  win.webContents.postMessage(PTY_PORT_CHANNEL, null, [port2])

  return {
    kind: 'utilityProcess',
    workerOk: true,
    probeMs: probe.ms,
    probeError: probe.error,
    pid,
  }
}
