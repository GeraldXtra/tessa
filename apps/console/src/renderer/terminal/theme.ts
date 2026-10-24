import type { ITheme } from '@xterm/xterm'
import tokens from '@tessa/tokens'

export function tok(name: string): string {
  const entry = (tokens.color as Record<string, { value: string } | undefined>)[name]
  if (!entry) throw new Error(`token color.${name} missing — regenerate with: npm run tokens`)
  return entry.value
}

function rgbOf(value: string): { r: number; g: number; b: number; a: number } {
  const hex = value.match(/^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/)
  if (hex) return { r: parseInt(hex[1]!, 16), g: parseInt(hex[2]!, 16), b: parseInt(hex[3]!, 16), a: 1 }
  const m = value.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/)
  if (m) return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]), a: m[4] === undefined ? 1 : Number(m[4]) }
  throw new Error(`token value ${value} is not a colour this Console can composite`)
}

function hex2(n: number): string {
  return Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0')
}

export function composite(over: string, under: string): string {
  const o = rgbOf(over)
  const u = rgbOf(under)
  const a = o.a
  return `#${hex2(o.r * a + u.r * (1 - a))}${hex2(o.g * a + u.g * (1 - a))}${hex2(o.b * a + u.b * (1 - a))}`
}

const ANSI = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'] as const

function palette(prefix: 'std' | 'mono'): Partial<ITheme> {
  const out: Record<string, string> = {}
  for (const n of ANSI) {
    out[n] = tok(`console-${prefix}-${n}`)
    out[`bright${n[0]!.toUpperCase()}${n.slice(1)}`] = tok(`console-${prefix}-bright-${n}`)
  }
  return out as Partial<ITheme>
}

export function terminalTheme(output: 'standard' | 'mono'): ITheme {
  return {
    background: 'rgba(0,0,0,0)',
    foreground: tok('console-t2'),
    cursor: tok('console-t1'),
    cursorAccent: tok('console-bg'),
    selectionBackground: tok('console-select'),
    selectionInactiveBackground: tok('console-select'),
    scrollbarSliderBackground: tok('console-line2'),
    scrollbarSliderHoverBackground: tok('console-t4'),
    scrollbarSliderActiveBackground: tok('console-t3'),
    ...palette(output === 'mono' ? 'mono' : 'std'),
  }
}

export const MATCH_BG = (): string => composite(tok('console-match'), tok('console-bg'))
export const CURRENT_BG = (): string => tok('console-t1')
export const CURRENT_FG = (): string => tok('console-bg')
export const INPUT_FG = (): string => tok('console-t1')

export function sgrTrue(name: string): string {
  const c = rgbOf(tok(name))
  return `\x1b[38;2;${c.r};${c.g};${c.b}m`
}

export const TERMINAL_FONT = '"Cascadia Mono", "Cascadia Code", Consolas, "Lucida Console", monospace'
const LINE_HEIGHT = 1.55

export function cellLineHeight(fontSize: number): number {
  const probe = document.createElement('span')
  probe.textContent = 'W'
  probe.style.cssText = `position:absolute;visibility:hidden;display:inline-block;white-space:pre;line-height:normal;font-family:${TERMINAL_FONT};font-size:${fontSize}px`
  document.body.append(probe)
  const h = probe.getBoundingClientRect().height
  probe.remove()
  return h > 0 ? (fontSize * LINE_HEIGHT) / h : LINE_HEIGHT
}
