import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

import { isShellId, type ShellId } from './shells.ts'

export const KEY_ACTIONS = [
  'copy',
  'paste',
  'selectAll',
  'clearSelection',
  'find',
  'fontIncrease',
  'fontDecrease',
  'fontReset',
  'splitRight',
  'splitDown',
  'closePane',
  'focusLeft',
  'focusRight',
  'focusUp',
  'focusDown',
  'newTab',
  'nextTab',
  'prevTab',
  'zoomPane',
  'toggleTree',
  'passThrough',
  'closeTab',
  'toggleSplit',
  'newPowerShell',
  'newGitBash',
  'newCmd',
  'newPwsh',
  'newWsl',
  'goToTab1',
  'goToTab2',
  'goToTab3',
  'goToTab4',
  'goToTab5',
  'goToTab6',
  'goToTab7',
  'goToTab8',
  'goToTab9',
  'prevCommand',
  'nextCommand',
  'palette',
  'settings',
] as const
export type KeyAction = (typeof KEY_ACTIONS)[number]

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

export type OutputColours = 'standard' | 'mono'
export type PromptMode = 'tessa' | 'shell'
export type CursorStyle = 'block' | 'bar' | 'underline'

export interface ConsoleSettings {
  defaultShell: ShellId
  keymap: Record<string, KeyAction>
  rightClickPastes: boolean
  copyOnSelect: boolean
  scrollback: number
  fontSize: number
  outputColours: OutputColours
  prompt: PromptMode
  cursorStyle: CursorStyle
  pasteGuard: boolean
}

export const DESIGN_KEYMAP: Record<string, KeyAction> = {
  'ctrl+shift+t': 'newTab',
  'ctrl+shift+1': 'newPowerShell',
  'ctrl+shift+2': 'newGitBash',
  'ctrl+shift+3': 'newCmd',
  'ctrl+shift+4': 'newPwsh',
  'ctrl+shift+5': 'newWsl',
  'ctrl+shift+w': 'closeTab',
  'ctrl+tab': 'nextTab',
  'alt+1': 'goToTab1',
  'alt+2': 'goToTab2',
  'alt+3': 'goToTab3',
  'alt+4': 'goToTab4',
  'alt+5': 'goToTab5',
  'alt+6': 'goToTab6',
  'alt+7': 'goToTab7',
  'alt+8': 'goToTab8',
  'alt+9': 'goToTab9',
  'ctrl+shift+d': 'toggleSplit',
  'alt+left': 'focusLeft',
  'alt+right': 'focusRight',
  'alt+up': 'focusUp',
  'alt+down': 'focusDown',
  'ctrl+shift+f': 'find',
  'ctrl+up': 'prevCommand',
  'ctrl+down': 'nextCommand',
  'ctrl+shift+p': 'palette',
  'ctrl+shift+c': 'copy',
  'ctrl+shift+v': 'paste',
  'ctrl+=': 'fontIncrease',
  'ctrl+-': 'fontDecrease',
  'ctrl+0': 'fontReset',
  'ctrl+,': 'settings',
}

export const DEFAULT_SETTINGS: ConsoleSettings = {
  defaultShell: 'powershell',
  keymap: {
    'ctrl+insert': 'copy',
    'ctrl+v': 'paste',
    'shift+insert': 'paste',
    'ctrl+a': 'selectAll',
    'ctrl+shift+a': 'selectAll',
    'ctrl+shift+=': 'fontIncrease',
    'ctrl+shift+tab': 'prevTab',
    ...DESIGN_KEYMAP,
  },
  rightClickPastes: true,
  copyOnSelect: false,
  scrollback: 10_000,
  fontSize: 14,
  outputColours: 'standard',
  prompt: 'tessa',
  cursorStyle: 'block',
  pasteGuard: true,
}

export const SETTINGS_VERSION = 5
export const FONT_MIN = 11
export const FONT_MAX = 20
export const FONT_DEFAULT = 14

export function settingsPath(): string {
  const base = process.env['LOCALAPPDATA'] ?? process.env['APPDATA'] ?? ''
  return join(base, 'Tessa', 'console-settings.json')
}

export interface LoadedSettings {
  settings: ConsoleSettings
  migrated: string[]
  replaced: string[]
  problems: string[]
  path: string
  existed: boolean
}

