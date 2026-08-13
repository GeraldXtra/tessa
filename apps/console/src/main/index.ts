/**
 * apps/console/src/main/index.ts — Electron main process.
 *
 * STEP 1: a blank, hardened window. No PTY, no WebSocket yet.
 *
 * The security posture here is not decoration — it is CONTRACT §2.3. The
 * renderer is a browser context: it cannot set an arbitrary `Origin`, and any
 * token held there is one XSS away from whatever the window renders. So the
 * socket and the token live in THIS process, and the renderer reaches them only
 * through a narrow contextBridge surface.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { app, BrowserWindow, ipcMain, shell } from 'electron'
// The window's background is set before any CSS loads, so it cannot come from
// tokens.css — it has to be read from the token SOURCE. @zoey/tokens is a
// devDependency, so electron-vite bundles this JSON into main rather than
// leaving a runtime require. No hex literal ever appears in this file.
import tokens from '@zoey/tokens'
import { devResize, devType, reportPty, shutdownPtyHost, startPty, killPty } from './pty-host.ts'
import { DaemonClient } from './ws-client.ts'
import { DaemonSupervisor } from './daemon.ts'

const isDev = !app.isPackaged

/**
 * Keep painting when the window is covered.
 *
 * Windows has a native occlusion detector: when another window fully covers
 * ours, Chromium stops compositing the renderer entirely — `requestAnimationFrame`
 * simply never fires. `backgroundThrottling: false` does NOT cover this; it
 * relaxes timer throttling, not occlusion. The symptom was a latency run that
 * stalled with `frameStalls=20` and no error, on a window that had definitely
 * shown (`ready-to-show` had fired and logged).
 *
 * These must be appended BEFORE `app.whenReady()`.
 *
 * This is not only a measurement concern: a terminal streaming a long build
 * behind another window must keep rendering, or the scrollback lurches when you
 * bring it back to the front.
 */
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows')
app.commandLine.appendSwitch('disable-renderer-backgrounding')
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion')

/** Cold-start measurement — reported at first paint, not guessed. */
const t0 = process.hrtime.bigint()
const msSince = (from: bigint): number => Number(process.hrtime.bigint() - from) / 1e6

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1200,
    height: 700,
    // The owner's display is 1366x768. Anything larger than this opens
    // partially offscreen, so these are ceilings, not preferences.
    minWidth: 640,
    minHeight: 400,
    show: false,
    // --bg-void, so the window never flashes white before the renderer paints
    backgroundColor: tokens.color['bg-void'].value,
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      // ── CONTRACT §7.1 non-negotiables ──────────────────────────────────
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      // No remote module, no node in workers.
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      // Chromium throttles setTimeout and requestAnimationFrame in a window
      // that is backgrounded or occluded — down to roughly 1 Hz. That silently
      // turned a ~15-samples/second latency run into ~1/second and made the
      // measurement meaningless. It also matters for the real product: a
      // terminal streaming a build must keep painting when it is not focused.
      backgroundThrottling: false,
    },
  })

  // Forward renderer console to the main log in dev. Without this, anything the
  // renderer reports — including the security self-checks in App.tsx — is
  // invisible unless devtools happen to be open.
  if (isDev) {
    // Electron 43 deprecated the positional (event, level, message) signature
    // in favour of a single event object.
    win.webContents.on('console-message', (event) => {
      console.log(`[renderer:${event.level}] ${event.message}`)
    })
  }

  win.once('ready-to-show', () => {
    win.show()
    // A latency run REQUIRES a compositing window. Chromium stops rAF entirely
    // for a hidden or fully occluded window, which produced a run that stalled
    // at sample 600 with no error. Under --measure we pin the window on top and
    // take focus so the frames the harness depends on actually happen.
    if (process.argv.includes('--measure')) {
      win.setAlwaysOnTop(true, 'screen-saver')
      win.focus()
      app.focus({ steal: true })
    }
    console.log(`[zoey-console] cold start -> first paint: ${msSince(t0).toFixed(0)} ms`)
    const mem = process.memoryUsage()
    console.log(`[zoey-console] main rss: ${(mem.rss / 1024 / 1024).toFixed(1)} MB`)
  })

  // Never let the app navigate itself somewhere else, and never open a window
  // we do not control. Both are standard Electron escape hatches.
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (event) => event.preventDefault())

  // Measurement flags are forwarded from argv into the renderer's query string
  // so the latency harness can be driven from a script instead of a click.
  // `--force-dom` proves the DOM fallback rung; `--measure` auto-runs the
  // harness once the PTY is ready. Neither is set on a normal launch, so a
  // normal launch carries none of the harness's overhead.
  const flags: string[] = []
  if (process.argv.includes('--force-dom')) flags.push('forceDom')
  if (process.argv.includes('--measure')) flags.push('measure')
  const search = flags.join('&')

  if (isDev && process.env['ELECTRON_RENDERER_URL']) {
    const url = new URL(process.env['ELECTRON_RENDERER_URL'])
    if (search) url.search = search
    void win.loadURL(url.toString())
  } else {
    void win.loadFile(join(__dirname, '../renderer/index.html'), search ? { search } : undefined)
  }

  return win
}

