/**
 * apps/console/src/renderer/panes/tree.ts — the split model.
 *
 * A TREE, NOT A LIST. Gerald asked for "command prompt and powershell side by
 * side, you can open another window by the side too... and the rest in the same
 * window", and then explicitly for more than four. A flat list cannot express
 * "the rest in the same window": splitting the right-hand pane has to subdivide
 * THAT pane, not append a fifth column across the whole window.
 *
 * So splitting a leaf REPLACES it with a split node holding the original and
 * the new one. Splitting again inside either nests further, arbitrarily deep.
 *
 * NO CAP on count or depth — he asked for that in those words. The only limit
 * is geometric and it is enforced at the point of splitting, not by a count:
 * see `MIN_PANE_PX` and `canSplit`.
 *
 * Everything here is PURE. No React, no DOM, no xterm. That is deliberate: the
 * layout maths is the part most likely to be subtly wrong, and pure functions
 * can be tested without launching Electron.
 */

export type SplitDir = 'row' | 'col'

export interface Leaf {
  kind: 'leaf'
  id: string
  /** Which shell this pane asked for. Inherited from the pane it split from. */
  shellId?: string
  /**
   * What this pane HOSTS. Absent means a terminal.
   *
   * A chat pane is a pane: it splits, zooms, closes and takes focus exactly as
   * a terminal does, and the layout does not care what is inside it. This one
   * field is the whole difference, which is the point — a fixed chat panel
   * would have needed its own focus, its own sizing and its own close.
   */
  content?: 'terminal' | 'chat'
}

export interface Split {
  kind: 'split'
  dir: SplitDir
  children: [PaneNode, PaneNode]
  /** Fractions of the parent along `dir`. Always sums to 1. */
  sizes: [number, number]
}

export type PaneNode = Leaf | Split

/**
 * THE SMALLEST TERMINAL THAT IS STILL A TERMINAL.
 *
 * 1366px is the whole screen and the layout already spends some of it on
 * chrome, so eight equal columns would be roughly 170px each — about 24
 * columns of text at the default size. `npm ERR!` wraps at that width; a stack
 * trace is unreadable; and `ls -la` becomes a column of fragments.
 *
 * 220px is about 30 columns, which is the point below which I would rather the
 * split were refused than have him discover it by reading a wrapped error.
 */
export const MIN_PANE_PX = 220

/**
 * TWO PANES PER TAB. HIS RULING, AFTER SEEING SEVEN.
 *
 * The tree below is unchanged and still supports arbitrary depth — a cap is a
 * POLICY, not a structural change, so raising it is this one number.
 *
 * Why it changed: seven panes filled 1366x768 with terminals of roughly
 * 450x290, and his reaction was "This is not it. It will be hard to read any
 * logs." He was right, and the answer was not smaller panes — it was that
 * "another window by the side" and "the rest in the same window" describe TABS.
 * Panes are for two things side by side; tabs are for many terminals.
 *
 * At two on 1366px each pane is about 683px, which is roughly 95 columns —
 * wider than the 80 that almost everything is written for.
 */
/**
 * ── THE TWO-PANE CAP IS LIFTED ────────────────────────────────────────────
 *
 * This was 2, from his own ruling after seven panes on 1366x768 gave terminals
 * of roughly 450x290 and he said "This is not it. It will be hard to read any
 * logs." That reading was right at the time.
 *
 * HE HAS OVERRIDDEN IT. He now wants Windows Terminal's splitting, and the cap
 * is gone rather than raised — there is no number that is correct for every
 * window size, which is exactly why the GEOMETRIC guard below is the right
 * gate and a count never was.
 *
 * NOTHING IS UNGUARDED. `canSplit` still refuses a split that would leave
 * either half under MIN_PANE_PX (horizontally) or MIN_PANE_PX_V (vertically),
 * and it still says so with the measured numbers. On 1366px that permits four
 * columns and stops at five, which is the same protection the cap was reaching
 * for, applied to the pane he is actually splitting instead of to a total.
 *
 * The constant is kept, exported and unused by the cap so the reasoning above
 * has somewhere to live. It is the ceiling of last resort: a runaway loop
 * cannot fill the tree, but nothing he does by hand will ever reach it.
 */
export const MAX_PANES_PER_TAB = 64

