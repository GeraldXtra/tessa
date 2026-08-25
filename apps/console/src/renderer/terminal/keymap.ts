/**
 * apps/console/src/renderer/terminal/keymap.ts — which chord means what.
 *
 * The bindings live in `%LOCALAPPDATA%\Tessa\console-settings.json` and are read
 * in MAIN; they are used HERE, in the renderer, because only the terminal knows
 * whether there is a selection. This module is the small amount of logic that
 * sits between the two.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHY THE CHORD IS BUILT FROM `event.code` AND NOT `event.key`
 *
 * `key` is what the character WOULD be after the keyboard layout and modifiers
 * are applied. With Shift held, `Ctrl+Shift+C` arrives with `key === 'C'`
 * (capital), and on some layouts `Ctrl+-` arrives as an entirely different
 * character. Matching on `key` therefore needs a table of exceptions per layout.
 *
 * `code` is the physical key — `KeyC`, `Minus`, `Equal`, `Digit0` — and does not
 * move. It is normalised down to the obvious name so the settings file stays
 * readable: a person writes `ctrl+shift+c`, not `ctrl+shift+KeyC`.
 */

export type KeyAction =
  | 'copy'
  | 'paste'
  | 'selectAll'
  | 'clearSelection'
  | 'find'
  | 'fontIncrease'
  | 'fontDecrease'
  | 'fontReset'
  | 'splitRight'
  | 'splitDown'
  | 'closePane'
  | 'focusLeft'
  | 'focusRight'
  | 'focusUp'
  | 'focusDown'
  | 'newTab'
  | 'nextTab'
  | 'prevTab'
  | 'zoomPane'
  | 'toggleTree'
  | 'passThrough'

/** `KeyC` -> `c`, `Digit0` -> `0`, `Minus` -> `-`. Unknown codes pass through. */
export function keyNameFromCode(code: string, key: string): string {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase()
  if (/^Digit[0-9]$/.test(code)) return code.slice(5)
  if (/^Numpad[0-9]$/.test(code)) return code.slice(6)
  switch (code) {
    case 'Minus':
    case 'NumpadSubtract':
      return '-'
    case 'Equal':
    case 'NumpadAdd':
      return '='
    case 'Insert':
      return 'insert'
    case 'Delete':
      return 'delete'
    case 'Home':
      return 'home'
    case 'End':
      return 'end'
    // Arrows get SHORT names, so he writes `alt+left` in the settings file
    // rather than `alt+arrowleft`. Their `code` is ArrowLeft etc., which would
    // otherwise fall through to the lower-cased default.
    case 'ArrowLeft':
      return 'left'
    case 'ArrowRight':
      return 'right'
    case 'ArrowUp':
      return 'up'
    case 'ArrowDown':
      return 'down'
    case 'Escape':
      return 'escape'
    case 'Space':
      return 'space'
    case 'Enter':
      return 'enter'
    case 'Tab':
      return 'tab'
    case 'Backslash':
      return '\\'
    case 'Slash':
      return '/'
    default:
      // F-keys and anything else: fall back to the printable key, lower-cased.
      return (code || key || '').toLowerCase()
  }
}

/** The canonical chord string for an event: modifiers in a fixed order. */
export function chordOf(e: KeyboardEvent): string {
  const parts: string[] = []
  if (e.ctrlKey) parts.push('ctrl')
  if (e.altKey) parts.push('alt')
  if (e.shiftKey) parts.push('shift')
  if (e.metaKey) parts.push('meta')
  parts.push(keyNameFromCode(e.code, e.key))
  return parts.join('+')
}

/**
 * Built-in defaults, used until main answers and if the file is unreadable.
 *
 * KEPT IN STEP WITH `main/settings.ts`. They are duplicated rather than shared
 * because main and the renderer are separate bundles under `sandbox: true` and
 * the renderer cannot read the file itself — so the renderer needs a usable
 * keymap in the milliseconds before the IPC round trip returns, or the very
 * first keystroke after launch would be unbound.
 */
export const FALLBACK_KEYMAP: Record<string, KeyAction> = {
  'ctrl+shift+c': 'copy',
  'ctrl+insert': 'copy',
  'ctrl+shift+v': 'paste',
  'ctrl+v': 'paste',
  'shift+insert': 'paste',
  'ctrl+a': 'selectAll',
  'ctrl+shift+a': 'selectAll',
  'ctrl+=': 'fontIncrease',
  'ctrl+shift+=': 'fontIncrease',
  'ctrl+-': 'fontDecrease',
  'ctrl+0': 'fontReset',
  'ctrl+shift+d': 'splitRight',
  'ctrl+shift+e': 'splitDown',
  'ctrl+shift+w': 'closePane',
  'alt+left': 'focusLeft',
  'alt+right': 'focusRight',
  'alt+up': 'focusUp',
  'alt+down': 'focusDown',
  'ctrl+shift+t': 'newTab',
  'ctrl+tab': 'nextTab',
  'ctrl+shift+tab': 'prevTab',
  'ctrl+shift+z': 'zoomPane',
  'ctrl+shift+b': 'toggleTree',
}

/**
 * `ctrl+c` IS DELIBERATELY ABSENT FROM EVERY KEYMAP IN THIS FILE.
 *
 * It is not "unbound by oversight" — it is the one chord that must reach the
 * shell untouched, because it is how he stops a hung command. A terminal that
 * loses SIGINT has lost the thing that makes it a terminal, and no amount of
 * clipboard convenience is worth it. His own ruling, and the right one.
 *
 * This is asserted rather than assumed: a keymap that binds it is rejected at
 * load with a named complaint, so it cannot be broken by editing the file.
 */
export const RESERVED_CHORDS = new Set(['ctrl+c'])

export function sanitiseKeymap(
  km: Record<string, string>,
): { keymap: Record<string, KeyAction>; rejected: string[] } {
  const out: Record<string, KeyAction> = {}
  const rejected: string[] = []
  for (const [chord, action] of Object.entries(km)) {
    if (RESERVED_CHORDS.has(chord)) {
      rejected.push(`${chord} is reserved for interrupt (SIGINT) and cannot be rebound`)
      continue
    }
    out[chord] = action as KeyAction
  }
  return { keymap: out, rejected }
}