function logMain(message: string): void {
  console.log(`[zoey-console] ${message}`)
}

/* ═════════════════════════════════════════ DEV HARNESS — Step 5 exit criterion */

/** Read `--flag value` from argv. Dev harness only; absent in normal launches. */
function devFlag(name: string): string | undefined {
  const i = process.argv.indexOf(name)
  return i >= 0 ? process.argv[i + 1] : undefined
}

type DevStep =
  | { type: 'input'; text: string }
  | { type: 'wait'; ms: number }
  | { type: 'resize'; cols: number; rows: number }
  | { type: 'log'; msg: string }

/**
 * Drive the Console's OWN terminal from a JSON script. DEV ONLY.
 *
 * Step 5's exit criterion is that npm, pip, git and claude run in this
 * terminal — not in some other shell that happens to be on the same machine.
 * xterm's keyboard cannot be driven from outside the renderer, so the harness
 * writes to the same `term.write()` a keystroke reaches. Everything under test
 * is real: the §6.5 grant, the ConPTY, the MessagePort, xterm's rendering. Only
 * the origin of the bytes differs, and the tee proves what the PTY actually
 * emitted rather than what it was expected to.
 */
async function runDevScript(sessionId: string): Promise<void> {
  const scriptPath = devFlag('--devscript')
  if (!scriptPath) return
  let steps: DevStep[]
  try {
    steps = JSON.parse(readFileSync(scriptPath, 'utf8')) as DevStep[]
  } catch (err) {
    logMain(`devscript unreadable: ${(err as Error).message}`)
    return
  }
  logMain(`DEVSCRIPT start (${steps.length} steps) for session ${sessionId}`)
  for (const [i, step] of steps.entries()) {
    switch (step.type) {
      case 'input':
        logMain(`DEVSCRIPT[${i}] input ${JSON.stringify(step.text)}`)
        devType(sessionId, step.text)
        break
      case 'resize':
        logMain(`DEVSCRIPT[${i}] resize -> ${step.cols}x${step.rows}`)
        devResize(sessionId, step.cols, step.rows)
        break
      case 'wait':
        await new Promise((r) => setTimeout(r, step.ms))
        break
      case 'log':
        logMain(`DEVSCRIPT[${i}] ${step.msg}`)
        break
    }
  }
  logMain('DEVSCRIPT done')
}

/** Dev-only supervisor. NEVER stops a daemon it did not start — see daemon.ts. */
const supervisor = new DaemonSupervisor({
  repoRoot: join(__dirname, '..', '..', '..', '..'),
  isDev,
  log: logMain,
})

/** The ONE socket, in main. CONTRACT §2.3. */
const daemonClient = new DaemonClient({
  surfaceVersion: '0.1.0',
  log: logMain,
  onStatus: (s) => logMain(`daemon link: ${s.phase}${s.detail ? ` — ${s.detail}` : ''}`),
  onRevoke: (sessionId, reason) => {
    // CONTRACT §4.2: the Console MUST comply and report back.
    // Stamped absolutely so an external Win32_Process poller can be correlated
    // against this log without trusting either clock alone.
    logMain(`revoke received at ${new Date().toISOString()} for session ${sessionId} (${reason}) — killing`)
    void killPty(daemonClient, sessionId, reason)
  },
})

