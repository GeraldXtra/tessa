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

import { readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, shell } from 'electron'
// The window's background is set before any CSS loads, so it cannot come from
// tokens.css — it has to be read from the token SOURCE. @tessa/tokens is a
// devDependency, so electron-vite bundles this JSON into main rather than
// leaving a runtime require. No hex literal ever appears in this file.
import tokens from '@tessa/tokens'
import { devResize, devType, reportPty, shutdownPtyHost, startPty, killPty } from './pty-host.ts'
import { DaemonClient } from './ws-client.ts'
import { DaemonSupervisor } from './daemon.ts'
import { describeShell, pickShell, resolveShells, type ShellId } from './shells.ts'
import { readTheme } from './theme.ts'
import { listDir } from './filetree.ts'
import { ensureSettingsFile, loadSettings } from './settings.ts'
import { startFileLog } from './log.ts'

const isDev = !app.isPackaged

/**
 * TEE EVERY DIAGNOSTIC TO DISK, BEFORE ANYTHING ELSE RUNS.
 *
 * Called at module scope, not inside `whenReady`, because the failures worth
 * catching happen earlier than that: a native module that will not load, a
 * settings file that will not parse, a second instance losing the lock. See
 * log.ts for why a packaged GUI process has nowhere else to say them.
 */
const LOG_PATH = startFileLog(`Tessa Console ${app.getVersion()} — packaged=${app.isPackaged} pid=${process.pid}`)

/* ════════════════════════════════════════════════ THE SINGLE-INSTANCE LOCK */

/**
 * The folder each NEW WINDOW was asked to open in, keyed by its webContents id
 * and consumed ONCE by that window's first shell.
 *
 * ── WHY A MAP AND NOT THE SINGLE `pendingCwd` THIS REPLACES ────────────────
 *
 * `tcli` now opens a WINDOW rather than a tab, so two windows can be starting
 * within milliseconds of each other. One shared variable would let the second
 * window's folder be consumed by the first window's shell — he would type
 * `tcli` in two folders and get two terminals in the same one, intermittently,
 * which is the worst kind of bug to be handed.
 *
 * Keyed by `event.sender.id` at the moment the shell is requested, so a window
 * can only ever consume its own.
 *
 * Still single-use per window. If it persisted, every later terminal in that
 * window would keep reopening in the folder it was launched from rather than
 * inheriting the pane he is working in.
 */
const openingCwd = new Map<number, string>()

/** Read and clear, for one window. */
function takeCwdFor(webContentsId: number): string | null {
  const c = openingCwd.get(webContentsId)
  if (c === undefined) return null
  openingCwd.delete(webContentsId)
  return c
}

/**
 * The folder the FIRST window should open in — this process's own launch
 * context. Set at module scope below, read once by `whenReady`.
 */
let firstWindowCwd: string | null = null

/**
 * A directory that actually exists and is actually local, or null.
 *
 * EVERYTHING ARRIVING HERE IS UNTRUSTED INPUT. `second-instance` hands over the
 * argv and working directory of a process this one did not start, so this is a
 * boundary, not a convenience check:
 *
 *   - It must be a DIRECTORY. A file path would be handed to the daemon as the
 *     `cwd` of a grant request and the spawn would then fail — burning a
 *     CONTRACT 6.5 grant on a shell that could never have started.
 *   - UNC paths are REFUSED, not attempted. A shell opened on a disconnected
 *     share hangs at the prompt with no way back, and the hydration rules in
 *     core/tools/files.py have no meaning off a local volume. Refusing with a
 *     reason in the log beats failing obscurely.
 */
function safeStartDir(dir: string | undefined | null): string | null {
  if (!dir) return null
  const d = String(dir).trim()
  if (!d) return null
  // TWO backslashes. This briefly held ONE, lost to shell escaping when the
  // edit was applied — which still caught UNC but also refused any path
  // rooted at a bare backslash.
  if (d.startsWith('\\\\')) {
    logMain(`tcli: refusing UNC path ${d} — open it from a local folder instead`)
    return null
  }
  try {
    if (!statSync(d).isDirectory()) {
      logMain(`tcli: ${d} is not a directory — ignoring`)
      return null
    }
  } catch (err) {
    logMain(`tcli: cannot read ${d} (${(err as Error).message}) — ignoring`)
    return null
  }
  return d
}

