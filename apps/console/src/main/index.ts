import { readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, screen, shell, type WebContents } from 'electron'
import tokens from '@tessa/tokens'
import { closePty, flushHeldReports, isEnding, killPty, liveSessions, reportPty, setSelfExitHandler, shellPidAlive, shutdownPtyHost, startPty } from './pty-host.ts'
import { DaemonClient } from './ws-client.ts'
import { DaemonSupervisor } from './daemon.ts'
import { describeShell, planSpawn, resolveShells, shellScriptDir, type ShellSpec } from './shells.ts'
import { DESIGN_KEYMAP, ensureSettingsFile, loadSettings, saveSettingsPatch, type LoadedSettings, type SettingsPatch } from './settings.ts'
import { loadSession, saveSession, sanitiseSession, sessionPath, type SavedSession } from './session.ts'
import { gitInfo, gitStats } from './git.ts'
import { openLink, realLauncher, type Launcher, type LinkRequest } from './links.ts'
import { startFileLog } from './log.ts'

const isDev = !app.isPackaged

const LOG_PATH = startFileLog(`Tessa Console ${app.getVersion()} — packaged=${app.isPackaged} pid=${process.pid}`)

function logMain(message: string): void {
  console.log(`[tessa-console] ${message}`)
}

function devFlagFrom(argv: readonly string[], name: string): string | undefined {
  const eq = argv.find((a) => a.startsWith(`${name}=`))
  if (eq) return eq.slice(name.length + 1) || undefined
  const i = argv.indexOf(name)
  if (i < 0) return undefined
  const next = argv[i + 1]
  if (next === undefined || next.startsWith('-')) return undefined
  return next
}

function devFlag(name: string): string | undefined {
  return isDev ? devFlagFrom(process.argv, name) : undefined
}

function safeStartDir(dir: string | undefined | null): string | null {
  if (!dir || typeof dir !== 'string') return null
  let d = dir.trim()
  if (!d || d.length > 1024 || /[\u0000-\u001f]/.test(d)) return null
  const msys = d.match(/^\/([a-zA-Z])(\/.*)?$/)
  if (msys) d = `${msys[1]!.toUpperCase()}:${(msys[2] ?? '/').replace(/\//g, '\\')}`
  if (d.startsWith('\\\\') || d.startsWith('//')) return null
  if (!/^[a-zA-Z]:[\\/]/.test(d)) return null
  try {
    if (!statSync(d).isDirectory()) return null
  } catch {
    return null
  }
  return resolve(d)
}

if (isDev) {
  app.setPath('userData', join(app.getPath('appData'), 'tessa-console-dev'))
}

const GOT_SINGLE_INSTANCE_LOCK = app.requestSingleInstanceLock()
if (!GOT_SINGLE_INSTANCE_LOCK) {
  console.log('[tessa-console] second instance — cwd handed to the running Console, exiting')
  app.exit(0)
}

app.commandLine.appendSwitch('disable-backgrounding-occluded-windows')
app.commandLine.appendSwitch('disable-renderer-backgrounding')
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion')

const t0 = process.hrtime.bigint()
const msSince = (from: bigint): number => Number(process.hrtime.bigint() - from) / 1e6

const color = (name: string): string => {
  const entry = (tokens.color as Record<string, { value: string } | undefined>)[name]
  if (!entry) throw new Error(`token color.${name} missing — run: npm run tokens`)
  return entry.value
}

function sgrOf(name: string): string {
  const m = color(name).match(/^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/)
  if (!m) return '0'
  return `38;2;${parseInt(m[1]!, 16)};${parseInt(m[2]!, 16)};${parseInt(m[3]!, 16)}`
}

interface WindowState {
  win: BrowserWindow
  startDir: string | null
  restore: boolean
  tabs: SavedSession
  order: number
}

const windows = new Map<number, WindowState>()
const sessionOwner = new Map<string, number>()
let windowOrder = 0
let firstWindowCwd: string | null = null
let settings: LoadedSettings
let shells: ShellSpec[] = []
let savedAtStart: SavedSession = { tabs: [], recent: [] }
let sessionDirty = false
let sessionTimer: NodeJS.Timeout | null = null
let lastRecent: string[] = []
let sessionSnapshot: SavedSession = { tabs: [], recent: [] }

