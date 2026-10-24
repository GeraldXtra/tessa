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
  | 'closeTab'
  | 'toggleSplit'
  | 'newPowerShell'
  | 'newGitBash'
  | 'newCmd'
  | 'newPwsh'
  | 'newWsl'
  | 'goToTab1'
  | 'goToTab2'
  | 'goToTab3'
  | 'goToTab4'
  | 'goToTab5'
  | 'goToTab6'
  | 'goToTab7'
  | 'goToTab8'
  | 'goToTab9'
  | 'prevCommand'
  | 'nextCommand'
  | 'palette'
  | 'settings'

export const RETIRED: ReadonlySet<string> = new Set(['zoomPane', 'toggleTree', 'passThrough'])

export const RESERVED_CHORDS: ReadonlySet<string> = new Set([
  'ctrl+c',
  'ctrl+l',
  'ctrl+k',
  'up',
  'down',
  'left',
  'right',
  'tab',
  'shift+tab',
])

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
    case 'Comma':
      return ','
    case 'Period':
      return '.'
    case 'Insert':
      return 'insert'
    case 'Delete':
      return 'delete'
    case 'Home':
      return 'home'
    case 'End':
      return 'end'
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
    case 'NumpadEnter':
      return 'enter'
    case 'Tab':
      return 'tab'
    case 'Backslash':
      return '\\'
    case 'Slash':
      return '/'
    default:
      return (code || key || '').toLowerCase()
  }
}

export function chordOf(e: KeyboardEvent): string {
  const parts: string[] = []
  if (e.ctrlKey) parts.push('ctrl')
  if (e.altKey) parts.push('alt')
  if (e.shiftKey) parts.push('shift')
  if (e.metaKey) parts.push('meta')
  parts.push(keyNameFromCode(e.code, e.key))
  return parts.join('+')
}

export function sanitiseKeymap(km: Record<string, string>): Record<string, KeyAction> {
  const out: Record<string, KeyAction> = {}
  for (const [chord, action] of Object.entries(km)) {
    if (RESERVED_CHORDS.has(chord)) continue
    out[chord] = action as KeyAction
  }
  return out
}

const KEY_LABEL: Record<string, string> = {
  ctrl: 'CTRL',
  alt: 'ALT',
  shift: 'SHIFT',
  meta: 'WIN',
  '=': '=',
  '-': 'MINUS',
  ',': 'COMMA',
  up: 'UP',
  down: 'DOWN',
  left: 'LEFT',
  right: 'RIGHT',
  enter: 'ENTER',
  escape: 'ESC',
  end: 'END',
  tab: 'TAB',
  insert: 'INSERT',
}

export function chordLabel(chord: string): string {
  return chord
    .split('+')
    .map((p) => KEY_LABEL[p] ?? p.toUpperCase())
    .join(' ')
}

export function keyFor(keymap: Record<string, KeyAction>, design: Record<string, string>, action: KeyAction): string {
  const bound = Object.entries(keymap).filter(([, a]) => a === action).map(([c]) => c)
  if (!bound.length) return ''
  const chosen = bound.find((c) => design[c] === action) ?? bound[0]!
  return chordLabel(chosen)
}