/**
 * ── WHY THE LOCK EXISTS, AND WHY IT IS THE FIRST THING THIS FILE DOES ───────
 *
 * Two reasons, and the second is a data risk rather than a feature.
 *
 * 1. `tcli` typed in an Explorer address bar has to open a fresh Console WINDOW
 *    rooted at the folder he was looking at. The ONLY route Electron offers to
 *    a second invocation's working directory is the `second-instance` event's
 *    third argument, and that event only fires for a lock holder.
 *
 *    ⚠ THIS ROUND REVERSED WHAT THE HANDLER DOES. It used to open a new TAB in
 *    the running window; it now opens a new WINDOW. The lock is kept anyway,
 *    and keeping it is the point: without it each `tcli` would be a separate
 *    Electron PROCESS — a second main, a second GPU process, a second utility
 *    host — on a 2-core i5-7200U. One process owning N windows costs one
 *    renderer per window instead, and keeps a single owner of the settings
 *    file. See the `second-instance` handler.
 *
 * 2. Two Consoles could both write `console-settings.json` — his 23 key
 *    bindings — with nothing arbitrating between them. Last writer won.
 *
 * ── WHY THIS DOES NOT BREAK `npm run dev` ──────────────────────────────────
 *
 * Electron keys the lock off the userData path, and MEASURED on this machine
 * those already differ:
 *
 *     dev       %APPDATA%\Electron         `electron out/main/index.js` runs
 *                                          Electron's default-app harness, so
 *                                          getName() falls back to "Electron"
 *     packaged  %APPDATA%\@tessa\console   reads resources/app.asar/package.json
 *
 * So the dev build and the installed build hold SEPARATE locks and run side by
 * side. Nothing was scoped, renamed or moved to achieve it — it was already
 * true, and measuring it first is what stopped a `userData` change that would
 * have moved where his settings are read from, for no reason at all.
 *
 * The consequence he has to be told: `tcli` launches the INSTALLED app, so
 * running it while only the DEV app is open starts the installed Console rather
 * than adding a tab to dev. Correct — they are different applications — but
 * surprising if nobody says it out loud.
 */
/**
 * ⚠ THE DEV BUILD GETS ITS OWN userData, AND THIS LINE IS WHY THE LOCK IS SAFE.
 *
 * Electron keys the single-instance lock off the userData path. MEASURING the
 * two dev entry points showed they do NOT agree with each other:
 *
 *     npm run start  ->  %APPDATA%\Electron          (`electron out/main/index.js`
 *                                                     runs Electron's default-app
 *                                                     harness; getName() falls
 *                                                     back to "Electron")
 *     npm run dev    ->  %APPDATA%\@tessa\console    (electron-vite hands Electron
 *                                                     the APP DIRECTORY, so it
 *                                                     reads apps/console/package.json)
 *     packaged       ->  %APPDATA%\@tessa\console
 *
 * So `npm run dev` collided with the INSTALLED app and `npm run start` did not.
 * Testing only the latter would have "proved" the lock was safe while leaving
 * the one command he builds every prompt with unable to start whenever the
 * installed Console happened to be open.
 *
 * Pinning a dev-only userData makes both dev entry points agree with each other
 * and disagree with the packaged app, which is exactly what is wanted.
 *
 * ⚠ THIS DOES NOT MOVE HIS SETTINGS. `console-settings.json`, `orb-theme.json`
 * and `runtime.json` all live under `%LOCALAPPDATA%\Tessa`, resolved from the
 * LOCALAPPDATA environment variable in settings.ts / theme.ts / token.ts. None
 * of them has ever been derived from `userData`. The only thing that moves is
 * the dev build's throwaway Chromium profile.
 */
if (!app.isPackaged) {
  app.setPath('userData', join(app.getPath('appData'), 'tessa-console-dev'))
}

const GOT_SINGLE_INSTANCE_LOCK = app.requestSingleInstanceLock()

if (!GOT_SINGLE_INSTANCE_LOCK) {
  // `app.exit()` and NOT `app.quit()`, deliberately. `quit()` is cooperative:
  // it fires before-quit/will-quit and RETURNS, so module evaluation would
  // continue past this line — constructing a DaemonSupervisor and a second
  // WebSocket to the daemon, and registering a whenReady handler that opens a
  // window. All of that from a process whose entire job is to hand its working
  // directory to the running Console and die. `exit()` terminates immediately.
  console.log('[tessa-console] second instance — cwd handed to the running Console, exiting')
  app.exit(0)
}

/**
 * A SECOND `tcli` — open a tab in the window that is already up.
 *
 * `workingDirectory` is the whole feature. Explorer's address bar runs a command
 * through ShellExecute with the current folder as the process working
 * directory, Electron forwards it here, and this is the only place it can be
 * read from.
 *
 * ── WHY THIS SENDS `newTab` AND SETS A VARIABLE INSTEAD OF PASSING A PATH ───
 *
 * The renderer already knows how to open a tab: `tessa:menu` carries command
 * strings and `newTab` is already one of them (App.tsx). A new IPC channel and a
 * matching renderer handler would be a second route to the same thing, and two
 * routes to one action is how the menu and the keyboard drifted apart before.
 *
 * So main seeds `pendingCwd` and then asks for a tab through the existing door.
 * The new pane calls `tessa:pty-start` milliseconds later and consumes it.
 */
app.on('second-instance', (_event, argv, workingDirectory) => {
  const asked = safeStartDir(devFlagFrom(argv, '--cwd') ?? workingDirectory)
  const target = asked ?? app.getPath('home')
  logMain(`second-instance: workingDirectory=${JSON.stringify(workingDirectory)} -> opening a NEW WINDOW at ${target}`)

  // ── A WHOLE WINDOW, NOT A TAB. THIS IS A DELIBERATE REVERSAL. ────────────
  //
  // The previous round routed a second `tcli` into the running window as a new
  // tab, and proved it. He wants the opposite: every `tcli` is a fresh Console
  // window rooted at that folder, even when one is already open.
  //
  // The single-instance LOCK IS KEPT even so, and that is the whole design.
  // Dropping it would give each `tcli` its own Electron process — a second
  // main, a second GPU process, a second utility host — on a 2-core i5-7200U.
  // Keeping it means one process owning N windows: one main, one GPU, one PTY
  // supervisor, and a renderer per window. It also keeps ONE owner of
  // console-settings.json, so his 23 bindings still have exactly one writer.
  //
  // `createWindow` raises and focuses the new window itself, and offsets it so
  // it cannot land exactly on top of the one already there.
  createWindow(target)
})