app.whenReady().then(async () => {
  // Step 1 liveness probe for the contextBridge.
  ipcMain.handle('zoey:ping', () => 'pong')

  // `--no-daemon` skips dev auto-start. Without it, "daemon down" is untestable:
  // the supervisor would simply start one and the grant gate would never see the
  // condition it exists to handle.
  //
  // ORDER MATTERS: the window is created only AFTER the daemon is up and the
  // `zoey:pty-start` handler is registered.
  //
  // It used to be created first, and that was a latent race that only showed
  // itself once the supervisor actually had to START a daemon rather than
  // attach to a running one: the renderer mounted, called `zoey:pty-start`, and
  // got `No handler registered` because registration was still behind
  // `await supervisor.ensure()`. Attaching to a live daemon returns in
  // milliseconds, which is why every previous run hid it.
  const mode = process.argv.includes('--no-daemon')
    ? ({ kind: 'unavailable', reason: 'auto-start disabled by --no-daemon' } as const)
    : await supervisor.ensure()
  logMain(`daemon mode: ${mode.kind}${mode.kind === 'unavailable' ? ` — ${mode.reason}` : ''}`)
  daemonClient.start()

  // Give the socket a moment to finish its handshake before the renderer can
  // ask for a PTY. Bounded, and it does NOT weaken the gate: if the daemon
  // never connects, the handler below still refuses — this only stops a cold
  // start from failing a request the daemon would have granted a moment later.
  const connectDeadline = Date.now() + 5_000
  while (!daemonClient.isConnected && Date.now() < connectDeadline) {
    await new Promise((r) => setTimeout(r, 100))
  }
  logMain(`daemon link before first PTY request: ${daemonClient.current.phase}`)

  // Assigned below, after the handler is registered. Declared here and read
  // through a null check rather than captured as a `const` from further down:
  // a closure referencing a not-yet-initialised block-scoped binding is a
  // temporal-dead-zone crash waiting for the first caller that arrives early,
  // and this file has already shipped that bug once (Step 2, TS2448).
  let win: BrowserWindow | null = null

  // ── THE GRANT GATE — CONTRACT §6.5 ───────────────────────────────────────
  //
  // "No PTY session may be created without a grant."
  //
  // Enforced HERE, and enforced by construction: `startPty` is called on
  // exactly one line in this file, and that line is unreachable unless
  // `cmd.pty.requestSpawn` has already returned `res.pty.grant`. Every other
  // outcome — daemon offline, denied, pending approval, malformed reply —
  // returns before it.
  //
  // The daemon enforces the other half: `cmd.pty.report{started}` redeems the
  // grant, and a `started` with no live grant is refused at red tier and
  // answered with `evt.pty.revoke` (core/server.py::_h_pty_report). So a
  // Console that skipped this gate would be caught by the daemon rather than
  // silently tolerated.
  ipcMain.handle('zoey:pty-start', async (_e, dims: { cols: number; rows: number }) => {
    const cwd = app.getPath('home')
    const profileId = 'cmd'

    if (!daemonClient.isConnected) {
      // No daemon means no grant means NO PTY. Failing closed is the point.
      const detail = daemonClient.current.detail ?? daemonClient.current.phase
      logMain(`pty-start refused: daemon not connected (${detail})`)
      return { ok: false as const, error: `Zoey Core is not connected (${detail}) — no PTY without a grant` }
    }

    // 1. ASK. Wall-clock timed so the grant round trip is a measured figure.
    const t0 = Date.now()
    let reply
    try {
      reply = await daemonClient.request('cmd.pty.requestSpawn', {
        profileId,
        cwd,
        actor: 'human',
        purpose: 'user opened a terminal',
      })
    } catch (err) {
      logMain(`pty-start refused: ${(err as Error).message}`)
      return { ok: false as const, error: `grant request failed: ${(err as Error).message}` }
    }
    const grantMs = Date.now() - t0

    // 2. HANDLE EVERY NON-GRANT OUTCOME BEFORE SPAWNING.
    if (!reply.ok || reply.type !== 'res.pty.grant') {
      const code = String(reply.payload['code'] ?? reply.type)
      const message = String(reply.payload['message'] ?? 'refused')
      if (code === 'permission.pending') {
        // Approval UI is a later phase. Do NOT spawn; log it and fail cleanly.
        logMain(`pty-start PENDING owner approval — not spawning: ${message}`)
        return { ok: false as const, error: `awaiting your approval: ${message}` }
      }
      logMain(`pty-start DENIED by the daemon (${code}): ${message}`)
      return { ok: false as const, error: `denied by Zoey Core (${code}): ${message}` }
    }

    const grantId = String(reply.payload['grantId'] ?? '')
    const sessionId = String(reply.payload['sessionId'] ?? '')
    const expiresAt = String(reply.payload['expiresAt'] ?? '')
    if (!grantId || !sessionId) {
      logMain('pty-start refused: grant reply missing grantId/sessionId')
      return { ok: false as const, error: 'malformed grant from Zoey Core' }
    }
    logMain(`GRANT ok in ${grantMs} ms — grantId=${grantId} sessionId=${sessionId} expiresAt=${expiresAt}`)

    // 3. ONLY NOW may a PTY exist.
    //
    // `--revoke-proof` is a DEV HARNESS FLAG, not a feature. It starts the shell
    // as `cmd /k ping -n 600 127.0.0.1` so the session has a real GRANDCHILD and
    // cannot exit on its own — the condition the last revoke retest lacked,
    // which is why that retest proved nothing. Materially identical to typing
    // the command; it just does not require a human at the keyboard mid-measure.
    const proofMode = process.argv.includes('--revoke-proof')
    try {
      if (!win) throw new Error('no window yet — PTY requested before the renderer existed')
      const result = await startPty(win, {
        shell: process.env['COMSPEC'] ?? 'cmd.exe',
        args: proofMode ? ['/k', 'ping -n 600 127.0.0.1'] : [],
        cwd: devFlag('--cwd') ?? cwd,
        cols: dims?.cols ?? 80,
        rows: dims?.rows ?? 24,
        sessionId,
        ...(devFlag('--stall-spawn') ? { stallSpawnMs: Number(devFlag('--stall-spawn')) } : {}),
        ...(devFlag('--capture') ? { capturePath: String(devFlag('--capture')) } : {}),
      })
      // 4. Redeem the grant. The daemon refuses a `started` with no live grant.
      //    `result.pid` is REQUIRED — startPty throws rather than returning
      //    without an observed pid, so this can no longer redeem a grant for a
      //    PTY nobody saw start.
      await reportPty(daemonClient, sessionId, 'started', result.pid)
      logMain(`STEP5 sessionId=${sessionId} grantId=${grantId} shellPid=${result.pid}`)
      void runDevScript(sessionId)
      return { ok: true as const, ...result, sessionId, grantId, grantMs, expiresAt }
    } catch (err) {
      // The PTY never came up. Release the grant rather than stranding it —
      // this is precisely why `startFailed` was added to the enum.
      const message = (err as Error).message
      logMain(`pty spawn FAILED — reporting startFailed to reclaim grant ${grantId}: ${message}`)
      await reportPty(daemonClient, sessionId, 'startFailed', message)
      return { ok: false as const, error: message }
    }
  })

  // Only now — handler registered, daemon link settled — may a window exist.
  win = createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

/**
 * Reap the PTY before quitting.
 *
 * `before-quit` fires while there is still an event loop to work with, so the
 * host can be told to kill its shell in order. Without this, cmd.exe and its
 * conhost.exe orphan — measured, not theorised: an ungraceful kill during Step 2
 * left 2 cmd.exe and 4 conhost.exe running.
 *
 * The one-shot guard is required because calling app.quit() from inside a
 * before-quit handler re-enters it.
 */
let teardownStarted = false
app.on('before-quit', (event) => {
  if (teardownStarted) return
  teardownStarted = true
  event.preventDefault()
  void shutdownPtyHost()
    .finally(() => {
      daemonClient.dispose()
      // No-op unless THIS process started the daemon. The Orb is live against
      // the same daemon; stopping one we merely attached to would sever it.
      supervisor.stop()
    })
    .finally(() => app.quit())
})

app.on('window-all-closed', () => {
  // Windows/Linux convention. The daemon's lifecycle is separate and is dealt
  // with in Step 4 — closing the Console must never kill a daemon it did not
  // start, because the Orb polls for that same daemon.
  if (process.platform !== 'darwin') app.quit()
})