export function normaliseChord(chord: string): string {
  const parts = String(chord)
    .toLowerCase()
    .split('+')
    .map((p) => p.trim())
  const mods = new Set<string>()
  let key = ''
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i] ?? ''
    if (p === '' && i === parts.length - 1 && parts.length > 1) {
      key = '+'
      continue
    }
    if (p === 'ctrl' || p === 'control') mods.add('ctrl')
    else if (p === 'alt') mods.add('alt')
    else if (p === 'shift') mods.add('shift')
    else if (p === 'meta' || p === 'win' || p === 'cmd') mods.add('meta')
    else if (p) key = p
  }
  if (!key) return ''
  const order = ['ctrl', 'alt', 'shift', 'meta'].filter((m) => mods.has(m))
  return [...order, key].join('+')
}

function writeAtomic(path: string, obj: unknown): void {
  mkdirSync(dirname(path), { recursive: true })
  const tmp = `${path}.tmp`
  writeFileSync(tmp, JSON.stringify(obj, null, 2) + '\n', 'utf8')
  renameSync(tmp, path)
}

function freshDefaults(): ConsoleSettings {
  return { ...DEFAULT_SETTINGS, keymap: { ...DEFAULT_SETTINGS.keymap } }
}

export function loadSettings(): LoadedSettings {
  const path = settingsPath()
  const out = freshDefaults()
  const problems: string[] = []
  if (!existsSync(path)) return { settings: out, problems, path, existed: false, migrated: [], replaced: [] }

  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(path, 'utf8'))
  } catch (err) {
    problems.push(`not valid JSON (${(err as Error).message}) - using defaults, file left untouched`)
    return { settings: out, problems, path, existed: true, migrated: [], replaced: [] }
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    problems.push('top level is not an object - using defaults, file left untouched')
    return { settings: out, problems, path, existed: true, migrated: [], replaced: [] }
  }
  const o = raw as Record<string, unknown>

  if (o['defaultShell'] !== undefined) {
    if (isShellId(o['defaultShell'])) out.defaultShell = o['defaultShell']
    else problems.push(`defaultShell "${String(o['defaultShell'])}" is not a known shell - kept ${out.defaultShell}`)
  }
  for (const k of ['rightClickPastes', 'copyOnSelect', 'pasteGuard'] as const) {
    if (o[k] === undefined) continue
    if (typeof o[k] === 'boolean') out[k] = o[k] as boolean
    else problems.push(`${k} must be true or false - kept ${String(out[k])}`)
  }
  for (const [k, lo, hi] of [
    ['scrollback', 200, 200_000],
    ['fontSize', 6, 48],
  ] as const) {
    if (o[k] === undefined) continue
    const n = Number(o[k])
    if (Number.isFinite(n) && n >= lo && n <= hi) out[k] = Math.round(n)
    else problems.push(`${k} must be a number between ${lo} and ${hi} - kept ${out[k]}`)
  }
  const choice = <T extends string>(k: string, allowed: readonly T[], set: (v: T) => void): void => {
    if (o[k] === undefined) return
    if ((allowed as readonly unknown[]).includes(o[k])) set(o[k] as T)
    else problems.push(`${k} must be one of ${allowed.join(', ')} - kept the default`)
  }
  choice('outputColours', ['standard', 'mono'] as const, (v) => (out.outputColours = v))
  choice('prompt', ['tessa', 'shell'] as const, (v) => (out.prompt = v))
  choice('cursorStyle', ['block', 'bar', 'underline'] as const, (v) => (out.cursorStyle = v))

  if (o['keymap'] !== undefined) {
    if (typeof o['keymap'] !== 'object' || o['keymap'] === null || Array.isArray(o['keymap'])) {
      problems.push('keymap must be an object of "chord": "action" - kept the defaults')
    } else {
      const km: Record<string, KeyAction> = {}
      for (const [chord, action] of Object.entries(o['keymap'] as Record<string, unknown>)) {
        const norm = normaliseChord(chord)
        if (!norm) {
          problems.push(`keymap key "${chord}" is not a readable chord - ignored`)
          continue
        }
        if (RESERVED_CHORDS.has(norm)) {
          problems.push(`keymap["${chord}"] always goes to the shell and cannot be bound - ignored`)
          continue
        }
        const a = String(action)
        if (!(KEY_ACTIONS as readonly string[]).includes(a)) {
          problems.push(`keymap["${chord}"] = "${a}" is not a known action - ignored`)
          continue
        }
        km[norm] = a as KeyAction
      }
      out.keymap = km
    }
  }

  const fileVersion = Number(o['version'] ?? 1)
  const migrated: string[] = []
  const replaced: string[] = []
  if (fileVersion < SETTINGS_VERSION && o['keymap'] !== undefined) {
    const before = { ...out.keymap }
    for (const [chord, action] of Object.entries(DESIGN_KEYMAP)) {
      const had = before[chord]
      if (had === action) continue
      if (had !== undefined) {
        replaced.push(`${chord}: ${had} -> ${action}`)
      } else {
        migrated.push(`${chord} -> ${action}`)
      }
      out.keymap[chord] = action
    }
    if (migrated.length) problems.push(`settings upgraded to v${SETTINGS_VERSION}: added ${migrated.length} binding(s): ${migrated.join(', ')}`)
    if (replaced.length) problems.push(`settings upgraded to v${SETTINGS_VERSION}: the design took ${replaced.length} chord(s): ${replaced.join(', ')}`)
  }
  if (fileVersion < SETTINGS_VERSION) {
    try {
      const { version: _v, ...rest } = o
      void _v
      writeAtomic(path, {
        version: SETTINGS_VERSION,
        ...rest,
        defaultShell: out.defaultShell,
        keymap: out.keymap,
        rightClickPastes: out.rightClickPastes,
        copyOnSelect: out.copyOnSelect,
        scrollback: out.scrollback,
        fontSize: out.fontSize,
        outputColours: out.outputColours,
        prompt: out.prompt,
        cursorStyle: out.cursorStyle,
        pasteGuard: out.pasteGuard,
      })
    } catch (err) {
      problems.push(`could not write the upgraded settings file: ${(err as Error).message}`)
    }
  }
  return { settings: out, problems, path, existed: true, migrated, replaced }
}