const approvals = new Map<string, { tool: string; detail: string; tier: string }>()
const threats: { alertId: string; title: string }[] = []
const endingWork = new Set<Promise<unknown>>()

function trackEnding<T>(p: Promise<T>): Promise<T> {
  endingWork.add(p)
  const done = (): void => {
    endingWork.delete(p)
  }
  p.then(done, done)
  return p
}

app.on('second-instance', (_event, argv) => {
  const asked = safeStartDir(devFlagFrom(argv, '--cwd'))
  if (asked) {
    logMain(`second-instance: opening a NEW WINDOW at ${asked}`)
    createWindow(asked, false)
    return
  }
  const first = [...windows.values()].filter((s) => !s.win.isDestroyed()).sort((a, b) => a.order - b.order)[0]
  if (!first) {
    createWindow(null, false)
    return
  }
  logMain('second-instance: no --cwd, focusing the open window')
  if (first.win.isMinimized()) first.win.restore()
  first.win.show()
  first.win.focus()
})

{
  const asked = devFlagFrom(process.argv, '--cwd')
  firstWindowCwd = safeStartDir(asked)
  logMain(asked ? `start folder from --cwd: ${firstWindowCwd ?? `refused (${asked})`}` : 'no --cwd: the first window restores the saved tabs')
}

function broadcast(channel: string, payload: unknown): void {
  for (const s of windows.values()) {
    if (!s.win.isDestroyed()) s.win.webContents.send(channel, payload)
  }
}

function sendTo(wcId: number, channel: string, payload: unknown): void {
  const s = windows.get(wcId)
  if (s && !s.win.isDestroyed()) s.win.webContents.send(channel, payload)
}

function linkPayload(): { linked: boolean; phase: string; detail: string } {
  const st = daemonClient.current
  return { linked: daemonClient.isConnected, phase: st.phase, detail: st.detail ?? '' }
}

function approvalsPayload(latest?: { requestId: string; tool: string; detail: string }): unknown {
  return { count: approvals.size, ...(latest ? { latest } : {}) }
}

function threatPayload(latest?: { alertId: string; title: string }): unknown {
  return { count: threats.length, ...(latest ? { latest } : {}) }
}

function settingsPayload(): unknown {
  const s = settings.settings
  return {
    keymap: s.keymap,
    designKeymap: DESIGN_KEYMAP,
    fontSize: s.fontSize,
    scrollback: s.scrollback,
    outputColours: s.outputColours,
    prompt: s.prompt,
    cursorStyle: s.cursorStyle,
    pasteGuard: s.pasteGuard,
    rightClickPastes: s.rightClickPastes,
    copyOnSelect: s.copyOnSelect,
    defaultShell: s.defaultShell,
    path: settings.path,
    problems: settings.problems,
  }
}

function flushSession(): void {
  if (sessionTimer) {
    clearTimeout(sessionTimer)
    sessionTimer = null
  }
  if (!sessionDirty) return
  sessionDirty = false
  if (windows.size > 0) sessionSnapshot = mergedSession()
  const merged = sessionSnapshot
  try {
    saveSession(merged)
    logMain(`session saved: ${merged.tabs.length} tab(s), ${merged.recent.length} recent folder(s) -> ${sessionPath()}`)
  } catch (err) {
    logMain(`session save FAILED: ${(err as Error).message}`)
  }
}

function mergedSession(): SavedSession {
  const ordered = [...windows.values()].sort((a, b) => a.order - b.order)
  const merged: SavedSession = { tabs: [], recent: lastRecent }
  for (const w of ordered) merged.tabs.push(...w.tabs.tabs)
  return merged
}

function markSessionDirty(): void {
  sessionDirty = true
  if (sessionTimer) clearTimeout(sessionTimer)
  sessionTimer = setTimeout(flushSession, 1000)
}

