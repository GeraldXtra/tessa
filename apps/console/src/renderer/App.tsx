import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import type { ApprovalsView, GitView, InitView, LinkView, SavedTabView, SettingsView, ShellInfo, ThreatView } from '../preload/index.ts'
import { chordOf, keyFor, RETIRED, sanitiseKeymap, type KeyAction } from './terminal/keymap.ts'
import { TermSession, blockVisualState, fmtDuration, hhmm, mmss, type Block, type SessionHost, type SessionSettings } from './terminal/session.ts'
import { Brackets, Kbd, Menu, enabledItems, type MenuItem, type MenuSpec } from './ui/Menu.tsx'
import { Palette, type PaletteItem } from './ui/Palette.tsx'
import { Notes, SettingsPanel, StartScreen, type Note } from './ui/Panels.tsx'
import { installDevHarness } from './dev.ts'

type Mode = 'one' | 'v' | 'h'

interface Layout {
  mode: Mode
  panes: [string | null, string | null]
  focus: 0 | 1
}

interface Rect {
  x: number
  y: number
  w: number
  h: number
}

const FONT_MIN = 11
const FONT_MAX = 20
const FONT_DEFAULT = 14
const LONG_COMMAND_MS = 5000
const HEADER_H = 30
const TEXT_INPUT_CHORDS = new Set(['ctrl+a', 'ctrl+c', 'ctrl+v', 'ctrl+x', 'ctrl+z', 'ctrl+y', 'ctrl+insert', 'shift+insert', 'ctrl+shift+v', 'ctrl+shift+z'])

let tabSeq = 0
let noteSeq = 0

function leaf(p: string): string {
  const parts = p.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] ?? p
}