/**
 * A FIRST `tcli` — the Console was closed, so this process IS the new window.
 *
 * `process.cwd()` is the folder ShellExecute handed us, which for a `tcli` typed
 * in an address bar is exactly the folder he was looking at.
 *
 * ⚠ THE EXCLUSION IS LOAD-BEARING. Launched from the Start Menu shortcut the
 * working directory is the INSTALL DIRECTORY (measured: the .lnk's WorkingDir is
 * `...\Programs\TessaConsole`). Opening his first terminal of the day inside the
 * application's own install folder would be a regression, so a cwd equal to the
 * executable's own directory is ignored and the existing fallback — his home
 * directory — stands.
 */
{
  const explicit = devFlag('--cwd')
  const here = safeStartDir(explicit ?? process.cwd())
  const exeDir = dirname(app.getPath('exe'))
  if (here && (explicit || resolve(here).toLowerCase() !== resolve(exeDir).toLowerCase())) {
    firstWindowCwd = here
    console.log(`[tessa-console] start directory from ${explicit ? '--cwd' : 'process.cwd()'}: ${here}`)
  }
}

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

/**
 * ── THE MENU, AND WHY COPY AND PASTE APPEARED DEAD ──────────────────────────
 *
 * `autoHideMenuBar: true` HIDES the menu bar. It does not remove the menu, and
 * it does not remove its ACCELERATORS. With no menu set, Electron installs a
 * default one whose Edit roles bind Ctrl+A, Ctrl+C, Ctrl+X and Ctrl+V at the
 * BROWSER level — and browser-level accelerators are handled before the
 * keystroke ever reaches the renderer.
 *
 * In a WebGL terminal that is fatal to all four. xterm's selection is not a DOM
 * selection, so Chromium's `copy` role copies nothing and `selectAll` selects
 * nothing, while both swallow the key. That is the whole of Gerald's complaint:
 * "I copied something and I can't paste it on my custom console." The keys were
 * being eaten upstairs.
 *
 * A CUSTOM MENU RATHER THAN `setApplicationMenu(null)`, deliberately. Null is
 * one line and it also throws away the View role — Ctrl+R reload, Ctrl+Shift+I
 * DevTools, and Ctrl+= / Ctrl+- zoom. Losing DevTools would make the next
 * person's job harder, and losing the zoom keys silently while fixing copy
 * would be trading a regression for a fix. So the menu is rebuilt with the
 * Edit roles REMOVED and everything worth keeping retained.
 *
 * Zoom is deliberately NOT kept as a menu role: Chromium's zoom scales the whole
 * page, which in a terminal blurs the glyph atlas and desynchronises the fit
 * addon's column maths. Font size is done properly in the renderer instead, on
 * the same keys.
 */