function createWindow(startDir: string | null, restore: boolean): BrowserWindow {
  const area = screen.getPrimaryDisplay().workArea
  const width = Math.max(900, Math.min(1366, area.width))
  const height = Math.max(600, Math.min(728, area.height))
  const CASCADE_PX = 34
  const from = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows().at(-1)
  const step = (BrowserWindow.getAllWindows().length % 6) + 1
  const pos = from
    ? { x: from.getBounds().x + CASCADE_PX * step, y: from.getBounds().y + CASCADE_PX * step }
    : { x: area.x + Math.round((area.width - width) / 2), y: area.y + Math.round((area.height - height) / 2) }

  const noActivate = isDev && process.argv.includes('--dev-noactivate')
  const win = new BrowserWindow({
    ...pos,
    width,
    height,
    minWidth: 900,
    minHeight: 600,
    frame: false,
    show: false,
    focusable: !noActivate,
    title: 'Tessa Console',
    backgroundColor: color('console-bg'),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      backgroundThrottling: false,
    },
  })
  const id = win.webContents.id
  windows.set(id, { win, startDir, restore, tabs: { tabs: [], recent: [] }, order: windowOrder++ })

  win.webContents.on('console-message', (event) => {
    console.log(`[renderer:${event.level}] ${event.message}`)
  })
  win.on('closed', () => {
    if (windows.size > 1) {
      windows.delete(id)
      markSessionDirty()
    } else {
      sessionSnapshot = mergedSession()
      windows.delete(id)
      sessionDirty = true
    }
  })
  win.on('maximize', () => win.webContents.send('tessa:maximized', true))
  win.on('unmaximize', () => win.webContents.send('tessa:maximized', false))
  win.once('ready-to-show', () => {
    if (noActivate) {
      win.showInactive()
      logMain('window shown without activation (--dev-noactivate): no focus, no raise')
    } else {
      win.show()
      if (win.isMinimized()) win.restore()
      win.focus()
      app.focus({ steal: true })
    }
    logMain(`cold start -> first paint: ${msSince(t0).toFixed(0)} ms`)
    logMain(`main rss: ${(process.memoryUsage().rss / 1024 / 1024).toFixed(1)} MB`)
  })
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  win.webContents.on('will-navigate', (event) => event.preventDefault())

  const flags: string[] = []
  if (isDev && process.argv.includes('--force-dom')) flags.push('forceDom')
  if (isDev && devFlag('--devscript')) flags.push('dev')
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

const supervisor = new DaemonSupervisor({
  repoRoot: join(__dirname, '..', '..', '..', '..'),
  isDev,
  log: logMain,
})

const daemonClient = new DaemonClient({
  surfaceVersion: '0.2.0',
  log: logMain,
  onStatus: (s) => {
    logMain(`daemon link: ${s.phase}${s.detail ? ` — ${s.detail}` : ''}`)
    if (s.phase !== 'connected' && approvals.size) {
      approvals.clear()
      broadcast('tessa:approvals', approvalsPayload())
    }
    broadcast('tessa:link', { linked: s.phase === 'connected', phase: s.phase, detail: s.detail ?? '' })
    if (s.phase === 'connected') void flushHeldReports(daemonClient)
  },
  onRevoke: (sessionId, reason) => {
    logMain(`revoke received at ${new Date().toISOString()} for session ${sessionId} (${reason}) — killing`)
    const owner = sessionOwner.get(sessionId)
    void trackEnding(
      killPty(daemonClient, sessionId, reason).then((o) => {
        if (owner !== undefined && !o.unknownSession) {
          sendTo(owner, 'tessa:pty-ended', { sessionId, why: `Tessa revoked this shell: ${reason}`, revoked: true })
          sessionOwner.delete(sessionId)
        }
      }),
    )
  },
  onPermissionRequest: (p) => {
    const requestId = typeof p['requestId'] === 'string' ? p['requestId'] : ''
    if (!requestId) return
    const isNew = !approvals.has(requestId)
    const tool = typeof p['tool'] === 'string' ? p['tool'] : 'an action'
    const detail = typeof p['detail'] === 'string' ? p['detail'].slice(0, 200) : ''
    approvals.set(requestId, { tool, detail, tier: typeof p['tier'] === 'string' ? p['tier'] : '' })
    logMain(`approval waiting: ${requestId.slice(0, 8)} ${tool} (count ${approvals.size})`)
    broadcast('tessa:approvals', approvalsPayload(isNew ? { requestId, tool, detail } : undefined))
  },
  onPermissionResolved: (p) => {
    const requestId = typeof p['requestId'] === 'string' ? p['requestId'] : ''
    if (requestId && approvals.delete(requestId)) {
      logMain(`approval resolved: ${requestId.slice(0, 8)} ${String(p['decision'] ?? '')} (count ${approvals.size})`)
      broadcast('tessa:approvals', approvalsPayload())
    }
  },
  onSentinelAlert: (p) => {
    if (p['level'] !== 'threat') return
    const alertId = typeof p['alertId'] === 'string' ? p['alertId'] : String(threats.length + 1)
    const title = typeof p['title'] === 'string' ? p['title'].slice(0, 120) : 'A guard reported a threat.'
    threats.push({ alertId, title })
    logMain(`threat alert ${alertId}`)
    broadcast('tessa:threat', threatPayload({ alertId, title }))
  },
  onPtySessions: (sessions) => {
    logMain(`ROSTER ${sessions.length} live session(s) per the daemon`)
  },
})