/** Same reasoning vertically: below this there is no room for output at all. */
export const MIN_PANE_PX_V = 90

let nextId = 0

export function newLeaf(shellId?: string, content?: 'terminal' | 'chat'): Leaf {
  nextId += 1
  return {
    kind: 'leaf',
    id: `pane-${nextId}`,
    ...(shellId ? { shellId } : {}),
    ...(content && content !== 'terminal' ? { content } : {}),
  }
}

/** Reset between tests so ids are predictable. Never called by the app. */
export function __resetIds(): void {
  nextId = 0
}

export function leaves(node: PaneNode): Leaf[] {
  return node.kind === 'leaf' ? [node] : [...leaves(node.children[0]), ...leaves(node.children[1])]
}

export function countLeaves(node: PaneNode): number {
  return node.kind === 'leaf' ? 1 : countLeaves(node.children[0]) + countLeaves(node.children[1])
}

export function findLeaf(node: PaneNode, id: string): Leaf | null {
  if (node.kind === 'leaf') return node.id === id ? node : null
  return findLeaf(node.children[0], id) ?? findLeaf(node.children[1], id)
}

/** Depth of the deepest leaf, for the report. A single pane is depth 0. */
export function depth(node: PaneNode): number {
  return node.kind === 'leaf' ? 0 : 1 + Math.max(depth(node.children[0]), depth(node.children[1]))
}

// ─────────────────────────────────────────────────────────────────────────────
// Geometry
// ─────────────────────────────────────────────────────────────────────────────

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface PlacedLeaf extends Rect {
  id: string
}

/**
 * Where every leaf actually sits, given the window's content box.
 *
 * Used for two things that both need real numbers rather than tree structure:
 * deciding whether a split would breach the minimum, and moving focus by
 * DIRECTION rather than by tree position — `Alt+Right` should go to the pane
 * physically to the right, which is not the same as the next node in the tree.
 */
export function layout(node: PaneNode, rect: Rect): PlacedLeaf[] {
  if (node.kind === 'leaf') return [{ id: node.id, ...rect }]
  const [a, b] = node.children
  const [fa] = node.sizes
  if (node.dir === 'row') {
    const wa = rect.w * fa
    return [
      ...layout(a, { ...rect, w: wa }),
      ...layout(b, { x: rect.x + wa, y: rect.y, w: rect.w - wa, h: rect.h }),
    ]
  }
  const ha = rect.h * fa
  return [
    ...layout(a, { ...rect, h: ha }),
    ...layout(b, { x: rect.x, y: rect.y + ha, w: rect.w, h: rect.h - ha }),
  ]
}

/**
 * Would splitting this pane leave either half too small?
 *
 * REFUSE WITH A REASON, rather than allowing a 90px terminal. The argument: a
 * terminal below a usable width is worse than no terminal, because it looks
 * like it works. He would split once more, see wrapped nonsense, and have no
 * way to tell whether the shell or the Console was at fault. A refusal that
 * names the number tells him exactly what happened and what to do about it —
 * close a pane, or widen the window.
 *
 * It is NOT a cap on pane count. Eight panes on a wide monitor are fine and the
 * code allows them; the same eight on 1366px are not, and only geometry can
 * tell the difference.
 */
export function canSplit(
  node: PaneNode,
  paneId: string,
  dir: SplitDir,
  rect: Rect,
): { ok: true } | { ok: false; reason: string } {
  const placed = layout(node, rect).find((p) => p.id === paneId)
  if (!placed) return { ok: false, reason: 'that pane no longer exists' }
  // THE CAP IS CHECKED FIRST, because its message is the useful one. Telling him
  // "this pane is too narrow" when the real answer is "open a tab" would send
  // him to resize a window that was never the problem.
  // THE COUNT IS NO LONGER THE GATE — GEOMETRY IS. This used to refuse at two
  // panes and send him to a new tab. He has lifted that; the only refusal left
  // is the one that can be justified in pixels, below.
  if (countLeaves(node) >= MAX_PANES_PER_TAB) {
    return {
      ok: false,
      reason: `this tab already has ${MAX_PANES_PER_TAB} panes, which is the structural ceiling`,
    }
  }
  if (dir === 'row') {
    const half = Math.floor(placed.w / 2)
    if (half < MIN_PANE_PX) {
      return {
        ok: false,
        reason:
          `splitting this ${Math.round(placed.w)}px pane would leave two of ${half}px, ` +
          `below the ${MIN_PANE_PX}px minimum — close a pane or widen the window`,
      }
    }
  } else {
    const half = Math.floor(placed.h / 2)
    if (half < MIN_PANE_PX_V) {
      return {
        ok: false,
        reason:
          `splitting this ${Math.round(placed.h)}px pane would leave two of ${half}px, ` +
          `below the ${MIN_PANE_PX_V}px minimum — close a pane or make the window taller`,
      }
    }
  }
  return { ok: true }
}

