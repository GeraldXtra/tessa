import { useCallback, useEffect, useRef, useState } from 'react'

import TerminalView, { paneMenuHandlers } from './terminal/Terminal.tsx'
import { FALLBACK_KEYMAP, sanitiseKeymap, type KeyAction } from './terminal/keymap.ts'
import { FileTree } from './tree/FileTree.tsx'
import { ChatPane } from './chat/ChatPane.tsx'
import { MIN_PANE_PX } from './panes/tree.ts'
import {
  canSplit,
  closePane,
  countLeaves,
  describe,
  dividers,
  findLeaf,
  layout,
  leaves,
  neighbour,
  newLeaf,
  setSplitSize,
  splitPane,
  splitRectOf,
  type Direction,
  type PaneNode,
  type PlacedLeaf,
  type SplitDir,
} from './panes/tree.ts'

type Bridge = 'checking' | 'ok' | 'failed'

interface PaneMeta {
  shellLabel?: string
  pid?: number
  cwd?: string
}

interface Tab {
  id: string
  tree: PaneNode
  focusedPaneId: string
  /** The focused pane temporarily fills this tab. Per tab, not global. */
  zoomed: boolean
}

/** The sidebar's width. One number, used by both the layout and the refusal. */
const TREE_WIDTH_PX = 250

let nextTabId = 0
function newTab(shellId?: string): Tab {
  nextTabId += 1
  const leaf = newLeaf(shellId)
  return { id: `tab-${nextTabId}`, tree: leaf, focusedPaneId: leaf.id, zoomed: false }
}

/**
 * The window: tabs, each holding its own pane tree.
 *
 * PANES FOR TWO THINGS SIDE BY SIDE; TABS FOR MANY TERMINALS.
 *
 * Seven panes filled 1366x768 with terminals of roughly 450x290, and his
 * reaction was "This is not it. It will be hard to read any logs." That capped
 * panes at two for several rounds.
 *
 * HE HAS LIFTED THAT CAP. Panes are now unlimited, as Windows Terminal has
 * them; the only refusal left is geometric, in `canSplit`, and it is stated in
 * measured pixels. Tabs remain uncapped as they always were.
 *
 * WHAT LIVES HERE, AND WHY
 *
 *   the tabs          a tab is a layout container, not a terminal
 *   the active tab    exactly one is visible
 *   focus             per tab, so switching back returns him where he was
 *   the keymap        one file, one load; N copies could drift apart
 *   font size         his eyesight does not vary by pane
 *   the menu listener N subscribers would make one click act N times
 *   the term bar      N bars would eat the height the terminals need
 *
 * Everything else — the shell, the PTY, the grant, the selection, the paste
 * guard, drag-and-drop — is per PANE and lives in Terminal.tsx.
 */