export default function App(): React.JSX.Element {
  const [init, setInit] = useState<InitView | null>(null)
  const [settings, setSettings] = useState<SettingsView | null>(null)
  const [fontSize, setFontSize] = useState(FONT_DEFAULT)
  const [tabs, setTabs] = useState<string[]>([])
  const [layout, setLayout] = useState<Layout>({ mode: 'one', panes: [null, null], focus: 0 })
  const [closed, setClosed] = useState<SavedTabView[]>([])
  const [recent, setRecent] = useState<string[]>([])
  const [link, setLink] = useState<LinkView>({ linked: false, phase: 'offline', detail: '' })
  const [approvals, setApprovals] = useState<ApprovalsView>({ count: 0 })
  const [threat, setThreat] = useState<ThreatView>({ count: 0 })
  const [menu, setMenu] = useState<MenuSpec | null>(null)
  const [menuIndex, setMenuIndex] = useState(0)
  const [palette, setPalette] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [guard, setGuard] = useState<{ tabId: string; text: string; lines: number } | null>(null)
  const [notes, setNotes] = useState<Note[]>([])
  const [winSize, setWinSize] = useState({ w: window.innerWidth, h: window.innerHeight })
  const [area, setArea] = useState({ w: 0, h: 0 })
  const [git, setGit] = useState<GitView | null>(null)
  const [, setVersion] = useState(0)
  const [tick, setTick] = useState(0)
  const [maximized, setMaximized] = useState(false)
  const [sweep, setSweep] = useState<{ pane: 0 | 1; n: number } | null>(null)

  const sessions = useRef(new Map<string, TermSession>())
  const order = useRef<string[]>([])
  const initDone = useRef(false)
  const lastRects = useRef(new Map<string, Rect>())
  const attached = useRef(new Set<string>())
  const areaRef = useRef<HTMLDivElement | null>(null)
  const stateRef = useRef({ tabs, layout, settings, fontSize, closed, recent, init, link, guard, menu, palette, settingsOpen, renaming })
  stateRef.current = { tabs, layout, settings, fontSize, closed, recent, init, link, guard, menu, palette, settingsOpen, renaming }
  const versionQueued = useRef(false)

  const bump = useCallback(() => {
    if (versionQueued.current) return
    versionQueued.current = true
    requestAnimationFrame(() => {
      versionQueued.current = false
      setVersion((v) => v + 1)
    })
  }, [])

  const keymap = useMemo<Record<string, KeyAction>>(() => sanitiseKeymap(settings?.keymap ?? {}), [settings])
  const design = settings?.designKeymap ?? {}
  const key = useCallback((a: KeyAction) => keyFor(keymap, design, a), [keymap, design])

  const addNote = useCallback((label: string, text: string, kind: '' | 'wait' | 'threat' = '') => {
    const id = ++noteSeq
    const n: Note = { id, label, text, kind, time: hhmm(Date.now()), out: false }
    setNotes((ns) => [...ns, n].slice(-3))
    const life = kind === 'threat' ? 9000 : 6000
    window.setTimeout(() => setNotes((ns) => ns.map((x) => (x.id === id ? { ...x, out: true } : x))), life)
    window.setTimeout(() => setNotes((ns) => ns.filter((x) => x.id !== id)), life + 320)
  }, [])

  const shellInfo = useCallback(
    (id: string): ShellInfo | undefined => stateRef.current.init?.shells.find((s) => s.id === id),
    [],
  )

  const defaultShellOf = useCallback((): string => {
    const want = stateRef.current.settings?.defaultShell ?? 'powershell'
    return stateRef.current.init?.shells.find((s) => s.id === want)?.available ? want : 'powershell'
  }, [])

  const shellLabel = useCallback(
    (id: string): string => {
      const s = shellInfo(id)
      if (!s) return id
      return s.version && !s.name.includes(s.version) ? `${s.name} ${s.version}` : s.name
    },
    [shellInfo],
  )

  const sessionSettings = useCallback((): SessionSettings => {
    const s = stateRef.current.settings
    return {
      fontSize: stateRef.current.fontSize,
      scrollback: s?.scrollback ?? 10_000,
      outputColours: s?.outputColours ?? 'standard',
      cursorStyle: s?.cursorStyle ?? 'block',
      pasteGuard: s?.pasteGuard ?? true,
      rightClickPastes: s?.rightClickPastes ?? true,
      copyOnSelect: s?.copyOnSelect ?? false,
    }
  }, [])

  const paneCount = layout.mode === 'one' ? 1 : 2
  const focusedId = layout.panes[layout.focus] ?? null
  const focused = focusedId ? sessions.current.get(focusedId) : undefined

  const pushRecent = useCallback((cwd: string) => {
    void window.tessa.checkDir(cwd).then((dir) => {
      if (!dir) return
      setRecent((r) => [dir, ...r.filter((x) => x.toLowerCase() !== dir.toLowerCase())].slice(0, 3))
    })
  }, [])

  const hostRef = useRef<SessionHost | null>(null)
  if (!hostRef.current) {
    hostRef.current = {
      changed: () => bump(),
      note: (label, text, kind) => addNote(label, text, kind ?? ''),
      focusMe: (id) => focusTabRef.current(id),
      blockMenu: (id, blockId, x, y) => openBlockMenuRef.current(id, blockId, x, y),
      cwdChanged: (_id, cwd) => {
        pushRecent(cwd)
        bump()
      },
      commandEnded: (id, b) => commandEndedRef.current(id, b),
      isShown: (id) => {
        const L = stateRef.current.layout
        return L.panes[0] === id || (L.mode !== 'one' && L.panes[1] === id)
      },
      isFocused: (id) => {
        const L = stateRef.current.layout
        return L.panes[L.focus] === id
      },
      settings: () => sessionSettings(),
      shellLabel: (s) => shellLabel(s),
      pasteAsk: (id, text, lines) => setGuard({ tabId: id, text, lines }),
      openLink: (req) => {
        void window.tessa.openLink(req).then((r) => addNote(r.ok ? 'OPEN' : 'NOT OPENED', r.message))
      },
      log: (msg) => console.log(msg),
    }
  }
  const host = hostRef.current

  const focusTabRef = useRef<(id: string) => void>(() => undefined)
  const openBlockMenuRef = useRef<(id: string, blockId: number, x: number, y: number) => void>(() => undefined)
  const commandEndedRef = useRef<(id: string, b: Block) => void>(() => undefined)

  const createTab = useCallback(
    (shellId: string, cwd: string, opts: { name?: string; banner?: string; pane?: 0 | 1; show?: boolean } = {}): string => {
      const id = `t${++tabSeq}`
      const s = new TermSession({ id, shellId, cwd, ...(opts.name ? { name: opts.name } : {}), ...(opts.banner ? { banner: opts.banner } : {}), host })
      sessions.current.set(id, s)
      order.current = [...order.current, id]
      setTabs(order.current)
      if (opts.show !== false) {
        setLayout((L) => {
          const pane = opts.pane ?? (L.focus < (L.mode === 'one' ? 1 : 2) ? L.focus : 0)
          const panes: [string | null, string | null] = [...L.panes]
          panes[pane] = id
          return { ...L, panes, focus: pane }
        })
        setSweep((sw) => ({ pane: opts.pane ?? stateRef.current.layout.focus, n: (sw?.n ?? 0) + 1 }))
      }
      return id
    },
    [host],
  )

  const startCwd = useCallback((): string => {
    const st = stateRef.current
    const f = st.layout.panes[st.layout.focus]
    const cur = f ? sessions.current.get(f)?.cwd : undefined
    return cur || st.init?.startDir || st.init?.home || ''
  }, [])

  const newTab = useCallback(
    (shellId: string, cwd?: string) => {
      const info = shellInfo(shellId)
      if (!info?.available) {
        addNote('NEW TAB', `${info?.name ?? shellId} is not found on this machine.`)
        return
      }
      closeOverlays()
      createTab(shellId, cwd ?? startCwd())
    },
    [createTab, shellInfo, startCwd, addNote],
  )

  function closeOverlays(): void {
    setMenu(null)
    setPalette(false)
    setSettingsOpen(false)
  }

  const disposeTab = useCallback((id: string, why: string) => {
    const s = sessions.current.get(id)
    if (!s) return
    s.dispose(why)
    sessions.current.delete(id)
    attached.current.delete(id)
    lastRects.current.delete(id)
  }, [])

  const closeTab = useCallback(
    (id: string) => {
      const s = sessions.current.get(id)
      if (!s) return
      setClosed((c) => [...c, { shellId: s.shellId, cwd: s.cwd, name: s.name }].slice(-6))
      setGuard((g) => (g?.tabId === id ? null : g))
      disposeTab(id, 'tab closed by the owner')
      order.current = order.current.filter((t) => t !== id)
      const ts = order.current
      setTabs(ts)
      setLayout((L) => {
        const panes: [string | null, string | null] = [...L.panes]
        for (const p of [0, 1] as const) {
          if (panes[p] !== id) continue
          const other = panes[p === 0 ? 1 : 0]
          const repl = [...ts].reverse().find((x) => x !== other && (L.mode === 'one' || x !== other))
          panes[p] = repl ?? null
        }
        let mode = L.mode
        let focus = L.focus
        if (mode !== 'one' && (!panes[0] || !panes[1])) {
          const keep = panes[0] ?? panes[1]
          panes[0] = keep
          panes[1] = null
          mode = 'one'
          focus = 0
        }
        return { mode, panes, focus }
      })
    },
    [disposeTab],
  )

  const focusPane = useCallback((p: 0 | 1) => {
    setLayout((L) => (p >= (L.mode === 'one' ? 1 : 2) || p === L.focus ? L : { ...L, focus: p }))
  }, [])

  const showTab = useCallback((id: string) => {
    const s = sessions.current.get(id)
    if (!s) return
    s.unseen = false
    setLayout((L) => {
      const n = L.mode === 'one' ? 1 : 2
      for (let p = 0; p < n; p++) if (L.panes[p] === id) return { ...L, focus: p as 0 | 1 }
      const panes: [string | null, string | null] = [...L.panes]
      panes[L.focus] = id
      return { ...L, panes }
    })
  }, [])

  focusTabRef.current = (id: string) => {
    const L = stateRef.current.layout
    const n = L.mode === 'one' ? 1 : 2
    for (let p = 0; p < n; p++) if (L.panes[p] === id && L.focus !== p) setLayout({ ...L, focus: p as 0 | 1 })
  }

  const setMode = useCallback(
    (m: Mode) => {
      const L = stateRef.current.layout
      if (m === L.mode) return
      if (m === 'one') {
        setLayout({ mode: 'one', panes: [L.panes[L.focus] ?? L.panes[0], null], focus: 0 })
        return
      }
      if (L.mode !== 'one') {
        setLayout({ ...L, mode: m })
        return
      }
      const cur = L.panes[0]
      const others = order.current.filter((t) => t !== cur)
      const other = others.find((t) => sessions.current.get(t)?.running) ?? others[0]
      if (other) {
        setLayout({ mode: m, panes: [cur, other], focus: L.focus })
      } else {
        setLayout({ mode: m, panes: [cur, null], focus: 0 })
        createTab(defaultShellOf(), startCwd(), { pane: 1 })
      }
    },
    [createTab, startCwd],
  )

  const toggleSplit = useCallback(() => setMode(stateRef.current.layout.mode === 'one' ? 'v' : 'one'), [setMode])

  const moveToOther = useCallback(
    (id: string) => {
      const L = stateRef.current.layout
      if (L.mode === 'one') {
        const other = id !== L.panes[0] ? id : order.current.find((t) => t !== id)
        if (other) setLayout({ mode: 'v', panes: [L.panes[0], other], focus: 1 })
        else {
          setLayout({ mode: 'v', panes: [L.panes[0], null], focus: 1 })
          createTab(defaultShellOf(), startCwd(), { pane: 1 })
        }
        return
      }
      if (L.panes[0] === id || L.panes[1] === id) {
        const p: 0 | 1 = L.panes[0] === id ? 0 : 1
        const q: 0 | 1 = p === 0 ? 1 : 0
        const panes: [string | null, string | null] = [...L.panes]
        panes[p] = L.panes[q] ?? null
        panes[q] = id
        setLayout({ ...L, panes, focus: q })
      } else {
        const q = L.focus === 0 ? 1 : 0
        const panes: [string | null, string | null] = [...L.panes]
        panes[q] = id
        setLayout({ ...L, panes, focus: q as 0 | 1 })
      }
    },
    [createTab, startCwd],
  )

  const cycleTab = useCallback(
    (d: number) => {
      const ts = order.current
      if (!ts.length) return
      const L = stateRef.current.layout
      const cur = L.panes[L.focus]
      const i = ts.findIndex((t) => t === cur)
      const next = ts[(i + d + ts.length) % ts.length]
      if (next) showTab(next)
    },
    [showTab],
  )

  const setFont = useCallback((n: number) => {
    const v = Math.max(FONT_MIN, Math.min(FONT_MAX, Math.round(n)))
    setFontSize(v)
    void window.tessa.saveSettings({ fontSize: v })
  }, [])

  const patchSettings = useCallback((p: Partial<SettingsView>) => {
    setSettings((s) => (s ? { ...s, ...p } : s))
    void window.tessa.saveSettings(p as Record<string, unknown>).then((r) => {
      if (!r.ok) addNote('SETTINGS', `Not saved: ${r.error ?? 'unknown reason'}`)
    })
  }, [addNote])

  const reopenClosed = useCallback(() => {
    const list = stateRef.current.closed
    if (!list.length) return
    setClosed([])
    let first: string | null = null
    for (const c of list) {
      const id = createTab(c.shellId, c.cwd, { name: c.name, banner: `Reopened in ${c.cwd}`, show: false })
      if (!first) first = id
    }
    if (first) setLayout({ mode: 'one', panes: [first, null], focus: 0 })
  }, [createTab])

  const restartTab = useCallback(
    (id: string) => {
      const s = sessions.current.get(id)
      if (!s) return
      void s.restart(`${shellLabel(s.shellId)} restarted at ${hhmm(Date.now())}`)
    },
    [shellLabel],
  )

  const dupTab = useCallback(
    (id: string) => {
      const s = sessions.current.get(id)
      if (!s) return
      createTab(s.shellId, s.cwd)
    },
    [createTab],
  )

  const openFind = useCallback(() => {
    closeOverlays()
    const L = stateRef.current.layout
    const f = L.panes[L.focus]
    const s = f ? sessions.current.get(f) : undefined
    if (!s) return
    s.setFindOpen(true)
    bump()
    window.setTimeout(() => (document.querySelector('.ph.find input') as HTMLInputElement | null)?.select(), 0)
  }, [bump])

  const closeFind = useCallback(() => {
    for (const s of sessions.current.values()) if (s.findOpen) s.setFindOpen(false)
    bump()
    focusedSessionRef.current()?.focus()
  }, [bump])

  const focusedSessionRef = useRef<() => TermSession | undefined>(() => undefined)
  focusedSessionRef.current = () => {
    const L = stateRef.current.layout
    const f = L.panes[L.focus]
    return f ? sessions.current.get(f) : undefined
  }

  const startRename = useCallback((id: string) => {
    closeOverlays()
    setRenaming(id)
  }, [])

  const pickFolderTab = useCallback(async () => {
    const dir = await window.tessa.pickFolder()
    if (dir) newTab(defaultShellOf(), dir)
  }, [newTab])

  const openPicker = useCallback(() => {
    const st = stateRef.current
    const shells = st.init?.shells ?? []
    const plus = document.querySelector('.newtab')?.getBoundingClientRect()
    const found = shells.filter((s) => s.available).length
    const items: MenuItem[] = shells.map((s) => ({
      tag: s.tag,
      label: s.version && !s.name.includes(s.version) ? `${s.name} ${s.version}` : s.name,
      keycap: s.available ? key(`new${s.id === 'powershell' ? 'PowerShell' : s.id === 'gitbash' ? 'GitBash' : s.id === 'cmd' ? 'Cmd' : s.id === 'pwsh' ? 'Pwsh' : 'Wsl'}` as KeyAction) : 'NOT FOUND',
      disabled: !s.available,
      run: () => newTab(s.id),
    }))
    setMenuIndex(0)
    setMenu({
      kind: 'picker',
      x: plus ? plus.left : 200,
      y: plus ? plus.bottom + 4 : 44,
      width: 372,
      head: ['NEW TAB', `${found} OF ${shells.length} FOUND`],
      groups: [
        items,
        [
          {
            tag: 'PANE',
            label: 'New PowerShell in the other pane',
            keycap: key('toggleSplit'),
            run: () => {
              const L = stateRef.current.layout
              const q: 0 | 1 = L.mode === 'one' ? 1 : L.focus === 0 ? 1 : 0
              if (L.mode === 'one') setLayout({ mode: 'v', panes: [L.panes[0], null], focus: 1 })
              createTab('powershell', startCwd(), { pane: q })
            },
          },
        ],
      ],
    })
  }, [key, newTab, createTab, startCwd])

  const openTabMenu = useCallback(
    (id: string, x: number, y: number) => {
      const s = sessions.current.get(id)
      if (!s) return
      const L = stateRef.current.layout
      const inTwo = L.mode !== 'one' && (L.panes[0] === id || L.panes[1] === id)
      setMenuIndex(0)
      setMenu({
        kind: 'tab',
        x,
        y,
        width: 300,
        head: [tabTitleOf(s).toUpperCase(), shellInfo(s.shellId)?.tag ?? ''],
        groups: [
          [
            { tag: 'NAME', label: 'Rename', keycap: 'DOUBLE CLICK', run: () => startRename(id) },
            { tag: 'COPY', label: 'Duplicate', run: () => dupTab(id) },
            { tag: 'PANE', label: inTwo ? 'Move to the other pane' : 'Open in the other pane', run: () => moveToOther(id) },
            { tag: 'SHELL', label: 'Restart the shell', run: () => restartTab(id) },
          ],
          [
            { tag: 'CLOSE', label: 'Close', keycap: key('closeTab'), run: () => closeTab(id) },
            {
              tag: 'OTHERS',
              label: 'Close the other tabs',
              disabled: order.current.length < 2,
              run: () => {
                for (const t of [...order.current]) if (t !== id) closeTab(t)
                setLayout({ mode: 'one', panes: [id, null], focus: 0 })
              },
            },
          ],
        ],
      })
    },
    [key, startRename, dupTab, moveToOther, restartTab, closeTab, shellInfo],
  )

  openBlockMenuRef.current = (id: string, blockId: number, x: number, y: number) => {
    const s = sessions.current.get(id)
    const b = s?.blockById(blockId)
    if (!s || !b) return
    s.select(blockId)
    const st = blockVisualState(b)
    const out = s.outputOf(blockId)
    const again = s.canRunAgain(blockId)
    const status =
      st === 'run' ? 'RUNNING' : st === 'stop' ? 'STOPPED' : !b.hasExit ? 'DONE' : `EXIT ${b.exit}`
    const took = st === 'run' ? '' : `  ${fmtDuration((b.ended ?? 0) - (b.started ?? 0))}`
    setMenuIndex(0)
    setMenu({
      kind: 'block',
      x: x + 10,
      y: y + 10,
      width: 330,
      head: [`STARTED ${b.started ? hhmm(b.started) : '--:--'}`, `${status}${took}`],
      groups: [
        [
          {
            tag: 'COPY',
            label: 'Copy the command',
            sub: b.command,
            run: () => {
              void window.tessa.clipWrite(b.command).then(() => addNote('COPIED', b.command.length > 70 ? `${b.command.slice(0, 70)}...` : b.command))
            },
          },
          {
            tag: 'COPY',
            label: 'Copy the output',
            keycap: `${out.lines} LINES`,
            run: () => {
              void window.tessa.clipWrite(s.outputOf(blockId).text).then(() => addNote('COPIED', `${out.lines} lines of output.`))
            },
          },
          {
            tag: 'RUN',
            label: 'Run it again',
            ...(again.ok ? {} : { sub: again.why }),
            disabled: !again.ok,
            run: () => {
              const ok = s.runAgain(blockId)
              console.log(`RUNAGAIN tab=${id} block=${blockId} typed=${b.typed === null ? 'none' : 'yes'} sent=${ok}`)
              if (!ok) addNote('RUN', s.canRunAgain(blockId).why || 'Not run.')
              s.select(null)
            },
          },
        ],
      ],
    })
  }

  commandEndedRef.current = (id: string, b: Block) => {
    const L = stateRef.current.layout
    const shown = L.panes[0] === id || (L.mode !== 'one' && L.panes[1] === id)
    const ms = (b.ended ?? 0) - (b.started ?? 0)
    const s = sessions.current.get(id)
    if (!shown && s && ms >= LONG_COMMAND_MS) {
      s.unseen = true
      const n = order.current.indexOf(id) + 1
      const how = b.stopped ? 'was stopped' : b.hasExit ? `finished in ${fmtDuration(ms).toLowerCase()}, exit ${b.exit}` : `finished in ${fmtDuration(ms).toLowerCase()}`
      addNote(`TAB ${n}  ${tabTitleOf(s).toUpperCase()}`, `${b.command} ${how}.`)
    }
    if (L.panes[L.focus] === id) refreshGit(true)
  }

  function tabTitleOf(s: TermSession): string {
    if (s.name) return s.name
    const r = s.running
    if (r?.command) return r.command
    return leaf(s.cwd) || s.cwd || shellLabel(s.shellId)
  }

  const refreshGitRef = useRef<(withCount: boolean) => void>(() => undefined)
  function refreshGit(withCount: boolean): void {
    refreshGitRef.current(withCount)
  }
  refreshGitRef.current = (withCount: boolean) => {
    const s = focusedSessionRef.current()
    if (!s || !s.cwd) {
      setGit(null)
      return
    }
    if (document.visibilityState !== 'visible') return
    const cwd = s.cwd
    void window.tessa.git(cwd, withCount).then((r) => {
      const cur = focusedSessionRef.current()
      if (cur?.cwd !== cwd) return
      setGit((g) => (withCount || !g || g.cwd !== r.cwd ? r : { ...r, changed: g.changed }))
    })
  }

  useEffect(() => {
    let off: (() => void)[] = []
    if (initDone.current) return
    initDone.current = true
    void window.tessa.init().then((v) => {
      setInit(v)
      setSettings(v.settings)
      setFontSize(Math.max(FONT_MIN, Math.min(FONT_MAX, v.settings.fontSize || FONT_DEFAULT)))
      setLink(v.link)
      setApprovals(v.approvals)
      setThreat(v.threat)
      setRecent(v.restore.recent)
      setMaximized(v.maximized)
      for (const p of v.settings.problems) console.log(`SETTINGS ${p}`)
      stateRef.current.init = v
      stateRef.current.settings = v.settings
      const tabsToOpen = v.restore.tabs.filter((t) => v.shells.find((s) => s.id === t.shellId)?.available)
      if (v.startDir) {
        createTab(defaultShellOf(), v.startDir)
      } else if (tabsToOpen.length) {
        let first: string | null = null
        for (const t of tabsToOpen) {
          const id = createTab(t.shellId, t.cwd, { name: t.name, banner: `Reopened in ${t.cwd}`, show: false })
          if (!first) first = id
        }
        if (first) setLayout({ mode: 'one', panes: [first, null], focus: 0 })
      } else {
        createTab(defaultShellOf(), v.home)
      }
      console.log(`SELFCHECK init shells=${v.shells.map((s) => `${s.id}:${s.available ? 'found' : 'missing'}`).join(',')} restore=${tabsToOpen.length} startDir=${v.startDir ?? ''}`)
    })
    off = [
      window.tessa.onLink((l) => {
        const was = stateRef.current.link.linked
        setLink(l)
        if (was && !l.linked) addNote('TESSA', 'The daemon is not answering. Your shells keep working.')
        if (!was && l.linked) {
          addNote('TESSA', 'Linked to the daemon again.')
          for (const s of sessions.current.values()) if (s.status === 'waiting') void s.start()
        }
      }),
      window.tessa.onApprovals((a) => {
        setApprovals(a)
        if (a.latest) addNote('WAITING ON YOU', `${a.latest.detail || `Tessa wants to use ${a.latest.tool}.`} Answer it in the Orb.`, 'wait')
      }),
      window.tessa.onThreat((t) => {
        setThreat(t)
        if (t.latest) addNote('SENTINEL', `${t.latest.title} Open SENTINEL in the Orb.`, 'threat')
      }),
      window.tessa.onSettings((s) => {
        setSettings(s)
      }),
      window.tessa.onPtyEnded((e) => {
        for (const s of sessions.current.values()) {
          if (s.sessionId === e.sessionId) s.ended(e.revoked ? e.why : 'exited', e.code)
        }
      }),
      window.tessa.onMaximized((m) => setMaximized(m)),
    ]
    const onResize = (): void => setWinSize({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', onResize)
    return () => {
      for (const f of off) f()
      window.removeEventListener('resize', onResize)
    }
  }, [createTab, addNote])

  useEffect(() => {
    for (const s of sessions.current.values()) s.applySettings(sessionSettings())
  }, [settings, fontSize, sessionSettings])

  useLayoutEffect(() => {
    const el = areaRef.current
    if (!el) return
    const read = (): void => {
      const r = el.getBoundingClientRect()
      setArea((a) => (Math.abs(a.w - r.width) < 0.5 && Math.abs(a.h - r.height) < 0.5 ? a : { w: r.width, h: r.height }))
    }
    read()
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [init])

  const sessionSig = JSON.stringify([
    tabs.map((id) => {
      const s = sessions.current.get(id)
      return s ? [s.shellId, s.cwd, s.name] : null
    }),
    recent,
  ])
  useEffect(() => {
    if (!initDone.current) return
    const t = window.setTimeout(() => {
      const list: SavedTabView[] = order.current
        .map((id) => sessions.current.get(id))
        .filter((s): s is TermSession => !!s)
        .map((s) => ({ shellId: s.shellId, cwd: s.cwd, name: s.name }))
      void window.tessa.saveSession({ tabs: list, recent: stateRef.current.recent })
    }, 400)
    return () => window.clearTimeout(t)
  }, [sessionSig])

  useEffect(() => {
    for (const s of sessions.current.values()) {
      if (s.id !== focusedId && s.findOpen) s.setFindOpen(false)
    }
  }, [focusedId])

  useEffect(() => {
    for (const p of [0, 1] as const) {
      if (p === 1 && layout.mode === 'one') continue
      const id = layout.panes[p]
      const s = id ? sessions.current.get(id) : undefined
      s?.repaint()
      if (s?.unseen) {
        s.unseen = false
        bump()
      }
    }
  }, [layout, bump])

  const anyRunning = [...sessions.current.values()].some((s) => !!s.running)
  useEffect(() => {
    if (!anyRunning) return
    const i = window.setInterval(() => {
      for (const s of sessions.current.values()) if (s.running) s.queueOverlay()
      setTick((n) => n + 1)
    }, 1000)
    return () => window.clearInterval(i)
  }, [anyRunning])

  const focusedCwd = focused?.cwd ?? ''
  const focusedLastEnded = focused?.last()?.state
  useEffect(() => {
    refreshGit(true)
    if (!focusedCwd) return
    const i = window.setInterval(() => {
      if (document.visibilityState === 'visible') refreshGit(true)
    }, 5000)
    const vis = (): void => {
      if (document.visibilityState === 'visible') refreshGit(true)
    }
    document.addEventListener('visibilitychange', vis)
    window.addEventListener('focus', vis)
    return () => {
      window.clearInterval(i)
      document.removeEventListener('visibilitychange', vis)
      window.removeEventListener('focus', vis)
    }
  }, [focusedId, focusedCwd, focusedLastEnded])

  useEffect(() => {
    if (!focused || stateRef.current.palette || stateRef.current.settingsOpen || stateRef.current.renaming) return
    if (focused.findOpen) {
      const input = document.querySelector('.pane.focus .find-in') as HTMLInputElement | null
      input?.focus()
      return
    }
    focused.focus()
    for (const s of sessions.current.values()) s.queueOverlay()
  }, [focusedId, layout, palette, settingsOpen, renaming, menu, focused])

  const runAction = useCallback(
    (a: KeyAction): boolean => {
      const st = stateRef.current
      const L = st.layout
      const fid = L.panes[L.focus]
      const s = fid ? sessions.current.get(fid) : undefined
      switch (a) {
        case 'copy': {
          const sel = s?.getSelection() ?? ''
          if (sel) void window.tessa.clipWrite(sel)
          return true
        }
        case 'paste':
          if (s) void window.tessa.clipRead().then((t) => t && s.requestPaste(t))
          return true
        case 'selectAll':
          s?.selectAll()
          return true
        case 'clearSelection':
          s?.clearSelection()
          return true
        case 'find':
          if (s?.findOpen) closeFind()
          else openFind()
          return true
        case 'fontIncrease':
          setFont(st.fontSize + 1)
          return true
        case 'fontDecrease':
          setFont(st.fontSize - 1)
          return true
        case 'fontReset':
          setFont(FONT_DEFAULT)
          return true
        case 'toggleSplit':
          toggleSplit()
          return true
        case 'splitRight':
          setMode(L.mode === 'v' ? 'one' : 'v')
          return true
        case 'splitDown':
          setMode(L.mode === 'h' ? 'one' : 'h')
          return true
        case 'closePane':
          if (L.mode !== 'one') setLayout({ mode: 'one', panes: [L.panes[L.focus === 0 ? 1 : 0], null], focus: 0 })
          return true
        case 'focusLeft':
        case 'focusRight':
        case 'focusUp':
        case 'focusDown':
          if (L.mode !== 'one') focusPane(L.focus === 0 ? 1 : 0)
          return true
        case 'newTab':
          newTab('powershell')
          return true
        case 'newPowerShell':
          newTab('powershell')
          return true
        case 'newGitBash':
          newTab('gitbash')
          return true
        case 'newCmd':
          newTab('cmd')
          return true
        case 'newPwsh':
          newTab('pwsh')
          return true
        case 'newWsl':
          newTab('wsl')
          return true
        case 'nextTab':
          cycleTab(1)
          return true
        case 'prevTab':
          cycleTab(-1)
          return true
        case 'closeTab':
          if (fid) closeTab(fid)
          return true
        case 'goToTab1':
        case 'goToTab2':
        case 'goToTab3':
        case 'goToTab4':
        case 'goToTab5':
        case 'goToTab6':
        case 'goToTab7':
        case 'goToTab8':
        case 'goToTab9': {
          const t = order.current[Number(a.slice(-1)) - 1]
          if (t) showTab(t)
          return true
        }
        case 'prevCommand':
          s?.jump(-1)
          return true
        case 'nextCommand':
          s?.jump(1)
          return true
        case 'palette':
          setMenu(null)
          setSettingsOpen(false)
          setPalette((p) => !p)
          return true
        case 'settings':
          setMenu(null)
          setPalette(false)
          setSettingsOpen((p) => !p)
          return true
        default:
          return false
      }
    },
    [closeFind, openFind, setFont, toggleSplit, setMode, focusPane, newTab, cycleTab, closeTab, showTab],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.type !== 'keydown') return
      const st = stateRef.current
      const chord = chordOf(e)
      const tgt = e.target as HTMLElement | null
      const isXterm = !!tgt?.classList?.contains('xterm-helper-textarea')
      const inInput = !isXterm && (tgt instanceof HTMLInputElement || tgt instanceof HTMLTextAreaElement)
      if (st.renaming) return
      const L = st.layout
      const fid = L.panes[L.focus]
      const s = fid ? sessions.current.get(fid) : undefined
      if (st.guard && st.guard.tabId === fid) {
        if (e.key === 'Enter') {
          e.preventDefault()
          e.stopPropagation()
          const g = st.guard
          setGuard(null)
          sessions.current.get(g.tabId)?.pasteNow(g.text)
          return
        }
        if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          setGuard(null)
          console.log('PASTE GUARD cancelled')
          return
        }
      }
      const action = keymap[chord]
      if (action && !RETIRED.has(action) && !(inInput && TEXT_INPUT_CHORDS.has(chord))) {
          e.preventDefault()
        e.stopPropagation()
        runAction(action)
        return
      }
      if (inInput) return
      if (st.menu) {
        const items = enabledItems(st.menu)
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault()
          e.stopPropagation()
          const n = Math.max(1, items.length)
          setMenuIndex((i) => (i + (e.key === 'ArrowDown' ? 1 : -1) + n) % n)
          return
        }
        if (e.key === 'Enter') {
          e.preventDefault()
          e.stopPropagation()
          const it = items[menuIndexRef.current]
          setMenu(null)
          it?.run()
          return
        }
        if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          setMenu(null)
          for (const x of sessions.current.values()) if (x.selected !== null) x.select(null)
          return
        }
      }
      if (e.key === 'Escape') {
        if (st.settingsOpen) {
          e.preventDefault()
          e.stopPropagation()
          setSettingsOpen(false)
          return
        }
        if (s?.findOpen) {
          e.preventDefault()
          e.stopPropagation()
          closeFind()
          return
        }
        if (s && s.selected !== null) {
          e.preventDefault()
          e.stopPropagation()
          s.select(null)
          return
        }
      }
      if (e.key === 'End' && !e.ctrlKey && !e.altKey && !e.shiftKey && s?.newOutput) {
        e.preventDefault()
        e.stopPropagation()
        s.scrollToBottom()
        return
      }
      if (!order.current.length && e.key === 'Enter' && st.closed.length) {
        e.preventDefault()
        e.stopPropagation()
        reopenClosed()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [keymap, runAction, closeFind, reopenClosed])

  const menuIndexRef = useRef(menuIndex)
  menuIndexRef.current = menuIndex

  useEffect(() => {
    if (!init) return
    return installDevHarness({
      sessions: () => sessions.current,
      focused: () => focusedSessionRef.current(),
      runAction,
      layout: () => stateRef.current.layout,
      tabs: () => order.current,
      newTab,
      closeTab,
      setMode,
      moveToOther,
      dupTab,
      restartTab,
      openPicker,
      openTabMenu,
      openFind,
      setFindQuery: (q: string) => {
        focusedSessionRef.current()?.setFindQuery(q)
        bump()
      },
      reopenClosed,
      patchSettings,
      guard: () => stateRef.current.guard,
      notes: () => notesRef.current,
      status: () => (document.querySelector('.sbar') as HTMLElement | null)?.innerText ?? '',
    })
  }, [init, runAction, newTab, closeTab, setMode, moveToOther, dupTab, restartTab, openPicker, openTabMenu, openFind, reopenClosed, patchSettings, bump])

  const notesRef = useRef(notes)
  notesRef.current = notes

  const narrow = winSize.w < 1000

  const slots: Rect[] = useMemo(() => {
    const W = area.w
    const H = area.h
    if (layout.mode === 'one') return [{ x: 0, y: 0, w: W, h: H }]
    if (layout.mode === 'v') {
      const w0 = Math.floor((W - 1) / 2)
      return [
        { x: 0, y: 0, w: w0, h: H },
        { x: w0 + 1, y: 0, w: W - w0 - 1, h: H },
      ]
    }
    const h0 = Math.floor((H - 1) / 2)
    return [
      { x: 0, y: 0, w: W, h: h0 },
      { x: 0, y: h0 + 1, w: W, h: H - h0 - 1 },
    ]
  }, [area, layout.mode])

  const headerFor = (p: number): number => {
    const id = layout.panes[p]
    const s = id ? sessions.current.get(id) : undefined
    if (layout.mode !== 'one') return HEADER_H
    return s?.findOpen && p === layout.focus ? HEADER_H : 0
  }

  const tabRect = (id: string): { rect: Rect; shown: boolean } => {
    for (let p = 0; p < paneCount; p++) {
      if (layout.panes[p] === id && slots[p]) {
        const sl = slots[p]!
        const hh = headerFor(p)
        const r = { x: sl.x, y: sl.y + hh, w: sl.w, h: Math.max(0, sl.h - hh) }
        lastRects.current.set(id, r)
        return { rect: r, shown: true }
      }
    }
    const last = lastRects.current.get(id)
    if (last) return { rect: last, shown: false }
    const sl = slots[0] ?? { x: 0, y: 0, w: area.w, h: area.h }
    return { rect: sl, shown: false }
  }

  const tabMark = (s: TermSession): React.JSX.Element => {
    if (s.running) return <span className="m run">RUN</span>
    if (s.unseen) return <span className="m dot" title="New output" />
    const l = s.last()
    if (l?.state === 'fail') return <span className="m bad">EXIT {l.exit}</span>
    return <span className="m none" />
  }

  const last = focused?.last() ?? null
  void tick

  const paletteItems: PaletteItem[] = useMemo(() => {
    const shells = init?.shells ?? []
    const L: PaletteItem[] = []
    const act = (a: KeyAction) => () => runAction(a)
    const newAct: Record<string, KeyAction> = { powershell: 'newPowerShell', gitbash: 'newGitBash', cmd: 'newCmd', pwsh: 'newPwsh', wsl: 'newWsl' }
    for (const s of shells.filter((x) => x.available)) L.push({ group: 'TABS', label: `New ${s.name} tab`, keycap: key(newAct[s.id]!), run: act(newAct[s.id]!) })
    L.push({ group: 'TABS', label: 'New tab in a folder', keycap: '', run: () => void pickFolderTab() })
    L.push({ group: 'TABS', label: 'Close tab', keycap: key('closeTab'), run: act('closeTab') })
    L.push({ group: 'TABS', label: 'Rename tab', keycap: 'DOUBLE CLICK', run: () => focusedId && startRename(focusedId) })
    L.push({ group: 'TABS', label: 'Next tab', keycap: key('nextTab'), run: act('nextTab') })
    if (closed.length) L.push({ group: 'TABS', label: 'Reopen closed tabs', keycap: '', run: reopenClosed })
    L.push({ group: 'PANES', label: 'One pane', keycap: key('toggleSplit'), run: () => setMode('one') })
    L.push({ group: 'PANES', label: 'Side by side', keycap: key('toggleSplit'), run: () => setMode('v') })
    L.push({ group: 'PANES', label: 'Stacked', keycap: key('splitDown'), run: () => setMode('h') })
    L.push({ group: 'PANES', label: 'Focus the other pane', keycap: key('focusLeft') ? 'ALT ARROWS' : '', run: act('focusLeft') })
    L.push({ group: 'FIND', label: 'Find in this tab', keycap: key('find'), run: act('find') })
    L.push({ group: 'FIND', label: 'Previous command', keycap: key('prevCommand'), run: act('prevCommand') })
    L.push({ group: 'FIND', label: 'Next command', keycap: key('nextCommand'), run: act('nextCommand') })
    L.push({ group: 'VIEW', label: 'Bigger text', keycap: key('fontIncrease'), run: act('fontIncrease') })
    L.push({ group: 'VIEW', label: 'Smaller text', keycap: key('fontDecrease'), run: act('fontDecrease') })
    L.push({ group: 'VIEW', label: 'Normal text size', keycap: key('fontReset'), run: act('fontReset') })
    L.push({ group: 'VIEW', label: 'Output colours: standard', keycap: '', run: () => patchSettings({ outputColours: 'standard' }) })
    L.push({ group: 'VIEW', label: 'Output colours: mono', keycap: '', run: () => patchSettings({ outputColours: 'mono' }) })
    L.push({ group: 'SETTINGS', label: 'Open settings', keycap: key('settings'), run: act('settings') })
    return L
  }, [init, key, runAction, pickFolderTab, focusedId, startRename, closed.length, reopenClosed, setMode, patchSettings])

  if (!init || !settings) {
    return <div id="win" className="loading" />
  }

  const shellsFound = init.shells
  const keyOfShell = (id: string): string =>
    key(({ powershell: 'newPowerShell', gitbash: 'newGitBash', cmd: 'newCmd', pwsh: 'newPwsh', wsl: 'newWsl' } as Record<string, KeyAction>)[id] ?? 'newPowerShell')

  const ruler = (p: number): React.JSX.Element | null => {
    const id = layout.panes[p]
    const s = id ? sessions.current.get(id) : undefined
    if (!s || !s.findOpen || !s.find.query || p !== layout.focus) return null
    const { ticks, total } = s.matchRuler()
    const sl = slots[p]
    if (!sl || !total) return null
    const top = headerFor(p) + 1
    const H = Math.max(1, sl.h - top)
    const view = s.viewWindow()
    return (
      <div className="ruler" style={{ left: sl.x + sl.w - 16, top: sl.y + top, height: H }}>
        {ticks.map((l) => (
          <i key={l} style={{ top: `${((l / total) * H).toFixed(1)}px` }} />
        ))}
        {view.total > view.rows ? (
          <i className="view" style={{ top: `${((view.top / view.total) * H).toFixed(1)}px`, height: `${Math.max(8, (view.rows / view.total) * H).toFixed(1)}px` }} />
        ) : null}
      </div>
    )
  }

  const paneChrome = (p: 0 | 1): React.JSX.Element | null => {
    const sl = slots[p]
    if (!sl) return null
    const id = layout.panes[p]
    const s = id ? sessions.current.get(id) : undefined
    const isFocus = p === layout.focus
    const finding = !!s?.findOpen && isFocus
    const hh = headerFor(p)
    const r = s?.running
    return (
      <div key={`pc${p}`} className={`pane${isFocus ? ' focus' : ''}`} style={{ left: sl.x, top: sl.y, width: sl.w, height: sl.h }}>
        {layout.mode !== 'one' ? <Brackets /> : null}
        {hh > 0 ? (
          finding && s ? (
            <div className="ph find">
              <span className="k">FIND</span>
              <input
                className="find-in"
                defaultValue={s.find.query}
                placeholder="Search this tab"
                aria-label="Find in scrollback"
                autoComplete="off"
                autoFocus
                onChange={(e) => {
                  s.setFindQuery(e.target.value)
                  bump()
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    s.stepFind(e.shiftKey ? -1 : 1)
                    bump()
                  } else if (e.key === 'Escape') {
                    e.preventDefault()
                    closeFind()
                  }
                }}
              />
              <span className="cnt">{!s.find.query ? '' : s.find.total ? `${s.find.at + 1} OF ${s.find.total}${s.find.capped ? '+' : ''}` : 'NO MATCHES'}</span>
              <button onClick={() => (s.stepFind(-1), bump())}>PREV</button>
              <button onClick={() => (s.stepFind(1), bump())}>NEXT</button>
              <button className="esc" onClick={closeFind}>
                CLOSE <span className="dim">ESC</span>
              </button>
            </div>
          ) : (
            <div className="ph" onMouseDown={() => focusPane(p)}>
              {s ? (
                <>
                  <span className="k">{shellInfo(s.shellId)?.tag}</span>
                  <span className="t">{tabTitleOf(s)}</span>
                  {r ? (
                    <span className="run">
                      RUNNING <span>{mmss(Date.now() - (r.started ?? Date.now()))}</span>
                    </span>
                  ) : null}
                </>
              ) : (
                <>
                  <span className="k">{p + 1}</span>
                  <span className="t">No shell</span>
                </>
              )}
            </div>
          )
        ) : null}
        {s?.sticky ? (
          <div
            className={`sticky ${s.sticky.state}`}
            style={{ top: hh }}
            title="Jump to where this command starts"
            onClick={() => {
              s.scrollToBlock(s.sticky!.blockId)
              bump()
            }}
          >
            <div className="g" />
            <div className="l">
              {(() => {
                const t = s.promptRowText(s.sticky.blockId)
                return (
                  <>
                    <span className="pr">{t.prompt}</span>
                    <span className="cmd">{t.command}</span>
                  </>
                )
              })()}
            </div>
            <div className="st">
              {s.sticky.state === 'run' ? 'RUNNING' : s.sticky.state === 'fail' ? `EXIT ${s.sticky.exit}` : s.sticky.state === 'stop' ? 'STOPPED' : hhmm(s.sticky.started)}
            </div>
          </div>
        ) : null}
        {s && (s.status === 'waiting' || s.status === 'failed') ? (
          <div className="waiting" style={{ top: hh }}>
            <span className="lab">{s.status === 'waiting' ? 'WAITING FOR TESSA' : 'NO SHELL'}</span>
            <p>{s.statusText}</p>
          </div>
        ) : null}
        {s?.newOutput ? (
          <button className="newout" onClick={() => (s.scrollToBottom(), s.focus())}>
            NEW OUTPUT BELOW <Kbd k="END" />
          </button>
        ) : null}
        {guard && s && guard.tabId === s.id ? (
          <div className="guard" role="alertdialog" aria-label="Paste of more than one line">
            <div className="q">
              Paste {guard.lines} lines into {shellLabel(s.shellId)}?
              <small>
                {s.shellId === 'gitbash'
                  ? 'The program running now gets each line the moment it arrives.'
                  : `${shellInfo(s.shellId)?.name ?? 'This shell'} runs each line the moment it arrives.`}
              </small>
            </div>
            <button
              className="go"
              onClick={() => {
                const g = guard
                setGuard(null)
                s.pasteNow(g.text)
                s.focus()
              }}
            >
              PASTE<Kbd k="ENTER" />
            </button>
            <button
              onClick={() => {
                setGuard(null)
                console.log('PASTE GUARD cancelled')
                s.focus()
              }}
            >
              CANCEL<Kbd k="ESC" />
            </button>
          </div>
        ) : null}
        {sweep && sweep.pane === p ? <div key={`sw${sweep.n}`} className="scanline" style={{ ['--scan-h' as string]: `${sl.h}px` }} /> : null}
      </div>
    )
  }

  const focusTabLabel = (s: TermSession): string => shellLabel(s.shellId)

  return (
    <div id="win" className={narrow ? 'narrow' : ''} style={{ ['--fs' as string]: `${fontSize}px` }}>
      <header className="tbar">
        <div className="brand">
          <b>TESSA</b>
          <span>CONSOLE</span>
        </div>
        <div className="tabs" role="tablist">
          {tabs.map((id) => {
            const s = sessions.current.get(id)
            if (!s) return null
            const on = id === focusedId
            const other = !on && (layout.panes[0] === id || (layout.mode !== 'one' && layout.panes[1] === id))
            return (
              <div
                key={id}
                className={`tab${on ? ' on' : ''}${other ? ' other' : ''}`}
                role="tab"
                aria-selected={on}
                data-tab={id}
                title={`${focusTabLabel(s)}  ${s.cwd}`}
                onMouseDown={(e) => {
                  if (e.button === 1) {
                    e.preventDefault()
                    closeTab(id)
                  }
                }}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest('.x, input')) return
                  setMenu(null)
                  showTab(id)
                }}
                onDoubleClick={() => startRename(id)}
                onContextMenu={(e) => {
                  e.preventDefault()
                  openTabMenu(id, e.clientX, e.clientY + 8)
                }}
              >
                <span className="k">{shellInfo(s.shellId)?.tag}</span>
                {renaming === id ? (
                  <input
                    className="ren"
                    defaultValue={tabTitleOf(s)}
                    maxLength={40}
                    aria-label="Tab name"
                    autoFocus
                    onFocus={(e) => e.target.select()}
                    onKeyDown={(e) => {
                      e.stopPropagation()
                      if (e.key === 'Enter') {
                        s.name = e.currentTarget.value.trim()
                        setRenaming(null)
                        bump()
                      } else if (e.key === 'Escape') setRenaming(null)
                    }}
                    onBlur={() => setRenaming(null)}
                  />
                ) : (
                  <span className="t">{tabTitleOf(s)}</span>
                )}
                {tabMark(s)}
                <i
                  className="x"
                  title="Close tab"
                  onClick={(e) => {
                    e.stopPropagation()
                    closeTab(id)
                  }}
                />
              </div>
            )
          })}
        </div>
        <button className={`newtab${menu?.kind === 'picker' ? ' on' : ''}`} aria-label="New tab" title="New tab" onClick={() => (menu?.kind === 'picker' ? setMenu(null) : openPicker())} />
        <div className="tfill" />
        <div className="wctl">
          <button className="mn" aria-label="Minimise" tabIndex={-1} onMouseDown={(e) => e.preventDefault()} onClick={() => void window.tessa.windowCommand('minimize')} />
          <button className={`mx${maximized ? ' restore' : ''}`} aria-label="Maximise" tabIndex={-1} onMouseDown={(e) => e.preventDefault()} onClick={() => void window.tessa.windowCommand('maximize')} />
          <button className="cl" aria-label="Close" tabIndex={-1} onMouseDown={(e) => e.preventDefault()} onClick={() => void window.tessa.windowCommand('close')} />
        </div>
      </header>

      <main className={`panes ${layout.mode}`} ref={areaRef}>
        {tabs.map((id) => {
          const { rect, shown } = tabRect(id)
          return (
            <div
              key={id}
              className="tv"
              style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h, visibility: shown ? 'visible' : 'hidden' }}
              ref={(el) => {
                if (!el || attached.current.has(id)) return
                const s = sessions.current.get(id)
                if (!s) return
                attached.current.add(id)
                s.attach(el)
                void s.start()
              }}
            />
          )
        })}
        {layout.mode === 'v' && slots[1] ? <div className="split v" style={{ left: slots[0]!.w, top: 0, width: 1, height: area.h }} /> : null}
        {layout.mode === 'h' && slots[1] ? <div className="split h" style={{ left: 0, top: slots[0]!.h, width: area.w, height: 1 }} /> : null}
        {paneChrome(0)}
        {layout.mode !== 'one' ? paneChrome(1) : null}
        {ruler(0)}
        {layout.mode !== 'one' ? ruler(1) : null}
        {!tabs.length ? (
          <StartScreen
            closedCount={closed.length}
            shells={shellsFound}
            recent={recent}
            keyOf={keyOfShell}
            onReopen={reopenClosed}
            onNew={(id) => newTab(id)}
            onFolder={(f) => {
              void window.tessa.checkDir(f).then((d) => {
                if (d) newTab('powershell', d)
                else addNote('RECENT', `${f} is not available any more.`)
              })
            }}
          />
        ) : null}
      </main>

      <footer className="sbar">
        {focused ? (
          <>
            <div className="sb">
              <span className="v">{shellLabel(focused.shellId)}</span>
            </div>
            <div className="sb cwd">
              <span className="v">{focused.cwd}</span>
            </div>
            {git && git.cwd === focused.cwd && git.repo ? (
              <div className="sb">
                <span className="v">{git.branch || 'detached'}</span>
                {git.changed !== null ? <span className="k">{git.changed} CHANGED</span> : null}
              </div>
            ) : git && git.cwd === focused.cwd && git.note ? (
              <div className="sb">
                <span className="v dim">{git.note}</span>
              </div>
            ) : git && git.cwd === focused.cwd ? (
              <div className="sb">
                <span className="v dim">Not a git folder</span>
              </div>
            ) : null}
            <div className="sb fill" />
            {last ? (
              last.state === 'run' ? (
                <div className="sb last">
                  <span className="v">RUNNING {mmss(Date.now() - last.started)}</span>
                </div>
              ) : (
                <div className={`sb last${last.state === 'fail' ? ' fail' : ''}`}>
                  <span className="v">
                    {last.state === 'stop' ? 'STOPPED' : !last.hasExit ? 'DONE' : `EXIT ${last.exit}`}  {fmtDuration(last.ms)}
                  </span>
                </div>
              )
            ) : null}
          </>
        ) : (
          <>
            <div className="sb">
              <span className="v dim">No shell open</span>
            </div>
            <div className="sb fill" />
          </>
        )}
        {link.linked && approvals.count > 0 ? (
          <div className="sb wait" title="Approvals are answered in the Orb" onClick={() => addNote('WAITING ON YOU', 'The Console cannot open the Orb. Answer it in the Orb.', 'wait')}>
            <span className="v">WAITING ON YOU  {approvals.count}</span>
          </div>
        ) : null}
        {threat.count > 0 ? (
          <div className="sb threat" title="Threats are answered in the Orb" onClick={() => addNote('SENTINEL', 'The Console cannot open the Orb. Open SENTINEL in the Orb.', 'threat')}>
            THREAT
          </div>
        ) : null}
        <div className={`sb tessa${link.linked ? '' : ' off'}`} title={link.linked ? 'Linked to the daemon' : link.detail}>
          <span className="k">TESSA</span>
          <span className="v">{link.linked ? 'LINKED' : 'OFFLINE'}</span>
        </div>
      </footer>

      <Notes notes={notes} />
      {menu ? <Menu spec={menu} winW={winSize.w} winH={winSize.h} activeIndex={menuIndex} setActiveIndex={setMenuIndex} onClose={() => setMenu(null)} /> : null}
      {palette ? <Palette items={paletteItems} onClose={() => setPalette(false)} /> : null}
      {settingsOpen ? (
        <SettingsPanel shells={shellsFound} settings={settings} fontSize={fontSize} onFont={(d) => setFont(fontSize + d)} onPatch={patchSettings} />
      ) : null}
    </div>
  )
}