// ─────────────────────────────────────────────────────────────────────────────
// Mutation — always returns a NEW tree
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Split `paneId`, returning the new tree and the new pane's id.
 *
 * THE NEW PANE INHERITS THE SHELL of the pane it came from — his ruling. It is
 * the only behaviour that makes repeated splitting useful: splitting a Git Bash
 * pane to run a second git command should not drop him into PowerShell.
 */
export function splitPane(
  node: PaneNode,
  paneId: string,
  dir: SplitDir,
): { tree: PaneNode; newId: string } | null {
  if (node.kind === 'leaf') {
    if (node.id !== paneId) return null
    // SPLITTING A CHAT PANE GIVES A TERMINAL. The shell inheritance rule is
    // about continuing the same job; a second Tessa beside the first would be
    // two views of one conversation taking the space a terminal needs, and he
    // has exactly one of her.
    const fresh = node.content === 'chat' ? newLeaf() : newLeaf(node.shellId)
    return {
      tree: { kind: 'split', dir, children: [node, fresh], sizes: [0.5, 0.5] },
      newId: fresh.id,
    }
  }
  for (const i of [0, 1] as const) {
    const r = splitPane(node.children[i], paneId, dir)
    if (r) {
      const children: [PaneNode, PaneNode] = [...node.children] as [PaneNode, PaneNode]
      children[i] = r.tree
      return { tree: { ...node, children }, newId: r.newId }
    }
  }
  return null
}

/**
 * Close a pane. Its container COLLAPSES and the sibling takes the whole space.
 *
 * Returns `null` when the pane closed was the last one — the caller decides
 * what that means, and `App` opens a fresh terminal rather than leaving an
 * empty window. See the note there.
 */
export function closePane(node: PaneNode, paneId: string): PaneNode | null {
  if (node.kind === 'leaf') return node.id === paneId ? null : node
  const [a, b] = node.children
  const na = closePane(a, paneId)
  const nb = closePane(b, paneId)
  if (na === null) return nb
  if (nb === null) return na
  if (na === a && nb === b) return node
  return { ...node, children: [na, nb] }
}