export default function App(): React.JSX.Element {
  const [bridge, setBridge] = useState<Bridge>('checking')
  const [tabs, setTabs] = useState<Tab[]>(() => [newTab()])
  const [activeTabId, setActiveTabId] = useState<string>('')
  const [fontSize, setFontSize] = useState(13)
  const [keymap, setKeymap] = useState<Record<string, KeyAction>>({ ...FALLBACK_KEYMAP })
  const [prefs, setPrefs] = useState({ rightClickPastes: true, copyOnSelect: false, scrollback: 8000 })
  const [shells, setShells] = useState<{ id: string; label: string; available: boolean; how: string }[]>([])
  /** Read inside callbacks, so `addTab` does not need `shells` as a dependency. */
  const shellsRef = useRef<{ id: string; label: string; available: boolean; how: string }[]>([])
  /** Whether the tab strip's shell dropdown is open. */
  const [shellMenuOpen, setShellMenuOpen] = useState(false)
  const [meta, setMeta] = useState<Record<string, PaneMeta>>({})
  const [notice, setNotice] = useState('')
  const [stageSize, setStageSize] = useState({ w: 0, h: 0 })
  const [theme, setTheme] = useState<string>('gold')
  /**
   * COLLAPSED BY DEFAULT — his read and mine. He opens the Console to type, not
   * to browse, and on a 1366px screen the tree is 250px he did not ask for.
   */
  const [treeOpen, setTreeOpen] = useState(false)

  const baseFontRef = useRef(13)
  const fontSeeded = useRef(false)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const tabsRef = useRef<Tab[]>(tabs)
  tabsRef.current = tabs
  const activeRef = useRef('')
  activeRef.current = activeTabId
  const stageSizeRef = useRef(stageSize)
  stageSizeRef.current = stageSize

  const activeTab = tabs.find((t) => t.id === activeTabId) ?? tabs[0]
  const focusedId = activeTab?.focusedPaneId ?? ''

  useEffect(() => {
    if (!activeTabId && tabs[0]) setActiveTabId(tabs[0].id)
  }, [tabs, activeTabId])

  const nodeLeaks = (['require', 'process', 'module', 'global', 'Buffer'] as const).filter(
    (k) => k in globalThis,
  )

  useEffect(() => {
    window.tessa
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

  // ── settings: ONE load for the window, re-read on window focus ────────────
  useEffect(() => {
    let cancelled = false
    const apply = (s: Awaited<ReturnType<typeof window.tessa.getSettings>>): void => {
      if (cancelled) return
      const { keymap: km, rejected } = sanitiseKeymap(s.keymap ?? {})
      setKeymap(Object.keys(km).length ? km : { ...FALLBACK_KEYMAP })
      setPrefs({
        rightClickPastes: s.rightClickPastes,
        copyOnSelect: s.copyOnSelect,
        scrollback: s.scrollback,
      })
      baseFontRef.current = s.fontSize
      if (!fontSeeded.current) {
        fontSeeded.current = true
        setFontSize(s.fontSize)
      }
      for (const p of s.problems ?? []) console.log(`SETTINGS PROBLEM ${p}`)
      for (const r of rejected) console.log(`SETTINGS PROBLEM ${r}`)
      console.log(`SELFCHECK keymap=${Object.keys(km).length} bindings from ${s.path}`)
    }
    void window.tessa
      .getSettings()
      .then(apply)
      .catch((err: unknown) =>
        console.log(`SETTINGS PROBLEM could not read settings: ${(err as Error).message}`),
      )
    void window.tessa
      .getShells()
      .then((r) => {
        if (!cancelled) {
          setShells(r.shells)
          shellsRef.current = r.shells
        }
      })
      .catch((err: unknown) =>
        console.log(`SETTINGS PROBLEM could not list shells: ${(err as Error).message}`),
      )
    const onWinFocus = (): void => {
      void window.tessa
        .reloadSettings()
        .then(apply)
        .catch((err: unknown) =>
          console.log(`SETTINGS PROBLEM reload failed: ${(err as Error).message}`),
        )
    }
    window.addEventListener('focus', onWinFocus)
    return () => {
      cancelled = true
      window.removeEventListener('focus', onWinFocus)
    }
  }, [])

  /**
   * FOLLOW THE ORB'S COMPANION COLOUR.
   *
   * Applied as `data-theme` on <html>; app.css rebinds --accent from it, so
   * every rule that already used var(--accent) themes for free. Re-read on
   * window focus, so switching companion in the Orb recolours this window when
   * he comes back to it — no restart, and no second source of truth.
   */
  useEffect(() => {
    const apply = (): void => {
      void window.tessa
        .getTheme?.()
        .then((t) => {
          if (!t) return
          document.documentElement.setAttribute('data-theme', t.theme)
          setTheme(t.theme)
          console.log(
            `SELFCHECK theme=${t.theme} source=${t.source}` +
              (t.problem ? ` problem="${t.problem}"` : ''),
          )
        })
        .catch((err: unknown) => console.log(`THEME PROBLEM ${(err as Error).message}`))
    }
    apply()
    window.addEventListener('focus', apply)
    return () => window.removeEventListener('focus', apply)
  }, [])

  useEffect(() => {
    const el = stageRef.current
    if (!el) return
    const read = (): void => {
      const r = el.getBoundingClientRect()
      setStageSize((prev) =>
        Math.abs(prev.w - r.width) < 0.5 && Math.abs(prev.h - r.height) < 0.5
          ? prev
          : { w: r.width, h: r.height },
      )
    }
    read()
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const say = useCallback((msg: string) => {
    console.log(`PANE ${msg}`)
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 7000)
  }, [])

  const patchTab = useCallback((tabId: string, fn: (t: Tab) => Tab) => {
    setTabs((ts) => ts.map((t) => (t.id === tabId ? fn(t) : t)))
  }, [])

  /** Which tab owns this pane. A pane lives in exactly one tab, always. */
  const tabOf = useCallback(
    (paneId: string) => tabsRef.current.find((t) => leaves(t.tree).some((l) => l.id === paneId)),
    [],
  )

  // ── panes ────────────────────────────────────────────────────────────────

  const doSplit = useCallback(
    (paneId: string, dir: SplitDir) => {
      const tab = tabOf(paneId)
      if (!tab) return
      const rect = { x: 0, y: 0, w: stageSizeRef.current.w, h: stageSizeRef.current.h }
      const verdict = canSplit(tab.tree, paneId, dir, rect)
      if (!verdict.ok) {
        say(`split refused — ${verdict.reason}`)
        return
      }
      const r = splitPane(tab.tree, paneId, dir)
      if (!r) return
      patchTab(tab.id, (t) => ({ ...t, tree: r.tree, focusedPaneId: r.newId, zoomed: false }))
      say(`split ${dir === 'row' ? 'right' : 'down'} — ${countLeaves(r.tree)} panes in this tab`)
    },
    [say, patchTab, tabOf],
  )

  /**
   * Close the focused PANE. Closing a tab's last pane closes the TAB.
   *
   * His reading and mine: Ctrl+Shift+W means "close what I am looking at". With
   * one pane that is the tab; with two it is the half he is in. A separate
   * close-tab chord for the one-pane case would be a distinction he has to
   * remember for no benefit.
   */
  const doClose = useCallback(
    (paneId: string) => {
      const tab = tabOf(paneId)
      if (!tab) return
      const next = closePane(tab.tree, paneId)
      if (next !== null) {
        const still = leaves(next)
        const focus = still.some((l) => l.id === tab.focusedPaneId)
          ? tab.focusedPaneId
          : (still[0]?.id ?? '')
        patchTab(tab.id, (t) => ({ ...t, tree: next, focusedPaneId: focus, zoomed: false }))
        say(`closed ${paneId} — ${countLeaves(next)} pane(s) left in this tab`)
        return
      }
      setTabs((ts) => {
        const rest = ts.filter((t) => t.id !== tab.id)
        if (rest.length === 0) {
          // THE LAST TAB. A window with nothing in it is not a state worth
          // having — no terminal to type in and no obvious way back — so a
          // fresh one opens, exactly as closing the last pane did before tabs.
          const fresh = newTab()
          setActiveTabId(fresh.id)
          say('that was the last terminal — opened a fresh one')
          return [fresh]
        }
        if (activeRef.current === tab.id) {
          const idx = ts.findIndex((t) => t.id === tab.id)
          const nextActive = rest[Math.min(idx, rest.length - 1)]
          if (nextActive) setActiveTabId(nextActive.id)
        }
        say(`closed the tab — ${rest.length} left`)
        return rest
      })
    },
    [say, patchTab, tabOf],
  )

  const moveFocus = useCallback(
    (paneId: string, dir: Direction) => {
      const tab = tabOf(paneId)
      if (!tab || tab.zoomed) return
      const to = neighbour(tab.tree, paneId, dir, {
        x: 0,
        y: 0,
        w: stageSizeRef.current.w,
        h: stageSizeRef.current.h,
      })
      if (!to) return
      patchTab(tab.id, (t) => ({ ...t, focusedPaneId: to }))
      console.log(`PANE focus ${paneId} -> ${to} (${dir})`)
    },
    [patchTab, tabOf],
  )

  // ── tabs ─────────────────────────────────────────────────────────────────

  /**
   * A NEW TAB OPENS THE DEFAULT SHELL, not the focused pane's.
   *
   * A split is a CONTINUATION — same job, second view — so it inherits the
   * shell. A tab is a new CONTEXT, and inheriting there would mean that after
   * an afternoon in Git Bash every new tab had silently stopped being
   * PowerShell.
   */
  /**
   * A new tab, optionally in a named shell.
   *
   * The argument is what the `∨` dropdown adds over the bare `+`: the `+`
   * passes nothing and the pane resolves the default shell exactly as it always
   * has, while a dropdown pick names one. There is no second shell registry —
   * `shellId` is threaded to the same `newLeaf(shellId)` a split already uses,
   * and main resolves it through `resolveShells()` like every other spawn.
   */
  const addTab = useCallback(
    (shellId?: string) => {
      const t = newTab(shellId)
      setTabs((ts) => [...ts, t])
      setActiveTabId(t.id)
      const label = shellId ? shellsRef.current.find((s) => s.id === shellId)?.label : undefined
      say(`new tab${label ? ` — ${label}` : ''} — ${tabsRef.current.length + 1} open`)
    },
    [say],
  )

  const cycleTab = useCallback((delta: number) => {
    const ts = tabsRef.current
    if (ts.length < 2) return
    const i = ts.findIndex((t) => t.id === activeRef.current)
    const next = ts[(i + delta + ts.length) % ts.length]
    if (next) {
      setActiveTabId(next.id)
      console.log(`TAB switch -> ${next.id}`)
    }
  }, [])

  const closeTab = useCallback(
    (tabId: string) => {
      const tab = tabsRef.current.find((t) => t.id === tabId)
      if (!tab) return
      // Routed through doClose so the last-tab rule lives in exactly one place.
      for (const l of leaves(tab.tree)) doClose(l.id)
    },
    [doClose],
  )

  /**
   * ZOOM: the focused pane fills the tab, and back.
   *
   * Built because it was cheap — the layout already computes from a rect, so
   * zooming is "lay out a single-leaf tree instead", about ten lines. It
   * matters less at two panes than at seven, but it is the difference between
   * splitting for context and being able to read a stack trace without closing
   * anything.
   */
  const toggleZoom = useCallback(
    (paneId: string) => {
      const tab = tabOf(paneId)
      if (!tab || countLeaves(tab.tree) < 2) return
      patchTab(tab.id, (t) => ({ ...t, zoomed: !t.zoomed, focusedPaneId: paneId }))
      say(tab.zoomed ? 'zoom off' : 'zoomed — Ctrl+Shift+Z to go back')
    },
    [patchTab, tabOf, say],
  )

  /**
   * THE TREE TAKES WIDTH FROM THE TERMINALS, so it obeys the same 220px pane
   * minimum a split does.
   *
   * At 1366px a 250px tree leaves two panes at roughly 550px each, which is
   * comfortable. It is the THIRD case that bites: if he is already at two panes
   * on a window he has dragged narrow, opening the tree would push them under
   * the minimum. REFUSING with the number is the same answer `canSplit` gives,
   * and for the same reason — a terminal too narrow to read is worse than one
   * he has to open a tab for.
   */
  const toggleTree = useCallback(() => {
    if (treeOpen) {
      setTreeOpen(false)
      return
    }
    const tab = tabsRef.current.find((t) => t.id === activeRef.current)
    const panes = tab ? countLeaves(tab.tree) : 1
    const after = (stageSizeRef.current.w - TREE_WIDTH_PX) / Math.max(1, panes)
    if (panes > 1 && after < MIN_PANE_PX) {
      say(
        `the file tree needs ${TREE_WIDTH_PX}px and would leave each pane at ` +
          `${Math.max(0, Math.round(after))}px, below the ${MIN_PANE_PX}px minimum — ` +
          `close a pane or widen the window`,
      )
      return
    }
    setTreeOpen(true)
  }, [treeOpen, say])

  /**
   * Turn the focused pane into a chat pane, or split to make room for one.
   *
   * With one pane it SPLITS, because replacing his only terminal with a chat
   * pane would take away the thing he came for. With two it converts the
   * focused one, because the cap is two and something has to give — and the
   * pane he is looking at is the one he chose.
   */
  const openChat = useCallback(() => {
    const tab = tabsRef.current.find((tb) => tb.id === activeRef.current)
    if (!tab) return
    const already = leaves(tab.tree).find((l) => l.content === 'chat')
    if (already) {
      patchTab(tab.id, (tb) => ({ ...tb, focusedPaneId: already.id }))
      say('Tessa is already open in this tab')
      return
    }
    const toChat = (n: PaneNode, id: string): PaneNode =>
      n.kind === 'leaf'
        ? n.id === id
          ? { ...n, content: 'chat' as const }
          : n
        : { ...n, children: [toChat(n.children[0], id), toChat(n.children[1], id)] }

    // No count test any more — `canSplit` answers in geometry, and it is the
    // only thing that can tell a splittable pane from an unsplittable one.
    {
      const rect = { x: 0, y: 0, w: stageSizeRef.current.w, h: stageSizeRef.current.h }
      const verdict = canSplit(tab.tree, tab.focusedPaneId, 'row', rect)
      if (verdict.ok) {
        const r = splitPane(tab.tree, tab.focusedPaneId, 'row')
        if (r) {
          patchTab(tab.id, (tb) => ({
            ...tb, tree: toChat(r.tree, r.newId), focusedPaneId: r.newId, zoomed: false,
          }))
          say('Tessa opened beside your terminal')
          return
        }
      }
    }
    patchTab(tab.id, (tb) => ({ ...tb, tree: toChat(tb.tree, tb.focusedPaneId) }))
    say('this pane is Tessa now — Ctrl+Shift+W closes it')
  }, [patchTab, say])

  const onPaneAction = useCallback(
    (paneId: string, action: KeyAction): boolean => {
      // Every layout action, named. Cheap, and it is how a chord that resolves
      // to the wrong action gets caught instead of guessed at.
      console.log(`ACTION ${action} pane=${paneId}`)
      switch (action) {
        case 'splitRight':
          doSplit(paneId, 'row')
          return true
        case 'splitDown':
          doSplit(paneId, 'col')
          return true
        case 'closePane':
          doClose(paneId)
          return true
        case 'focusLeft':
          moveFocus(paneId, 'left')
          return true
        case 'focusRight':
          moveFocus(paneId, 'right')
          return true
        case 'focusUp':
          moveFocus(paneId, 'up')
          return true
        case 'focusDown':
          moveFocus(paneId, 'down')
          return true
        case 'newTab':
          addTab()
          return true
        case 'nextTab':
          cycleTab(1)
          return true
        case 'prevTab':
          cycleTab(-1)
          return true
        case 'zoomPane':
          toggleZoom(paneId)
          return true
        case 'toggleTree':
          toggleTree()
          return true
        case 'fontIncrease':
          setFontSize((n) => Math.min(48, n + 1))
          return true
        case 'fontDecrease':
          setFontSize((n) => Math.max(6, n - 1))
          return true
        case 'fontReset':
          setFontSize(baseFontRef.current)
          return true
        default:
          return false
      }
    },
    [doSplit, doClose, moveFocus, addTab, cycleTab, toggleZoom, toggleTree],
  )

  /** Replace the focused pane's shell — a new grant and a new PTY. */
  const openShellInFocused = useCallback((shellId: string) => {
    const tab = tabsRef.current.find((t) => t.id === activeRef.current)
    if (!tab) return
    const id = tab.focusedPaneId
    setTabs((ts) =>
      ts.map((t) => {
        if (t.id !== tab.id) return t
        const replace = (n: PaneNode): PaneNode =>
          n.kind === 'leaf'
            ? n.id === id
              ? { ...n, shellId }
              : n
            : { ...n, children: [replace(n.children[0]), replace(n.children[1])] }
        return { ...t, tree: replace(t.tree) }
      }),
    )
  }, [])

  // ── THE ONE MENU SUBSCRIPTION ─────────────────────────────────────────────
  useEffect(() => {
    const LAYOUT_CMDS = new Set([
      'splitRight', 'splitDown', 'closePane', 'focusLeft', 'focusRight',
      'focusUp', 'focusDown', 'newTab', 'nextTab', 'prevTab', 'zoomPane', 'toggleTree',
    ])
    const off = window.tessa.onMenu?.((cmd: string) => {
      if (cmd.startsWith('shell:')) return openShellInFocused(cmd.slice(6))
      /*
        DEV HARNESS: click a chrome control by CSS selector.
        `devkey:` and `devtype:` can drive the terminal, and `menu:` can drive a
        named action — but neither can press a BUTTON, and the tab strip's `+`
        and shell dropdown are buttons. Without this the only proof that the
        dropdown opens would be "I read the code", which is not a proof.
        Absent from every normal launch: nothing sends `devclick:` but a script.
      */
      if (cmd.startsWith('devclick:')) {
        const sel = cmd.slice(9)
        const el = document.querySelector<HTMLElement>(sel)
        console.log(`DEVCLICK ${sel} found=${Boolean(el)}`)
        el?.click()
        return
      }
      const tab = tabsRef.current.find((t) => t.id === activeRef.current)
      const id = tab?.focusedPaneId ?? ''
      if (cmd === 'openChat') {
        openChat()
        return
      }
      if (LAYOUT_CMDS.has(cmd)) {
        onPaneAction(id, cmd as KeyAction)
        return
      }
      // EVERYTHING ELSE GOES TO THE FOCUSED PANE OF THE ACTIVE TAB — never to a
      // pane in a hidden tab, which is exactly what a per-pane subscription
      // would have done.
      const handler = paneMenuHandlers.get(id)
      if (!handler) {
        console.log(`MENU "${cmd}" — no focused pane to act on`)
        return
      }
      handler(cmd)
    })
    return off
  }, [openShellInFocused, onPaneAction, openChat])

  const onMeta = useCallback((paneId: string, m: PaneMeta) => {
    setMeta((prev) => {
      const cur = prev[paneId]
      if (cur && cur.shellLabel === m.shellLabel && cur.pid === m.pid && cur.cwd === m.cwd) {
        return prev
      }
      return { ...prev, [paneId]: m }
    })
  }, [])

  const setFocusInTab = useCallback(
    (paneId: string) => {
      const tab = tabOf(paneId)
      if (tab) patchTab(tab.id, (t) => ({ ...t, focusedPaneId: paneId }))
    },
    [patchTab, tabOf],
  )

  const onDividerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>, tabId: string, anchorId: string, dir: SplitDir) => {
      e.preventDefault()
      const tab = tabsRef.current.find((t) => t.id === tabId)
      const stage = stageRef.current?.getBoundingClientRect()
      if (!tab || !stage) return
      const box = splitRectOf(tab.tree, anchorId, {
        x: 0,
        y: 0,
        w: stageSizeRef.current.w,
        h: stageSizeRef.current.h,
      })
      if (!box) return
      const move = (ev: PointerEvent): void => {
        const f =
          dir === 'row'
            ? (ev.clientX - stage.left - box.x) / box.w
            : (ev.clientY - stage.top - box.y) / box.h
        if (Number.isFinite(f)) {
          patchTab(tabId, (t) => ({ ...t, tree: setSplitSize(t.tree, anchorId, f) }))
        }
      }
      const up = (): void => {
        window.removeEventListener('pointermove', move)
        window.removeEventListener('pointerup', up)
      }
      window.addEventListener('pointermove', move)
      window.addEventListener('pointerup', up)
    },
    [patchTab],
  )

  /**
   * A tab's layout, honouring zoom.
   *
   * ZOOM HIDES THE OTHER PANES; IT DOES NOT UNMOUNT THEM.
   *
   * This used to return ONLY the zoomed leaf, and that killed the other pane's
   * shell. Measured: zooming a two-pane tab issued a SECOND grant on unzoom,
   * because React dropped the hidden pane, its PTY died with it, and the
   * remount took a fresh grant and a fresh shell. A build running in the pane
   * he zoomed away from would have been destroyed by the zoom — silently, and
   * with a new prompt waiting where his output used to be.
   *
   * Every leaf is still returned; the ones that are not zoomed are marked
   * `hidden` and keep their box, exactly as a hidden TAB does, so their column
   * count survives too.
   */
  const layoutOf = useCallback(
    (tab: Tab): (PlacedLeaf & { hidden?: boolean })[] => {
      const rect = { x: 0, y: 0, w: stageSize.w, h: stageSize.h }
      const all = layout(tab.tree, rect)
      if (!tab.zoomed) return all
      const focus = findLeaf(tab.tree, tab.focusedPaneId) ?? leaves(tab.tree)[0]
      if (!focus) return all
      return all.map((r) =>
        r.id === focus.id ? { ...r, ...rect } : { ...r, hidden: true },
      )
    },
    [stageSize],
  )

  useEffect(() => {
    window.__tessaTree = () =>
      tabsRef.current
        .map((t) => {
          const mark = t.id === activeRef.current ? '*' : ' '
          const z = t.zoomed ? ' ZOOM' : ''
          const shape = describe(
            t.tree,
            (id) => meta[id]?.shellLabel ?? findLeaf(t.tree, id)?.shellId,
          )
          return `${mark}${t.id}${z}: ${shape}`
        })
        .join('  ||  ')
    console.log(`SELFCHECK paneTree ${window.__tessaTree()}`)
  }, [tabs, activeTabId, meta])

  const paneCount = activeTab ? countLeaves(activeTab.tree) : 1
  const focusedMeta = meta[focusedId] ?? {}

  const tabTitle = (t: Tab): string => {
    const ls = leaves(t.tree)
    const m = meta[t.focusedPaneId] ?? meta[ls[0]?.id ?? ''] ?? {}
    const shell = m.shellLabel ?? 'terminal'
    // THE CWD IS THE ONE IT OPENED IN, not the live one. Tracking the live
    // directory needs OSC 7, which no default Windows shell emits — and faking
    // it would be worse than a stable label that is true.
    const where = m.cwd ? m.cwd.split(/[\\/]/).filter(Boolean).pop() : ''
    const extra = ls.length > 1 ? ` (${ls.length})` : ''
    return where ? `${shell} — ${where}${extra}` : `${shell}${extra}`
  }

  return (
    <main className="shell">
      <header className="top">
        <h1 className="brand">TESSA CONSOLE</h1>
        {/*
          WHAT HE ASKED ABOUT, ANSWERED.

          "NODE SANDBOXED" is GONE from the surface. It is a security posture
          for someone auditing the app, not information for the person using
          it — and it was shouting at the same weight as everything else. The
          check still runs and still logs SELFCHECK nodeAccess on every launch;
          it just no longer occupies his screen. If it ever reports a LEAK that
          is a different matter, and it says so below.

          "BRIDGE" is renamed. He asked what it was, which is the proof that
          "bridge" meant nothing to him. It is the connection to Tessa Core —
          the daemon that authorises every terminal — so it is now "Tessa Core",
          and it only draws attention to itself when it is NOT connected.
        */}
        <span className="phase">
          Tessa Core <b data-state={bridge}>{bridge === 'ok' ? 'connected' : bridge}</b>
          {nodeLeaks.length === 0 ? null : (
            <>
              {' · '}
              <b data-state="failed">SANDBOX LEAKED {nodeLeaks.join(',')}</b>
            </>
          )}
          {' · '}
          {tabs.length} tab{tabs.length === 1 ? '' : 's'}
          {paneCount > 1 ? ` · ${paneCount} panes` : ''}
        </span>
      </header>

      {/* THE TAB STRIP, as his screenshot shows: a name, an X, and a +. */}
      <div className="tab-row">
      <div className="tab-strip" role="tablist">
        {tabs.map((t) => (
          <div
            key={t.id}
            className="tab"
            data-active={t.id === activeTabId ? 'yes' : 'no'}
            role="tab"
            aria-selected={t.id === activeTabId}
            onMouseDown={() => setActiveTabId(t.id)}
            title={tabTitle(t)}
          >
            <span className="tab-label">{tabTitle(t)}</span>
            <button
              className="tab-x"
              title="Close this tab"
              onMouseDown={(e) => {
                e.stopPropagation()
                closeTab(t.id)
              }}
            >
              ×
            </button>
          </div>
        ))}
        </div>
        {/*
          THE `+` AND THE `▾`, as his Windows Terminal screenshot shows.

          `+` opens a tab in the DEFAULT shell — it passes no id, so the pane
          resolves `settings.defaultShell` exactly as it always did. On this
          machine that is PowerShell. `∨` names one instead.

          This is clickable chrome that an earlier ruling of his ("type only,
          no icon rail") forbade; he has overridden that ruling deliberately.
          Both still have keyboard equivalents — nothing became mouse-only.
        */}
        <div className="tab-new">
          <button className="tab-add" onClick={() => addTab()} title="New tab — Ctrl+Shift+T">
            +
          </button>
          <button
            className="tab-menu"
            title="New tab in a specific shell"
            aria-haspopup="menu"
            aria-expanded={shellMenuOpen}
            onClick={() => setShellMenuOpen((v) => !v)}
          >
            ▾
          </button>
          {shellMenuOpen ? (
            <>
              {/* Below the popup, above everything else: one click anywhere
                  dismisses it without the popup's own buttons losing theirs. */}
              <div className="tab-menu-scrim" onMouseDown={() => setShellMenuOpen(false)} />
              <div className="tab-menu-pop" role="menu">
                {shells.map((sh) => (
                  <button
                    key={sh.id}
                    role="menuitem"
                    disabled={!sh.available}
                    title={sh.available ? sh.how : `not found — ${sh.how}`}
                    onClick={() => {
                      setShellMenuOpen(false)
                      addTab(sh.id)
                    }}
                  >
                    {sh.label}
                  </button>
                ))}
              </div>
            </>
          ) : null}
        </div>
      </div>

      <div className="term-bar">
        {/*
          PID STAYS, QUIETER. It is not always useful, but it is exactly what he
          needs when a shell hangs and he wants to kill it — so it keeps its
          place and loses its volume.
        */}
        <span className="term-meta">
          {focusedMeta.shellLabel ?? '…'}
          {focusedMeta.pid ? <span className="term-pid"> · {focusedMeta.pid}</span> : null}
        </span>
        {shells.map((s) => (
          <button
            key={s.id}
            onClick={() => openShellInFocused(s.id)}
            disabled={!s.available}
            title={
              s.available
                ? `Open ${s.label} in the focused pane — ${s.how}`
                : `Not installed: ${s.how}`
            }
          >
            {s.label}
          </button>
        ))}
        <span className="bar-sep" />
        <button onClick={() => onPaneAction(focusedId, 'splitRight')} title="Ctrl+Shift+D">
          split →
        </button>
        <button onClick={() => onPaneAction(focusedId, 'splitDown')} title="Ctrl+Shift+E">
          split ↓
        </button>
        <button onClick={() => onPaneAction(focusedId, 'zoomPane')} title="Ctrl+Shift+Z">
          {activeTab?.zoomed ? 'unzoom' : 'zoom'}
        </button>
        <button onClick={() => onPaneAction(focusedId, 'closePane')} title="Ctrl+Shift+W">
          close pane
        </button>
        <button onClick={toggleTree} title="File tree — Ctrl+Shift+B">
          {treeOpen ? 'hide files' : 'files'}
        </button>
        <button onClick={openChat} title="Ask Tessa — a chat pane beside your shells">
          ask tessa
        </button>
      </div>

      {notice ? <div className="pane-notice">{notice}</div> : null}

      {/*
        EVERY TAB STAYS MOUNTED; only the active one is visible.

        Hidden tabs are hidden with `visibility`, NOT `display:none`, and that is
        deliberate. An element with `display:none` has no box, so xterm would
        measure zero columns and the fit addon would tell the shell it had a
        one-column terminal. A build running in a hidden tab would then wrap
        every line at column one and the scrollback he came back to would be
        ruined. Keeping the box means a hidden pane cannot lose its column count
        — which is the whole point of a tab that keeps running.
      */}
      <div className="stage-row">
        {treeOpen ? (
          <FileTree
            root={focusedMeta.cwd ?? ''}
            onInsertPath={(path) => {
              // SAME ROUTE AS DRAG-AND-DROP: the path is inserted into the
              // focused pane and never executed.
              const h = paneMenuHandlers.get(focusedId)
              const quoted = /[\s"']/.test(path) ? `"${path.replace(/"/g, '')}"` : path
              h?.(`devtype:${btoa(quoted + ' ')}`)
            }}
          />
        ) : null}
      <div className="pane-stage" ref={stageRef}>
        {/*
          THE "TESSA" WATERMARK USED TO BE HERE AND IS GONE — "remove the Tessa
          Background there ... doesn't look good there". An empty terminal is
          what Windows Terminal shows and what he pointed at. Nothing replaced
          it: inventing decorative artwork is not something to do unasked.
        */}
        {tabs.map((t) => {
          const placed = layoutOf(t)
          const bars =
            t.zoomed || stageSize.w === 0
              ? []
              : dividers(t.tree, { x: 0, y: 0, w: stageSize.w, h: stageSize.h })
          const active = t.id === activeTabId
          // `data-panes` drives ONE css rule: a single-pane tab paints no focus
          // border, because with one pane that border is a rectangle around the
          // whole Console rather than a pane affordance.
          const visible = placed.filter((r) => !r.hidden).length
          return (
            <div
              key={t.id}
              className="tab-page"
              data-active={active ? 'yes' : 'no'}
              data-panes={visible}
            >
              {placed.map((r) => {
                const leaf = findLeaf(t.tree, r.id)
                const isChat = leaf?.content === 'chat'
                return (
                  <div
                    key={r.id}
                    className="pane-frame"
                    data-hidden={r.hidden ? 'yes' : 'no'}
                    style={{ left: r.x, top: r.y, width: r.w, height: r.h }}
                  >
                    {isChat ? (
                      /*
                        NO GRANT IS REQUESTED FOR THIS PANE. A chat pane spawns
                        no PTY, so asking for one would be a grant issued for
                        nothing — and CONTRACT §6.5 exists to make a grant mean
                        a real shell. The gate is skipped by construction here:
                        `startPty` is only ever called from TerminalView.
                      */
                      <ChatPane
                        paneId={r.id}
                        focused={active && r.id === t.focusedPaneId}
                        fontSize={fontSize}
                        onFocus={setFocusInTab}
                        connected={bridge === 'ok'}
                        keymap={keymap}
                        onPaneAction={onPaneAction}
                      />
                    ) : (
                    <TerminalView
                      key={`${r.id}:${leaf?.shellId ?? 'default'}`}
                      paneId={r.id}
                      {...(leaf?.shellId ? { shellId: leaf.shellId } : {})}
                      focused={active && r.id === t.focusedPaneId}
                      fontSize={fontSize}
                      keymap={keymap}
                      prefs={prefs}
                      onFocus={setFocusInTab}
                      onPaneAction={onPaneAction}
                      onMeta={onMeta}
                    />
                    )}
                  </div>
                )
              })}
              {bars.map((d) => (
                <div
                  key={`div-${d.anchorId}-${d.dir}`}
                  className="pane-divider"
                  data-dir={d.dir}
                  style={{ left: d.x, top: d.y, width: d.w, height: d.h }}
                  onPointerDown={(e) => onDividerDown(e, t.id, d.anchorId, d.dir)}
                  role="separator"
                  aria-orientation={d.dir === 'row' ? 'vertical' : 'horizontal'}
                />
              ))}
            </div>
          )
        })}
      </div>
      </div>
    </main>
  )
}