function installMenu(isDevBuild: boolean): void {
  /**
   * Electron types the menu-click window as `BaseWindow`, which has no
   * `webContents` — only `BrowserWindow` does. Resolving the focused
   * BrowserWindow is both type-correct and behaviour-correct: the menu acts on
   * the window the user is looking at.
   */
  const toTerminal = (cmd: string) => (): void => {
    BrowserWindow.getFocusedWindow()?.webContents.send('tessa:menu', cmd)
  }

  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: 'Terminal',
      submenu: [
        // NOTE THE ABSENCE OF `accelerator:` ON EVERY ITEM. That is the fix,
        // not an oversight. Setting `accelerator` would re-register these chords
        // at the browser level and reintroduce exactly the interception this
        // menu exists to remove. The chord is written into the LABEL so it is
        // discoverable, while the key itself travels to the renderer untouched
        // and is handled by xterm's custom key handler — which, unlike Chromium,
        // knows whether there is a terminal selection.
        //
        // Clicking the item is a second route to the same action, via
        // `tessa:menu`, for when he cannot remember the chord.
        { label: 'Copy\tCtrl+Shift+C', click: toTerminal('copy') },
        { label: 'Paste\tCtrl+Shift+V', click: toTerminal('paste') },
        { label: 'Select All\tCtrl+A', click: toTerminal('selectAll') },
        // `clearSelection` was reachable from the menu channel and had a
        // keyboard case, but nothing bound it and no menu item sent it — an
        // action wired at both ends and unreachable in the middle. A menu item
        // is the honest fix: one line, and it is discoverable.
        { label: 'Clear Selection', click: toTerminal('clearSelection') },
        { type: 'separator' },
        // PANES. Chords in the label only, no `accelerator:` — setting one
        // would re-register the chord at browser level and reintroduce the
        // exact interception the custom menu exists to remove.
        { label: 'Split Right	Ctrl+Shift+D', click: toTerminal('splitRight') },
        { label: 'Split Down	Ctrl+Shift+E', click: toTerminal('splitDown') },
        { label: 'Close Pane	Ctrl+Shift+W', click: toTerminal('closePane') },
        { type: 'separator' },
        { label: 'New PowerShell', click: toTerminal('shell:powershell') },
        { label: 'New Command Prompt', click: toTerminal('shell:cmd') },
        { label: 'New Git Bash', click: toTerminal('shell:gitbash') },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        // KEPT: reload and DevTools. DEVTOOLS IN DEV ONLY — a packaged build
        // should not ship an inspector on a keystroke.
        ...(isDevBuild
          ? ([
              { role: 'reload' },
              { role: 'forceReload' },
              { role: 'toggleDevTools' },
              { type: 'separator' },
            ] as Electron.MenuItemConstructorOptions[])
          : []),
        // NOT `role: 'zoomIn'` — see the note above. These reach the terminal.
        { label: 'Bigger Text\tCtrl+=', click: toTerminal('fontIncrease') },
        { label: 'Smaller Text\tCtrl+-', click: toTerminal('fontDecrease') },
        { label: 'Reset Text Size\tCtrl+0', click: toTerminal('fontReset') },
      ],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

/**
 * A window.
 *
 * `startDir` is the folder this window's FIRST shell should open in — a `tcli`
 * invocation's working directory. It is recorded against the window's
 * webContents id rather than in a shared variable, so two windows opening at
 * once cannot take each other's folder.
 *
 * ── IT MUST BE VISIBLY A NEW WINDOW ───────────────────────────────────────
 *
 * A second window landing exactly on top of the first is indistinguishable
 * from nothing happening — the same trap the previous round hit when a tab
 * opened in a window he could not see. So each window after the first is
 * offset down and right from the one that spawned it, and brought to the
 * front. On Windows a background process cannot raise itself with `focus()`
 * alone; `app.focus({ steal: true })` is the part that actually does it.
 */
function createWindow(startDir?: string): BrowserWindow {
  // Cascade from whichever window is frontmost, wrapping every 6 so a long
  // session cannot walk new windows off the bottom of a 768px screen.
  const CASCADE_PX = 34
  const from = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows().at(-1)
  const step = (BrowserWindow.getAllWindows().length % 6) + 1
  const offset = from ? { x: from.getBounds().x + CASCADE_PX * step, y: from.getBounds().y + CASCADE_PX * step } : null

  const win = new BrowserWindow({
    ...(offset ? { x: offset.x, y: offset.y } : {}),
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

  // Forward renderer console to the main log. This used to be gated on `isDev`,
  // back when the only destination was a terminal that a packaged app does not
  // have. It now goes to the log file too, and the packaged case is exactly the
  // one where it matters: the renderer's own security self-checks, the GPU rung
  // it selected and every xterm failure are otherwise invisible in an installed
  // build with no devtools.
  //
  // Electron 43 deprecated the positional (event, level, message) signature in
  // favour of a single event object.
  win.webContents.on('console-message', (event) => {
    console.log(`[renderer:${event.level}] ${event.message}`)
  })

  // RECORDED BEFORE THE RENDERER CAN ASK. `tessa:pty-start` arrives well after
  // this, but keying it here means the id is bound at construction rather than
  // raced for later.
  if (startDir) {
    openingCwd.set(win.webContents.id, startDir)
    logMain(`window ${win.webContents.id} will open its first shell in ${startDir}`)
  }
  // A window whose renderer dies must not leave its folder in the map for ever.
  win.on('closed', () => openingCwd.delete(win.webContents.id))

  win.once('ready-to-show', () => {
    win.show()
    // Every window raises itself, not just the first: a `tcli` window that
    // opened behind the one he was looking at reads as "nothing happened".
    if (win.isMinimized()) win.restore()
    win.focus()
    app.focus({ steal: true })
    // A latency run REQUIRES a compositing window. Chromium stops rAF entirely
    // for a hidden or fully occluded window, which produced a run that stalled
    // at sample 600 with no error. Under --measure we pin the window on top and
    // take focus so the frames the harness depends on actually happen.
    if (process.argv.includes('--measure')) {
      win.setAlwaysOnTop(true, 'screen-saver')
      win.focus()
      app.focus({ steal: true })
    }
    console.log(`[tessa-console] cold start -> first paint: ${msSince(t0).toFixed(0)} ms`)
    const mem = process.memoryUsage()
    console.log(`[tessa-console] main rss: ${(mem.rss / 1024 / 1024).toFixed(1)} MB`)
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
  console.log(`[tessa-console] ${message}`)
}

/* ═════════════════════════════════════════ DEV HARNESS — Step 5 exit criterion */

/**
 * Read `--flag value` from ANY argv.
 *
 * Split out from `devFlag` because `second-instance` delivers the OTHER
 * process's argv, and reading `process.argv` there would answer a question
 * about the wrong process entirely — silently, and with a plausible-looking
 * result.
 */
function devFlagFrom(argv: readonly string[], name: string): string | undefined {
  // `--flag=value` first. Chromium's command-line parser normalises switches it
  // recognises into that form, and a plain indexOf would miss them entirely.
  const eq = argv.find((a) => a.startsWith(`${name}=`))
  if (eq) return eq.slice(name.length + 1) || undefined

  const i = argv.indexOf(name)
  if (i < 0) return undefined
  const next = argv[i + 1]
  // ⚠ THE GUARD IS NOT DEFENSIVE PROGRAMMING — IT IS A MEASURED BUG.
  //
  // `second-instance` delivers the second process's argv AFTER Chromium has had
  // it, and Chromium APPENDS its own switches. Observed on this machine: a
  // launch carrying `--cwd "\server\share"` arrived with
  // `--allow-file-access-from-files` sitting where the value should have been,
  // and the Console dutifully tried to stat a switch name as a directory:
  //
  //     tcli: cannot read --allow-file-access-from-files (ENOENT ...)
  //
  // A value that begins with `-` is never a directory, so it is never the
  // value. Rejecting it makes the caller fall back to `workingDirectory`, which
  // is the correct answer anyway.
  if (next === undefined || next.startsWith('-')) return undefined
  return next
}

/** Read `--flag value` from this process's argv. */
function devFlag(name: string): string | undefined {
  return devFlagFrom(process.argv, name)
}

type DevStep =
  | { type: 'input'; text: string }
  | { type: 'wait'; ms: number }
  | { type: 'resize'; cols: number; rows: number }
  | { type: 'log'; msg: string }
  /** A chord, dispatched as a REAL KeyboardEvent into xterm. */
  | { type: 'key'; chord: string }
  /** Read something back into the log: `clipboard` or `selection`. */
  | { type: 'dump'; what: string }
  /** Seed the OS clipboard, for the paste-from-outside proof. */
  | { type: 'clip'; text: string }
  /**
   * Drive the MENU route, exactly as a click does.
   *
   * Item 1B needs this to exist. `key` proves the KEYBOARD path, and would
   * still pass if the menu kept its own unguarded copy of the same action —
   * which is precisely the bug this step is here to rule out.
   */
  | { type: 'menu'; cmd: string }
  /**
   * Block until the focused terminal's buffer matches, or give up loudly.
   *
   * Replaces `wait` before anything that depends on a shell being ready. A
   * timeout REPORTS WHAT IT SAW rather than continuing quietly, because a
   * script that types into a dead shell produces an empty capture and looks
   * like a product bug.
   */
  | { type: 'waitFor'; pattern: string; timeoutMs?: number }
  /** Resize the window, so a layout can be proven at a size he actually uses. */
  | { type: 'winsize'; w: number; h: number }
  /**
   * Capture the window to a PNG.
   *
   * "He cannot see it" is a claim about PIXELS, and DOM boxes cannot settle it.
   * Two rounds of this watermark were reported as correct from geometry alone
   * while he was looking at nothing.
   */
  | { type: 'shot'; path: string }
  /**
   * Close the app when the script finishes.
   *
   * A scripted run MUST clean up after itself. Without this every proof launch
   * leaves an Electron tree behind, and once its launching shell is gone the
   * ancestry guard correctly refuses to kill it — so the orphans accumulate and
   * nobody can safely remove them. The harness ending itself is the only
   * version of this that does not depend on a later kill being provable.
   */
  | { type: 'quit' }

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
let devScriptStarted = false

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
        // ROUTED THROUGH THE FOCUSED PANE, not through a fixed session id.
        // With panes, "type this" has to mean "type it where the cursor is",
        // or every proof would silently address pane one.
        logMain(`DEVSCRIPT[${i}] input ${JSON.stringify(step.text)}`)
        BrowserWindow.getAllWindows()[0]?.webContents.send(
          'tessa:menu',
          `devtype:${Buffer.from(step.text, 'utf8').toString('base64')}`,
        )
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
      case 'key':
        // DEV HARNESS ONLY. Forwards a chord to the renderer, which dispatches
        // a REAL KeyboardEvent into xterm's own textarea — so `chordOf`, the
        // keymap lookup and the action all run exactly as they do for a human.
        // Calling the action directly would prove nothing about the binding.
        logMain(`DEVSCRIPT[${i}] key ${step.chord}`)
        BrowserWindow.getAllWindows()[0]?.webContents.send('tessa:menu', `devkey:${step.chord}`)
        break
      case 'dump':
        logMain(`DEVSCRIPT[${i}] dump ${step.what}`)
        BrowserWindow.getAllWindows()[0]?.webContents.send('tessa:menu', `devdump:${step.what}`)
        break
      case 'quit':
        logMain('DEVSCRIPT quit — closing the app so no orphan is left behind')
        // Reap the PTY first; on Windows killing the host does not reap
        // cmd.exe/conhost.exe, which is the whole reason shutdownPtyHost exists.
        await shutdownPtyHost()
        app.quit()
        return
      case 'clip':
        // Seed the OS clipboard, so "paste from outside the Console" is a real
        // clipboard round trip rather than a string the renderer already had.
        logMain(`DEVSCRIPT[${i}] clip ${JSON.stringify(step.text)}`)
        clipboard.writeText(step.text)
        break
      case 'shot': {
        const win2 = BrowserWindow.getAllWindows()[0]
        if (win2) {
          const img = await win2.webContents.capturePage()
          writeFileSync(step.path, img.toPNG())
          logMain(`DEVSCRIPT[${i}] shot -> ${step.path} (${img.getSize().width}x${img.getSize().height})`)
        }
        break
      }
      case 'waitFor': {
        const w3 = BrowserWindow.getAllWindows()[0]
        const deadline = Date.now() + (step.timeoutMs ?? 60_000)
        const re = new RegExp(step.pattern)
        let seen = ''
        let hit = false
        while (Date.now() < deadline) {
          try {
            seen = String(
              await w3?.webContents.executeJavaScript('window.__tessaBuffer ? window.__tessaBuffer() : ""'),
            )
          } catch {
            seen = ''
          }
          if (re.test(seen)) { hit = true; break }
          await new Promise((r) => setTimeout(r, 400))
        }
        const tail = seen.split('\n').filter(Boolean).slice(-3).join(' | ').slice(0, 160)
        logMain(`DEVSCRIPT[${i}] waitFor /${step.pattern}/ -> ${hit ? 'MATCHED' : 'TIMED OUT'}  last: ${tail}`)
        break
      }
      case 'winsize': {
        const w = BrowserWindow.getAllWindows()[0]
        logMain(`DEVSCRIPT[${i}] winsize ${step.w}x${step.h}`)
        // setSize is a no-op on a maximized window, which is how this one opens.
        if (w?.isMaximized()) w.unmaximize()
        w?.setSize(step.w, step.h)
        break
      }
      case 'menu':
        // The SAME channel and the SAME payload `toTerminal()` sends on a real
        // menu click — not a shortcut past it.
        logMain(`DEVSCRIPT[${i}] menu ${step.cmd}`)
        BrowserWindow.getAllWindows()[0]?.webContents.send('tessa:menu', step.cmd)
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
  // ONE THREAD, BOTH SURFACES. Anything broadcast on evt.transcript.message
  // reaches the chat pane — whether he typed it here or spoke it to the Orb.
  onTranscript: (msg) => {
    BrowserWindow.getAllWindows()[0]?.webContents.send('tessa:transcript', msg)
  },
  onAgentState: (state) => {
    BrowserWindow.getAllWindows()[0]?.webContents.send('tessa:agent-state', state)
  },
  onRevoke: (sessionId, reason) => {
    // CONTRACT §4.2: the Console MUST comply and report back.
    // Stamped absolutely so an external Win32_Process poller can be correlated
    // against this log without trusting either clock alone.
    logMain(`revoke received at ${new Date().toISOString()} for session ${sessionId} (${reason}) — killing`)
    void killPty(daemonClient, sessionId, reason)
  },
})

app.whenReady().then(async () => {
  // ── FIRST, BEFORE ANY WINDOW EXISTS ──────────────────────────────────────
  //
  // Replacing the default menu must happen before a window can receive a
  // keystroke, or the first Ctrl+A of the session is still eaten by Chromium's
  // Edit role. `setApplicationMenu` is global rather than per-window, so this is
  // the right place and the right time.
  logMain(`log file: ${LOG_PATH || '(none — LOCALAPPDATA unset)'}`)
  logMain(`paths: exe=${app.getPath('exe')} __dirname=${__dirname}`)
  // The lock keys off userData, so both are logged together: this one line is
  // what proves dev and installed hold SEPARATE locks rather than one shared
  // one, on any machine, without a differential experiment.
  logMain(`identity: name=${app.getName()} userData=${app.getPath('userData')} singleInstanceLock=${GOT_SINGLE_INSTANCE_LOCK}`)

  installMenu(isDev)
  logMain(`menu: custom menu installed (Edit roles removed; devTools=${isDev})`)

  // Step 1 liveness probe for the contextBridge.
  ipcMain.handle('tessa:ping', () => 'pong')

  // `--no-daemon` skips dev auto-start. Without it, "daemon down" is untestable:
  // the supervisor would simply start one and the grant gate would never see the
  // condition it exists to handle.
  //
  // ORDER MATTERS: the window is created only AFTER the daemon is up and the
  // `tessa:pty-start` handler is registered.
  //
  // It used to be created first, and that was a latent race that only showed
  // itself once the supervisor actually had to START a daemon rather than
  // attach to a running one: the renderer mounted, called `tessa:pty-start`, and
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

  // ── SETTINGS AND SHELLS, RESOLVED ONCE AT STARTUP ────────────────────────
  //
  // Written out on first run so there is a real file to edit rather than a
  // blank he has to invent the schema for.
  /**
   * The directory the next terminal opens in — ITEM 5e.
   *
   * Set from the cwd a terminal was last spawned with, so opening Git Bash
   * while sitting in a project folder lands in that project folder rather than
   * bouncing back to home. Tracking the shell's LIVE cwd (as it changes with
   * `cd`) needs OSC 7, which no default Windows shell emits without a prompt
   * hook — that is Phase 2's job and is deliberately not faked here.
   */
  /** The directory the last shell opened in, PER WINDOW (webContents id). */
  const lastCwd = new Map<number, string>()

  const seeded = ensureSettingsFile()
  let loaded = loadSettings()
  logMain(
    `settings: ${loaded.path}${seeded.wrote ? ' (created with defaults)' : ''}` +
      `${loaded.existed && !seeded.wrote ? ' (read)' : ''}`,
  )
  // A MALFORMED ENTRY IS NEVER FATAL AND NEVER SILENT. Each complaint is named,
  // and the default it fell back to is named with it — a settings file that is
  // quietly ignored is worse than one that errors, because he edits it again
  // and again and nothing changes.
  for (const p of loaded.problems) logMain(`settings PROBLEM: ${p}`)
  for (const m of loaded.migrated) logMain(`settings MIGRATED: ${m}`)

  const shells = resolveShells()
  for (const s of shells) logMain(`shell: ${describeShell(s)}`)
  const unavailable = shells.filter((s) => !s.available)
  if (unavailable.length) {
    logMain(`shell: NOT AVAILABLE — ${unavailable.map((s) => s.label).join(', ')}`)
  }

  // The Console follows the Orb's companion colour. Read on demand rather than
  // cached, so switching companion in the Orb recolours this window on its next
  // focus without a restart.
  // READ-ONLY, METADATA-ONLY, ONE LEVEL. See filetree.ts for why lazy is a
  // safety property here and not an optimisation. The renderer names a
  // directory; main does the reading, because the preload is sandboxed and
  // cannot touch the filesystem at all — which is the property we want.
  ipcMain.handle('tessa:fs-list', (_e, dir: unknown) => {
    if (typeof dir !== 'string' || !dir) {
      return { path: '', entries: [], total: 0, truncated: false, error: 'no directory given' }
    }
    const r = listDir(dir)
    logMain(`fs.list ${dir} -> ${r.entries.length}/${r.total}${r.error ? ` ERROR ${r.error}` : ''}`)
    return r
  })

  /**
   * A TYPED TURN, over the Console's EXISTING authenticated socket.
   *
   * No second connection: this rides the same DaemonClient that already
   * carries the PTY grants, so it inherits the per-launch token and the
   * Origin check rather than re-doing them.
   *
   * The renderer sends TEXT and nothing else — no tool name, no args. Which
   * tool runs is the daemon's decision, exactly as it is for voice, so a
   * compromised renderer cannot name an action.
   */
  ipcMain.handle('tessa:agent-send', async (_e, text: unknown) => {
    const t = typeof text === 'string' ? text.trim() : ''
    if (!t) return { ok: false, error: 'empty' }
    if (!daemonClient.isConnected) {
      return { ok: false, error: 'notConnected' }
    }
    try {
      const reply = await daemonClient.request('cmd.agent.message', { text: t })
      if (reply.type.startsWith('err.')) {
        const p = reply.payload as { message?: unknown }
        return { ok: false, error: String(p.message ?? reply.type) }
      }
      return { ok: true, ...(reply.payload as Record<string, unknown>) }
    } catch (err) {
      return { ok: false, error: (err as Error).message }
    }
  })

  ipcMain.handle('tessa:theme', () => {
    const t = readTheme()
    if (t.problem) logMain(`theme: ${t.problem}`)
    return t
  })

  ipcMain.handle('tessa:shells', () => ({
    shells: shells.map((s) => ({ id: s.id, label: s.label, available: s.available, how: s.how })),
    defaultShell: loaded.settings.defaultShell,
  }))

  const settingsPayload = (): Record<string, unknown> => ({
    keymap: loaded.settings.keymap,
    rightClickPastes: loaded.settings.rightClickPastes,
    copyOnSelect: loaded.settings.copyOnSelect,
    scrollback: loaded.settings.scrollback,
    fontSize: loaded.settings.fontSize,
    path: loaded.path,
    problems: loaded.problems,
  })
  ipcMain.handle('tessa:settings', () => settingsPayload())
  ipcMain.handle('tessa:settings-reload', () => {
    loaded = loadSettings()
    logMain(`settings: reloaded from ${loaded.path}`)
    for (const p of loaded.problems) logMain(`settings PROBLEM: ${p}`)
    for (const m of loaded.migrated) logMain(`settings MIGRATED: ${m}`)
    return settingsPayload()
  })

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
  ipcMain.handle('tessa:pty-start', async (_e, dims: { cols: number; rows: number }, shellId?: string) => {
    const senderId = _e.sender.id
    // ── WHICH SHELL ────────────────────────────────────────────────────────
    //
    // The renderer passes an ID from a closed set, never a command line. An
    // unknown or absent id resolves to the configured default rather than
    // reaching a spawn, so this remains a menu selection.
    // `--shell <id>` is a DEV HARNESS FLAG: it lets a scripted run prove each
    // shell end to end without a human clicking the picker. A renderer request
    // still wins, so it only sets the default for the first terminal.
    // `defaultShell` FROM THE SETTINGS FILE IS THE LAST RUNG, and it was
    // missing. Without it `pickShell` fell through to the module constant
    // DEFAULT_SHELL, so setting "defaultShell": "gitbash" in the settings file
    // validated, crossed the IPC hop, reached the picker UI — and PowerShell
    // still opened. The field was read and not used, the same shape as
    // `scrollback`.
    const choice = pickShell(
      (shellId as ShellId | undefined) ??
        (devFlag('--shell') as ShellId | undefined) ??
        (loaded.settings.defaultShell as ShellId | undefined),
      shells,
    )
    if (choice.message) logMain(`shell: ${choice.message}`)

    // THE WORKING DIRECTORY IS INHERITED, NOT RESET. Opening Git Bash from a
    // project folder must land in that project folder — anything else is
    // surprising. `--cwd` overrides for the harness; otherwise the last
    // directory a terminal reported, falling back to home on the first one.
    // ONE cwd, resolved ONCE, used for BOTH the grant request and the spawn.
    //
    // These used to diverge: the grant was requested for `lastCwd` while the
    // shell was actually started in `devFlag('--cwd') ?? cwd`. Harmless while
    // only the harness passed `--cwd`, and wrong the moment `tcli` does — the
    // daemon would audit a §6.5 grant for one directory and a shell would open
    // in another, making the audit answer the wrong question.
    //
    // THIS WINDOW's `tcli` folder first: it is an explicit instruction about
    // this terminal and outranks the inherited directory. Then the directory
    // the last shell IN THIS WINDOW opened in — per window, so a `tcli` window
    // rooted in one project cannot hand its folder to a terminal opened in
    // another window.
    const cwd =
      takeCwdFor(senderId) ?? devFlag('--cwd') ?? lastCwd.get(senderId) ?? app.getPath('home')

    // The grant is asked for BY SHELL. `profileId` is what the daemon audits
    // and what its policy keys on, so a Git Bash spawn must not present itself
    // as a cmd spawn — the log would then be unable to answer which shell ran
    // something, which is the whole point of recording it.
    const profileId = choice.spec.id

    if (!choice.spec.exe) {
      logMain(`pty-start refused: ${choice.message}`)
      return { ok: false as const, error: choice.message }
    }

    if (!daemonClient.isConnected) {
      // No daemon means no grant means NO PTY. Failing closed is the point.
      const detail = daemonClient.current.detail ?? daemonClient.current.phase
      logMain(`pty-start refused: daemon not connected (${detail})`)
      return { ok: false as const, error: `Tessa Core is not connected (${detail}) — no PTY without a grant` }
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
      return { ok: false as const, error: `denied by Tessa Core (${code}): ${message}` }
    }

    const grantId = String(reply.payload['grantId'] ?? '')
    const sessionId = String(reply.payload['sessionId'] ?? '')
    const expiresAt = String(reply.payload['expiresAt'] ?? '')
    if (!grantId || !sessionId) {
      logMain('pty-start refused: grant reply missing grantId/sessionId')
      return { ok: false as const, error: 'malformed grant from Tessa Core' }
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
        // `--revoke-proof` forces cmd AND its argv together. It exists to create
        // a real GRANDCHILD (`cmd` -> `ping`) for the revoke test, and pairing a
        // cmd-shaped argv with whatever shell happens to be default would spawn
        // PowerShell with `/k`, which is not a command it understands.
        shell: proofMode ? (process.env['COMSPEC'] ?? 'cmd.exe') : choice.spec.exe,
        args: proofMode ? ['/k', 'ping -n 600 127.0.0.1'] : choice.spec.args,
        cwd,
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
      lastCwd.set(senderId, cwd)
      logMain(
        `STEP5 sessionId=${sessionId} grantId=${grantId} shellPid=${result.pid} ` +
          `shell=${choice.spec.id} exe=${choice.spec.exe} cwd=${cwd}`,
      )
      // ONCE. `tessa:pty-start` now fires per PANE, so without this guard a
      // four-pane run would start the script four times over.
      if (!devScriptStarted) {
        devScriptStarted = true
        void runDevScript(sessionId)
      }
      return {
        ok: true as const,
        ...result,
        sessionId,
        grantId,
        grantMs,
        expiresAt,
        // The terminal shows which shell it actually got, and says so out loud
        // when that is not the one that was asked for.
        shellId: choice.spec.id,
        shellLabel: choice.spec.label,
        substituted: choice.substituted,
        shellMessage: choice.message,
        // Where it OPENED, for the tab title. Not the live cwd — that needs
        // OSC 7, which no default Windows shell emits.
        cwd,
      }
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
  win = createWindow(firstWindowCwd ?? undefined)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})
  // ── A REJECTION HERE USED TO PRODUCE NOTHING AT ALL ───────────────────────
  //
  // Everything before `createWindow()` is awaited inside this block: the daemon
  // supervisor, the settings load, the shell resolution. If any of them threw,
  // the promise rejected unobserved — no window was ever created, nothing was
  // logged, and the app sat in the tray doing nothing with no way to find out
  // why. That is the worst version of this repo's silent-failure class, because
  // it takes away the very surface the diagnosis would have appeared on.
  .catch((err: unknown) => {
    const e = err as Error
    logMain(`STARTUP FAILED before the window existed: ${e?.stack ?? String(err)}`)
    dialog.showErrorBox(
      'Tessa Console could not start',
      `${e?.message ?? String(err)}

See the console output for the full trace.`,
    )
    app.quit()
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