/** Resize the split that directly contains `paneId`, clamped away from zero. */
export function setSplitSize(node: PaneNode, paneId: string, fraction: number): PaneNode {
  if (node.kind === 'leaf') return node
  const f = Math.min(0.9, Math.max(0.1, fraction))
  if (node.children[0].kind === 'leaf' && node.children[0].id === paneId) {
    return { ...node, sizes: [f, 1 - f] }
  }
  return {
    ...node,
    children: [
      setSplitSize(node.children[0], paneId, fraction),
      setSplitSize(node.children[1], paneId, fraction),
    ],
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Focus movement
// ─────────────────────────────────────────────────────────────────────────────

export type Direction = 'left' | 'right' | 'up' | 'down'

/**
 * The pane physically in `dir` from the focused one.
 *
 * BY GEOMETRY, NOT BY TREE POSITION. In a tree where the right half is split
 * vertically, `Alt+Right` from the left pane must land on whichever of the two
 * right-hand panes is level with it — the tree alone cannot answer that, and
 * "next sibling" would jump somewhere he did not point at.
 *
 * The rule: among panes strictly beyond the current edge in `dir`, take the one
 * whose leading edge is nearest, breaking ties by the smallest gap on the
 * perpendicular axis (measured centre to centre). That is the pane a person
 * means when they press the arrow.
 */
export function neighbour(
  node: PaneNode,
  paneId: string,
  dir: Direction,
  rect: Rect,
): string | null {
  const placed = layout(node, rect)
  const me = placed.find((p) => p.id === paneId)
  if (!me) return null
  const myCx = me.x + me.w / 2
  const myCy = me.y + me.h / 2

  const candidates = placed.filter((p) => {
    if (p.id === paneId) return false
    switch (dir) {
      case 'left':
        return p.x + p.w <= me.x + 1
      case 'right':
        return p.x >= me.x + me.w - 1
      case 'up':
        return p.y + p.h <= me.y + 1
      case 'down':
        return p.y >= me.y + me.h - 1
    }
  })
  if (!candidates.length) return null

  const horizontal = dir === 'left' || dir === 'right'
  candidates.sort((p, q) => {
    const pd = horizontal
      ? dir === 'right'
        ? p.x - me.x
        : me.x - (p.x + p.w)
      : dir === 'down'
        ? p.y - me.y
        : me.y - (p.y + p.h)
    const qd = horizontal
      ? dir === 'right'
        ? q.x - me.x
        : me.x - (q.x + q.w)
      : dir === 'down'
        ? q.y - me.y
        : me.y - (q.y + q.h)
    if (pd !== qd) return pd - qd
    const pp = horizontal ? Math.abs(p.y + p.h / 2 - myCy) : Math.abs(p.x + p.w / 2 - myCx)
    const qp = horizontal ? Math.abs(q.y + q.h / 2 - myCy) : Math.abs(q.x + q.w / 2 - myCx)
    return pp - qp
  })
  return candidates[0]?.id ?? null
}

/** A readable one-line rendering of the tree, for the report and the log. */
export function describe(node: PaneNode, shellOf?: (id: string) => string | undefined): string {
  if (node.kind === 'leaf') {
    const sh = shellOf?.(node.id) ?? node.shellId
    return sh ? `${node.id}(${sh})` : node.id
  }
  const op = node.dir === 'row' ? ' | ' : ' / '
  return `[${describe(node.children[0], shellOf)}${op}${describe(node.children[1], shellOf)}]`
}

export interface Divider extends Rect {
  /** A leaf inside the FIRST child, which `setSplitSize` uses to address this split. */
  anchorId: string
  dir: SplitDir
}

/**
 * Where the draggable dividers sit, in the same coordinate space as `layout`.
 *
 * Computed separately from the panes because the panes are positioned
 * ABSOLUTELY rather than nested — see the note in App on why. A divider is a
 * property of a split node, so it cannot come out of `layout`, which only
 * reports leaves.
 */
export function dividers(node: PaneNode, rect: Rect, thickness = 4): Divider[] {
  if (node.kind === 'leaf') return []
  const [a, b] = node.children
  const [fa] = node.sizes
  const half = thickness / 2
  const anchorId = leaves(a)[0]?.id ?? ''
  if (node.dir === 'row') {
    const wa = rect.w * fa
    return [
      { x: rect.x + wa - half, y: rect.y, w: thickness, h: rect.h, anchorId, dir: 'row' },
      ...dividers(a, { ...rect, w: wa }, thickness),
      ...dividers(b, { x: rect.x + wa, y: rect.y, w: rect.w - wa, h: rect.h }, thickness),
    ]
  }
  const ha = rect.h * fa
  return [
    { x: rect.x, y: rect.y + ha - half, w: rect.w, h: thickness, anchorId, dir: 'col' },
    ...dividers(a, { ...rect, h: ha }, thickness),
    ...dividers(b, { x: rect.x, y: rect.y + ha, w: rect.w, h: rect.h - ha }, thickness),
  ]
}

/** The split node that owns `anchorId` as its first child's leaf, if any. */
export function splitRectOf(node: PaneNode, anchorId: string, rect: Rect): Rect | null {
  if (node.kind === 'leaf') return null
  if ((leaves(node.children[0])[0]?.id ?? '') === anchorId) return rect
  const [a, b] = node.children
  const [fa] = node.sizes
  if (node.dir === 'row') {
    const wa = rect.w * fa
    return (
      splitRectOf(a, anchorId, { ...rect, w: wa }) ??
      splitRectOf(b, anchorId, { x: rect.x + wa, y: rect.y, w: rect.w - wa, h: rect.h })
    )
  }
  const ha = rect.h * fa
  return (
    splitRectOf(a, anchorId, { ...rect, h: ha }) ??
    splitRectOf(b, anchorId, { x: rect.x, y: rect.y + ha, w: rect.w, h: rect.h - ha })
  )
}