setSelfExitHandler((sessionId, code) => {
  void reportPty(daemonClient, sessionId, 'exited', `exit ${code}`)
  const owner = sessionOwner.get(sessionId)
  if (owner !== undefined) sendTo(owner, 'tessa:pty-ended', { sessionId, why: 'exited', code })
  sessionOwner.delete(sessionId)
})

function cleanBanner(v: unknown): string {
  if (typeof v !== 'string') return ''
  return v.replace(/[\u0000-\u001f\u007f!%^&|<>]/g, '').slice(0, 200)
}

function cleanOwnPrompt(): void {
  const p = process.env['PROMPT']
  if (p && /[&|<>^()]/.test(p)) {
    logMain('cmd own prompt holds characters a batch line cannot carry - $P$G is used for it')
    process.env['PROMPT'] = '$P$G'
  }
}

let linkLauncher: Launcher

app.whenReady()
  .then(async () => {
    logMain(`log file: ${LOG_PATH || '(none)'}`)
    logMain(`identity: name=${app.getName()} userData=${app.getPath('userData')} singleInstanceLock=${GOT_SINGLE_INSTANCE_LOCK}`)
    logMain(
      `dev flags: packaged=${app.isPackaged} devscript=${!!devFlag('--devscript')} linkDryRun=${isDev && process.argv.includes('--link-dry-run')} ` +
        `noActivate=${isDev && process.argv.includes('--dev-noactivate')} forceDom=${isDev && process.argv.includes('--force-dom')} noDaemon=${process.argv.includes('--no-daemon')} startDaemon=${isDev && process.argv.includes('--dev-start-daemon')}`,
    )
    Menu.setApplicationMenu(null)

    const dryRun = isDev && process.argv.includes('--link-dry-run')
    linkLauncher = dryRun
      ? {
          openExternal: async (url) => logMain(`LINK DRY-RUN openExternal ${JSON.stringify(url)}`),
          spawnDetached: (exe, args, env) =>
            logMain(`LINK DRY-RUN spawn ${JSON.stringify(exe)} ${JSON.stringify(args)}${env ? ` env=${JSON.stringify(Object.keys(env))}` : ''}`),
        }
      : realLauncher((url) => shell.openExternal(url))

    const mode = process.argv.includes('--no-daemon')
      ? ({ kind: 'unavailable', reason: 'auto-start disabled by --no-daemon' } as const)
      : await supervisor.ensure(isDev && process.argv.includes('--dev-start-daemon'))
    logMain(`daemon mode: ${mode.kind}${mode.kind === 'unavailable' ? ` — ${mode.reason}` : ''}`)
    daemonClient.start()
    const connectDeadline = Date.now() + 5_000
    while (!daemonClient.isConnected && Date.now() < connectDeadline) {
      await new Promise((r) => setTimeout(r, 100))
    }
    logMain(`daemon link before first PTY request: ${daemonClient.current.phase}`)

    const seeded = ensureSettingsFile()
    settings = loadSettings()
    logMain(`settings: ${settings.path}${seeded.wrote ? ' (created with defaults)' : ''}`)
    for (const p of settings.problems) logMain(`settings PROBLEM: ${p}`)

    const pathParts = (process.env['PATH'] ?? '').split(';').filter(Boolean)
    logMain(`console PATH: ${pathParts.length} entries, first ${JSON.stringify(pathParts[0] ?? '')}, last ${JSON.stringify(pathParts[pathParts.length - 1] ?? '')}`)
    shells = resolveShells()
    for (const s of shells) logMain(`shell: ${describeShell(s)}`)
    cleanOwnPrompt()

    const loaded = loadSession()
    savedAtStart = loaded.session
    lastRecent = loaded.session.recent
    if (loaded.problem) logMain(`session file PROBLEM: ${loaded.problem}`)
    logMain(`session: ${loaded.existed ? `${savedAtStart.tabs.length} saved tab(s), ${savedAtStart.recent.length} recent` : 'none saved'}`)

    const scriptDir = shellScriptDir(app.isPackaged, process.resourcesPath, __dirname)
    logMain(`shell scripts: ${scriptDir}`)


    ipcMain.handle('tessa:init', (e) => {
      const st = windows.get(e.sender.id)
      const restore = st?.restore ?? false
      if (st) st.restore = false
      return {
        shells: shells.map((s) => ({ id: s.id, tag: s.tag, name: s.name, version: s.version, key: s.key, available: s.available, integrated: s.integrated })),
        settings: settingsPayload(),
        restore: restore ? sanitiseSession(savedAtStart) : { tabs: [], recent: savedAtStart.recent },
        startDir: st?.startDir ?? null,
        home: app.getPath('home'),
        link: linkPayload(),
        approvals: approvalsPayload(),
        threat: threatPayload(),
        maximized: st?.win.isMaximized() ?? false,
      }
    })

    ipcMain.handle('tessa:win', (e, cmd: unknown) => {
      const w = BrowserWindow.fromWebContents(e.sender)
      if (!w) return false
      if (cmd === 'minimize') w.minimize()
      else if (cmd === 'maximize') {
        if (w.isMaximized()) w.unmaximize()
        else w.maximize()
      } else if (cmd === 'close') w.close()
      return true
    })

    const devClip = devFlag('--dev-clipboard')
    if (devClip) logMain(`dev clipboard: reads and writes go to ${devClip}, never the system clipboard`)
    ipcMain.handle('tessa:clip-read', () => {
      if (!devClip) return clipboard.readText()
      try {
        return readFileSync(devClip, 'utf8')
      } catch {
        return ''
      }
    })
    ipcMain.handle('tessa:clip-write', (_e, text: unknown) => {
      if (typeof text !== 'string' || text.length > 4 * 1024 * 1024) return false
      if (devClip) {
        writeFileSync(`${devClip}.written`, `${JSON.stringify(text)}\n`, { flag: 'a' })
        logMain(`dev clipboard write ${text.length} chars`)
        return true
      }
      clipboard.writeText(text)
      return true
    })

    ipcMain.handle('tessa:settings-save', (_e, patch: unknown) => {
      if (typeof patch !== 'object' || patch === null) return { ok: false, error: 'bad patch' }
      const r = saveSettingsPatch(patch as SettingsPatch)
      if (r.ok) {
        settings = loadSettings()
        broadcast('tessa:settings', settingsPayload())
      } else {
        logMain(`settings save refused: ${r.error ?? ''}`)
      }
      return r
    })

    ipcMain.handle('tessa:session-save', (e, s: unknown) => {
      const st = windows.get(e.sender.id)
      if (!st) return false
      st.tabs = sanitiseSession(s)
      if (st.tabs.recent.length) lastRecent = st.tabs.recent
      sessionSnapshot = mergedSession()
      markSessionDirty()
      return true
    })

    ipcMain.handle('tessa:check-dir', (_e, p: unknown) => safeStartDir(typeof p === 'string' ? p : null))

    ipcMain.handle('tessa:pick-folder', async (e) => {
      const w = BrowserWindow.fromWebContents(e.sender)
      const r = w
        ? await dialog.showOpenDialog(w, { properties: ['openDirectory'], title: 'New tab in a folder' })
        : await dialog.showOpenDialog({ properties: ['openDirectory'], title: 'New tab in a folder' })
      if (r.canceled || !r.filePaths[0]) return null
      return safeStartDir(r.filePaths[0])
    })

    ipcMain.handle('tessa:git', async (_e, cwd: unknown, withCount: unknown) => {
      const dir = safeStartDir(typeof cwd === 'string' ? cwd : null)
      if (!dir) return { cwd: String(cwd ?? ''), repo: false, branch: '', changed: null, note: 'folder not readable', ms: 0 }
      const r = await gitInfo(dir, withCount === true)
      if (withCount === true && r.changed !== null) logMain(`git status: ${r.changed} changed in ${dir} (${r.ms} ms, run ${gitStats().runs})`)
      return r
    })

    ipcMain.handle('tessa:open-link', async (_e, req: unknown) => {
      if (typeof req !== 'object' || req === null) return { ok: false, message: 'Nothing opened.' }
      const r = req as Record<string, unknown>
      let lr: LinkRequest
      if (r['kind'] === 'url' && typeof r['target'] === 'string') lr = { kind: 'url', target: r['target'].slice(0, 4096) }
      else if (r['kind'] === 'path' && typeof r['target'] === 'string') {
        const line = Number(r['line'])
        const col = Number(r['col'])
        lr = {
          kind: 'path',
          target: r['target'].slice(0, 1024),
          base: typeof r['base'] === 'string' ? r['base'] : '',
          ...(Number.isInteger(line) && line > 0 ? { line } : {}),
          ...(Number.isInteger(col) && col > 0 ? { col } : {}),
        }
      } else return { ok: false, message: 'Nothing opened.' }
      try {
        const out = await openLink(lr, linkLauncher)
        logMain(`link ${lr.kind}: ${out.ok ? out.opened : 'refused'} — ${out.message}`)
        return out
      } catch (err) {
        return { ok: false, message: `Could not open it: ${(err as Error).message}` }
      }
    })

    ipcMain.handle('tessa:pty-close', async (_e, sessionId: unknown, why: unknown) => {
      if (typeof sessionId !== 'string' || !sessionId) return false
      const reason = typeof why === 'string' ? why.slice(0, 80) : 'closed by the owner'
      sessionOwner.delete(sessionId)
      const o = await trackEnding(closePty(daemonClient, sessionId, reason))
      return o.observedDead
    })

    ipcMain.handle('tessa:pty-start', async (e, dims: { cols: number; rows: number }, req: unknown) => {
      const senderId = e.sender.id
      const win = BrowserWindow.fromWebContents(e.sender)
      const r = (typeof req === 'object' && req !== null ? req : {}) as Record<string, unknown>
      const wanted = typeof r['shellId'] === 'string' ? r['shellId'] : settings.settings.defaultShell
      const spec = shells.find((s) => s.id === wanted)
      if (!spec || !spec.available) {
        return { ok: false as const, error: `${spec?.name ?? String(wanted)} is not found on this machine.` }
      }
      const askedCwd = typeof r['cwd'] === 'string' ? r['cwd'] : ''
      const st = windows.get(senderId)
      let cwd = safeStartDir(askedCwd)
      let cwdNote = ''
      if (!cwd) {
        cwd = safeStartDir(st?.startDir) ?? app.getPath('home')
        if (askedCwd) cwdNote = `${askedCwd} is not available - opened in ${cwd}`
      }
      if (!daemonClient.isConnected) {
        logMain(`pty-start refused: daemon not connected (${daemonClient.current.detail ?? daemonClient.current.phase})`)
        return { ok: false as const, waiting: true, error: 'Waiting for Tessa. The tab opens once she is back.' }
      }
      const t1 = Date.now()
      let reply
      try {
        reply = await daemonClient.request('cmd.pty.requestSpawn', {
          profileId: spec.id,
          cwd,
          actor: 'human',
          purpose: 'user opened a terminal',
        })
      } catch (err) {
        logMain(`pty-start refused: ${(err as Error).message}`)
        return { ok: false as const, waiting: !daemonClient.isConnected, error: `grant request failed: ${(err as Error).message}` }
      }
      const grantMs = Date.now() - t1
      if (!reply.ok || reply.type !== 'res.pty.grant') {
        const code = String(reply.payload['code'] ?? reply.type)
        const message = String(reply.payload['message'] ?? 'refused')
        if (code === 'permission.pending') {
          logMain(`pty-start PENDING owner approval — not spawning: ${message}`)
          return { ok: false as const, error: `Tessa wants your approval for a shell here. Answer it in the Orb. (${message})` }
        }
        logMain(`pty-start DENIED by the daemon (${code}): ${message}`)
        return { ok: false as const, error: `Tessa refused a shell here (${code}): ${message}` }
      }
      const grantId = String(reply.payload['grantId'] ?? '')
      const sessionId = String(reply.payload['sessionId'] ?? '')
      if (!grantId || !sessionId) return { ok: false as const, error: 'malformed grant from Tessa Core' }
      logMain(`GRANT ok in ${grantMs} ms — grantId=${grantId} sessionId=${sessionId}`)
      const env: Record<string, string> = spec.integrated
        ? {
            TESSA_PROMPT: settings.settings.prompt,
            TESSA_SGR_FOLDER: sgrOf('console-t3'),
            TESSA_SGR_DIM: sgrOf('console-t4'),
            TESSA_SGR_INPUT: sgrOf('console-t1'),
            ...(spec.id === 'cmd' ? { TESSA_ESC: String.fromCharCode(27) } : {}),
          }
        : {}
      const banner = cleanBanner(r['banner'])
      if (banner && spec.integrated) env['TESSA_BANNER'] = banner
      const plan = planSpawn(spec, scriptDir, env, logMain)
      try {
        if (!win) throw new Error('no window for this request')
        const result = await startPty(win, {
          shell: plan.exe,
          args: plan.args,
          cwd,
          cols: dims?.cols ?? 80,
          rows: dims?.rows ?? 24,
          sessionId,
          env: plan.env,
          ...(devFlag('--capture') ? { capturePath: `${String(devFlag('--capture'))}.${sessionId.slice(-6)}.bin` } : {}),
          ...(isDev && process.argv.includes('--dev-pty-probe') ? { devProbe: true } : {}),
        })
        await reportPty(daemonClient, sessionId, 'started', result.pid)
        sessionOwner.set(sessionId, senderId)
        logMain(`SPAWNED sessionId=${sessionId} grantId=${grantId} shellPid=${result.pid} shell=${spec.id} integrated=${plan.integrated} cwd=${cwd}`)
        return {
          ok: true as const,
          sessionId,
          grantId,
          grantMs,
          pid: result.pid,
          shellId: spec.id,
          integrated: plan.integrated,
          cwd,
          cwdNote,
        }
      } catch (err) {
        const message = (err as Error).message
        logMain(`pty spawn FAILED — reporting startFailed to reclaim grant ${grantId}: ${message}`)
        await reportPty(daemonClient, sessionId, 'startFailed', message)
        return { ok: false as const, error: message }
      }
    })

    createWindow(firstWindowCwd, true)
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow(null, false)
    })
    void runDevScript()
    const dropAfter = Number(devFlag('--dev-drop-link') ?? NaN)
    if (Number.isFinite(dropAfter) && dropAfter >= 0) {
      setTimeout(() => daemonClient.suspendFor(20_000), dropAfter * 1000)
      logMain(`dev link drop armed: closes the link ${dropAfter} s after start for 20 s`)
    }
  })
  .catch((err: unknown) => {
    const e = err as Error
    logMain(`STARTUP FAILED before the window existed: ${e?.stack ?? String(err)}`)
    dialog.showErrorBox('Tessa Console could not start', `${e?.message ?? String(err)}`)
    app.quit()
  })

