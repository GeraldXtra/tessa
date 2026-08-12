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

import { join } from 'node:path'
import { app, BrowserWindow, ipcMain, shell } from 'electron'
// The window's background is set before any CSS loads, so it cannot come from
// tokens.css — it has to be read from the token SOURCE. @zoey/tokens is a
// devDependency, so electron-vite bundles this JSON into main rather than
// leaving a runtime require. No hex literal ever appears in this file.
import tokens from '@zoey/tokens'
import { shutdownPtyHost, startPty } from './pty-host.ts'

const isDev = !app.isPackaged

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

  if (isDev && process.env['ELECTRON_RENDERER_URL']) {
    void win.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    void win.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return win
}

app.whenReady().then(() => {
  // Step 1 liveness probe for the contextBridge.
  ipcMain.handle('zoey:ping', () => 'pong')

  const win = createWindow()

  // ── Step 2 ────────────────────────────────────────────────────────────────
  // The renderer asks for a terminal; main runs the Worker probe, forks the
  // host, and hands the renderer a MessagePort. Main never sees the bytes.
  //
  // NOTE: in Step 4 this becomes illegal as written. CONTRACT §6.5 requires a
  // daemon grant (cmd.pty.requestSpawn → res.pty.grant) BEFORE any PTY exists.
  // Right now nothing gates this, which is fine only because Step 2 has no
  // daemon wired yet — and is exactly the gap Step 4 closes.
  ipcMain.handle('zoey:pty-start', async (_e, dims: { cols: number; rows: number }) => {
    try {
      const result = await startPty(win, {
        shell: process.env['COMSPEC'] ?? 'cmd.exe',
        args: [],
        cwd: app.getPath('home'),
        cols: dims?.cols ?? 80,
        rows: dims?.rows ?? 24,
      })
      return { ok: true as const, ...result }
    } catch (err) {
      return { ok: false as const, error: (err as Error).message }
    }
  })

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
  void shutdownPtyHost().finally(() => app.quit())
})

app.on('window-all-closed', () => {
  // Windows/Linux convention. The daemon's lifecycle is separate and is dealt
  // with in Step 4 — closing the Console must never kill a daemon it did not
  // start, because the Orb polls for that same daemon.
  if (process.platform !== 'darwin') app.quit()
})