export function ensureSettingsFile(): { wrote: boolean; path: string } {
  const path = settingsPath()
  if (existsSync(path)) return { wrote: false, path }
  try {
    writeAtomic(path, { version: SETTINGS_VERSION, ...DEFAULT_SETTINGS })
    return { wrote: true, path }
  } catch {
    return { wrote: false, path }
  }
}

export type SettingsPatch = Partial<Pick<ConsoleSettings, 'fontSize' | 'outputColours' | 'prompt' | 'cursorStyle' | 'pasteGuard'>>

export function saveSettingsPatch(patch: SettingsPatch): { ok: boolean; error?: string } {
  const path = settingsPath()
  const clean: Record<string, unknown> = {}
  if (patch.fontSize !== undefined) {
    const n = Math.round(Number(patch.fontSize))
    if (!Number.isFinite(n) || n < FONT_MIN || n > FONT_MAX) return { ok: false, error: 'fontSize out of range' }
    clean['fontSize'] = n
  }
  if (patch.outputColours !== undefined) {
    if (patch.outputColours !== 'standard' && patch.outputColours !== 'mono') return { ok: false, error: 'bad outputColours' }
    clean['outputColours'] = patch.outputColours
  }
  if (patch.prompt !== undefined) {
    if (patch.prompt !== 'tessa' && patch.prompt !== 'shell') return { ok: false, error: 'bad prompt' }
    clean['prompt'] = patch.prompt
  }
  if (patch.cursorStyle !== undefined) {
    if (!['block', 'bar', 'underline'].includes(patch.cursorStyle)) return { ok: false, error: 'bad cursorStyle' }
    clean['cursorStyle'] = patch.cursorStyle
  }
  if (patch.pasteGuard !== undefined) {
    if (typeof patch.pasteGuard !== 'boolean') return { ok: false, error: 'bad pasteGuard' }
    clean['pasteGuard'] = patch.pasteGuard
  }
  try {
    let current: Record<string, unknown> = {}
    if (existsSync(path)) {
      const parsed = JSON.parse(readFileSync(path, 'utf8')) as unknown
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) current = parsed as Record<string, unknown>
      else return { ok: false, error: 'settings file is not an object - not overwritten' }
    } else {
      current = { version: SETTINGS_VERSION, ...DEFAULT_SETTINGS }
    }
    writeAtomic(path, { ...current, ...clean })
    return { ok: true }
  } catch (err) {
    return { ok: false, error: (err as Error).message }
  }
}