type DevStep = { type: string; [k: string]: unknown }

async function runDevScript(): Promise<void> {
  const scriptPath = devFlag('--devscript')
  if (!scriptPath) return
  let steps: DevStep[]
  try {
    steps = JSON.parse(readFileSync(scriptPath, 'utf8')) as DevStep[]
  } catch (err) {
    logMain(`devscript unreadable: ${(err as Error).message}`)
    return
  }
  const first = (): BrowserWindow | undefined => [...windows.values()].sort((a, b) => a.order - b.order)[0]?.win
  const wc = (): WebContents | undefined => first()?.webContents
  for (let i = 0; i < 200 && !wc(); i++) await new Promise((r) => setTimeout(r, 100))
  for (let i = 0; i < 300; i++) {
    const ready = await wc()?.executeJavaScript('typeof window.__tessaDev === "function"').catch(() => false)
    if (ready) break
    await new Promise((r) => setTimeout(r, 100))
  }
  logMain(`DEVSCRIPT start (${steps.length} steps)`)
  for (const [i, step] of steps.entries()) {
    const tag = `DEVSCRIPT[${i}] ${step.type}`
    try {
      switch (step.type) {
        case 'wait':
          await new Promise((r) => setTimeout(r, Number(step['ms'] ?? 0)))
          break
        case 'log':
          logMain(`${tag} ${String(step['msg'] ?? '')}`)
          break
        case 'clip': {
          const devClipFile = devFlag('--dev-clipboard')
          if (devClipFile) {
            writeFileSync(devClipFile, String(step['text'] ?? ''), 'utf8')
            logMain(`${tag} dev clipboard file seeded`)
          } else {
            logMain(`${tag} refused: the system clipboard is never seeded; pass --dev-clipboard <file>`)
          }
          break
        }
        case 'shot': {
          const w = first()
          if (w) {
            const img = await w.webContents.capturePage()
            writeFileSync(String(step['path']), img.toPNG())
            logMain(`${tag} -> ${String(step['path'])} (${img.getSize().width}x${img.getSize().height})`)
          }
          break
        }
        case 'winsize': {
          const w = first()
          if (w?.isMaximized()) w.unmaximize()
          w?.setContentSize(Number(step['w']), Number(step['h']))
          logMain(`${tag} ${String(step['w'])}x${String(step['h'])} -> content ${JSON.stringify(w?.getContentSize())}`)
          break
        }
        case 'state': {
          const live = liveSessions()
          const alive = live.filter((s) => s.shellPid !== undefined && shellPidAlive(s.shellPid)).length
          logMain(`${tag} live=${live.length} shellsAlive=${alive} sent=${JSON.stringify([...daemonClient.sentTypes.entries()])} git=${JSON.stringify(gitStats())}`)
          break
        }
        case 'deliver': {
          const type = String(step['event'] ?? '')
          if (!/^evt\.(permission\.(request|resolved)|sentinel\.alert)$/.test(type)) {
            logMain(`${tag} refused: only permission and sentinel events can be delivered`)
            break
          }
          logMain(`${tag} delivering ${type} through the message handler (nothing is sent to the daemon)`)
          daemonClient.devDeliver(type, (step['payload'] ?? {}) as Record<string, unknown>)
          break
        }
        case 'revoke': {
          const live = liveSessions()
          const target = live[Number(step['index'] ?? 0)]
          if (!target) {
            logMain(`${tag} no live session to revoke`)
            break
          }
          logMain(`${tag} delivering evt.pty.revoke for ${target.sessionId} (shell pid ${target.shellPid ?? '?'})`)
          daemonClient.devDeliver('evt.pty.revoke', { sessionId: target.sessionId, reason: 'dev harness revoke test' })
          break
        }
        case 'quit':
          logMain(`${tag} closing the app`)
          app.quit()
          return
        default: {
          const out = await wc()?.executeJavaScript(`window.__tessaDev(${JSON.stringify(step)})`)
          logMain(`${tag} -> ${typeof out === 'string' ? out : JSON.stringify(out)}`)
        }
      }
    } catch (err) {
      logMain(`${tag} FAILED ${(err as Error).message}`)
    }
  }
  logMain('DEVSCRIPT done')
}

let teardownStarted = false
app.on('before-quit', (event) => {
  if (teardownStarted) return
  teardownStarted = true
  event.preventDefault()
  sessionDirty = true
  flushSession()
  const live = liveSessions().filter((s) => !isEnding(s.sessionId))
  void shutdownPtyHost()
    .then(async () => {
      for (const s of live) {
        if (s.shellPid !== undefined && !shellPidAlive(s.shellPid)) {
          await reportPty(daemonClient, s.sessionId, 'exited', 'Console closed')
        } else {
          logMain(`session ${s.sessionId.slice(0, 8)} not observed dead at quit - not reported`)
        }
      }
      if (endingWork.size) {
        logMain(`waiting for ${endingWork.size} shell(s) still being ended before closing the link`)
        await Promise.allSettled([...endingWork])
      }
    })
    .finally(() => {
      daemonClient.dispose()
      supervisor.stop()
    })
    .finally(() => app.quit())
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

