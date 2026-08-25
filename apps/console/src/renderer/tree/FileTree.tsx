/**
 * apps/console/src/renderer/tree/FileTree.tsx — the collapsible file sidebar.
 *
 * READ-ONLY, LAZY, AND METADATA-ONLY. The reading itself happens in main (see
 * main/filetree.ts, which explains why `lstat` and never `stat`); this file is
 * only the surface.
 *
 * WHY IT IS READ-ONLY. Rename, delete and new-file are red-tier actions with an
 * approval card behind them. A context menu that deletes things would be a
 * permission surface nobody asked for, sitting one slip away from his work.
 *
 * WHY A CLICK INSERTS A PATH. It matches drag-and-drop, which already does
 * exactly this, and it is the safe answer: opening a file is a bigger decision
 * and reading a OneDrive placeholder is the one operation this whole subsystem
 * is built to avoid.
 */

import { useCallback, useEffect, useState } from 'react'

export interface TreeEntry {
  name: string
  path: string
  dir: boolean
  link: boolean
  size: number
}

interface Listing {
  path: string
  entries: TreeEntry[]
  total: number
  truncated: boolean
  error?: string
}

export interface FileTreeProps {
  /** The active pane's opening directory. */
  root: string
  /** Insert a path into the focused pane — same route drag-and-drop uses. */
  onInsertPath: (path: string) => void
}

function shortSize(n: number): string {
  if (n < 0) return ''
  if (n < 1024) return `${n}B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)}K`
  return `${Math.round(n / (1024 * 1024))}M`
}

export function FileTree({ root, onInsertPath }: FileTreeProps): React.JSX.Element {
  const [listings, setListings] = useState<Record<string, Listing>>({})
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState<Record<string, boolean>>({})

  const load = useCallback(async (dir: string) => {
    setBusy((b) => ({ ...b, [dir]: true }))
    try {
      const r = await window.tessa.listDir(dir)
      setListings((l) => ({ ...l, [dir]: r }))
    } catch (err) {
      setListings((l) => ({
        ...l,
        [dir]: { path: dir, entries: [], total: 0, truncated: false, error: (err as Error).message },
      }))
    } finally {
      setBusy((b) => ({ ...b, [dir]: false }))
    }
  }, [])

  // The root is read when it changes — one directory, not a walk.
  useEffect(() => {
    if (!root) return
    setListings({})
    setOpen({ [root]: true })
    void load(root)
  }, [root, load])

  const toggle = useCallback(
    (dir: string) => {
      setOpen((o) => {
        const next = !o[dir]
        // READ ON EXPAND, NOT BEFORE. This is the line that keeps 17,340
        // OneDrive placeholders untouched until he actually asks for one.
        if (next && !listings[dir]) void load(dir)
        return { ...o, [dir]: next }
      })
    },
    [listings, load],
  )

  const rows = (dir: string, depth: number): React.JSX.Element[] => {
    const l = listings[dir]
    if (!l) return busy[dir] ? [<div key={`${dir}:…`} className="tree-note" style={{ paddingLeft: depth * 12 + 8 }}>reading…</div>] : []
    if (l.error) {
      // AN EMPTY FOLDER AND AN UNREADABLE ONE ARE DIFFERENT FACTS, and a tree
      // that renders both as nothing is lying about one of them.
      return [
        <div key={`${dir}:!`} className="tree-error" style={{ paddingLeft: depth * 12 + 8 }}>
          cannot read — {l.error}
        </div>,
      ]
    }
    const out: React.JSX.Element[] = []
    if (l.entries.length === 0) {
      out.push(
        <div key={`${dir}:empty`} className="tree-note" style={{ paddingLeft: depth * 12 + 8 }}>
          empty
        </div>,
      )
    }
    for (const e of l.entries) {
      const isOpen = Boolean(open[e.path])
      out.push(
        <div
          key={e.path}
          className="tree-row"
          data-kind={e.dir ? 'dir' : 'file'}
          style={{ paddingLeft: depth * 12 + 8 }}
          title={e.path}
          onClick={() => (e.dir ? toggle(e.path) : onInsertPath(e.path))}
        >
          <span className="tree-caret">{e.dir ? (isOpen ? '▾' : '▸') : ' '}</span>
          <span className="tree-name">{e.name}</span>
          {/*
            NO CLOUD MARKER. Node cannot tell a dehydrated file from a tiny
            MFT-resident one — the check that tried flagged 610 of his own
            local files. The tree never opens anything, so it does not need to
            know; core/tools/files.py answers it properly where a read happens.
          */}
          {e.link ? <span className="tree-cloud" title="link — never followed">↗</span> : null}
          {!e.dir && e.size >= 0 ? <span className="tree-size">{shortSize(e.size)}</span> : null}
        </div>,
      )
      if (e.dir && isOpen) out.push(...rows(e.path, depth + 1))
    }
    if (l.truncated) {
      out.push(
        <div key={`${dir}:more`} className="tree-note" style={{ paddingLeft: depth * 12 + 8 }}>
          showing {l.entries.length} of {l.total.toLocaleString()} — open it in a terminal to see
          the rest
        </div>,
      )
    }
    return out
  }

  return (
    <aside className="tree">
      <div className="tree-head">
        <span className="tree-root" title={root}>
          {root ? (root.split(/[\\/]/).filter(Boolean).pop() ?? root) : 'no folder'}
        </span>
        {/*
          MANUAL REFRESH, DELIBERATELY. A watcher on a large tree costs a handle
          per directory and would fire constantly under node_modules during an
          install — for a sidebar he glances at, that is a poor trade. A button
          that says what it does is honest and cheap.
        */}
        <button
          className="tree-refresh"
          title="Re-read the open folders"
          onClick={() => {
            const opened = Object.keys(open).filter((k) => open[k])
            setListings({})
            for (const d of opened) void load(d)
          }}
        >
          ↻
        </button>
      </div>
      <div className="tree-body">{root ? rows(root, 0) : null}</div>
    </aside>
  )
}
