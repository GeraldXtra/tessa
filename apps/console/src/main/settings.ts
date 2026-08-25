/**
 * apps/console/src/main/settings.ts — the Console's own settings, editable.
 *
 * `%LOCALAPPDATA%\Tessa\console-settings.json`
 *
 * WHY THIS EXISTS NOW RATHER THAN LATER. Gerald has already changed his mind
 * once about Ctrl+C versus Ctrl+Shift+C, and he will again once he has lived
 * with the bindings for a week. A keymap he can edit costs almost nothing today
 * and saves an entire prompt later. The same file carries the default shell,
 * for the same reason.
 *
 * A MALFORMED FILE MUST NOT COST HIM HIS TERMINAL. Every failure — missing,
 * truncated, not JSON, wrong types, an unknown action name — falls back to the
 * built-in defaults and is REPORTED in the log. It is never fatal, and it is
 * never silent: a settings file that is quietly ignored is worse than one that
 * errors, because he edits it again and again and nothing changes.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

import { DEFAULT_SHELL, SHELL_IDS, type ShellId } from './shells.ts'

/** Actions the terminal knows how to perform. A binding to anything else is rejected. */
export const KEY_ACTIONS = [
  'copy',
  'paste',
  'selectAll',
  'clearSelection',
  'find',
  'fontIncrease',
  'fontDecrease',
  'fontReset',
  // ── PANES ───────────────────────────────────────────────────────────────
  // DEFAULTS, NOT CONSTANTS. They live in the same editable keymap as copy and
  // paste, so he can move them if Ctrl+Shift+D collides with something else.
  'splitRight',
  'splitDown',
  'closePane',
  'focusLeft',
  'focusRight',
  'focusUp',
  'focusDown',
  // ── TABS ────────────────────────────────────────────────────────────────
  'newTab',
  'nextTab',
  'prevTab',
  'zoomPane',
  // ── THE FILE TREE ───────────────────────────────────────────────────────
  'toggleTree',
  'passThrough',
] as const
export type KeyAction = (typeof KEY_ACTIONS)[number]

export interface ConsoleSettings {
  defaultShell: ShellId
  /** "ctrl+shift+c" -> "copy". Lower-case, modifiers in ctrl+alt+shift order. */
  keymap: Record<string, KeyAction>
  /** Paste on right-click. Copy-on-select is separate and off by default. */
  rightClickPastes: boolean
  copyOnSelect: boolean
  scrollback: number
  fontSize: number
}

/**
 * THE DEFAULTS, AND THE TWO RULINGS INSIDE THEM.
 *
 * Ctrl+C is NOT here, deliberately — it falls through to the shell as SIGINT.
 * That is his ruling and it is also the only correct default: interrupting a
 * hung command is the thing a terminal must never lose.
 *
 * Ctrl+A IS here as selectAll, which he asked for by name. It costs him
 * beginning-of-line inside the shell — but Home does that in both PSReadLine
 * and bash readline, so the loss has a direct substitute, and this file is
 * where he takes it back if he disagrees.
 */
export const DEFAULT_SETTINGS: ConsoleSettings = {
  defaultShell: DEFAULT_SHELL,
  keymap: {
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
  },
  rightClickPastes: true,
  copyOnSelect: false,
  scrollback: 8000,
  fontSize: 13,
}

/**
 * THE SETTINGS FILE HAS A VERSION, AND THIS IS WHY.
 *
 * The keymap REPLACES the defaults wholesale when present, so that he can
 * delete a binding he does not want and have it stay deleted. That rule is
 * right, and it has a consequence nobody notices until a feature is added:
 * a file written before panes existed contains no split bindings, so after
 * upgrading, Ctrl+Shift+D would do nothing on his machine and everything on a
 * fresh one. The feature would look broken for exactly the person who had been
 * using the Console longest.
 *
 * So: when the file's version is older than this, any default binding whose
 * ACTION appears nowhere in his keymap is added once, the file is rewritten
 * with the new version, and every addition is NAMED in the problems list. An
 * action he has deliberately bound elsewhere is left alone, and a binding he
 * deleted stays deleted — only genuinely new actions arrive.
 */
export const SETTINGS_VERSION = 4

export function settingsPath(): string {
  const base = process.env['LOCALAPPDATA'] ?? process.env['APPDATA'] ?? ''
  return join(base, 'Tessa', 'console-settings.json')
}

export interface LoadedSettings {
  settings: ConsoleSettings
  /** Bindings added by the version migration, for the log. */
  migrated: string[]
  /** Every complaint, in order. Empty when the file was clean or absent. */
  problems: string[]
  path: string
  existed: boolean
}

/** Never throws. Worst case it returns the defaults and a list of complaints. */
export function loadSettings(): LoadedSettings {
  const path = settingsPath()
  const out: ConsoleSettings = {
    ...DEFAULT_SETTINGS,
    keymap: { ...DEFAULT_SETTINGS.keymap },
  }
  const problems: string[] = []

  if (!existsSync(path)) return { settings: out, problems, path, existed: false, migrated: [] }

  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(path, 'utf8'))
  } catch (err) {
    problems.push(`not valid JSON (${(err as Error).message}) — using defaults`)
    return { settings: out, problems, path, existed: true, migrated: [] }
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    problems.push('top level is not an object — using defaults')
    return { settings: out, problems, path, existed: true, migrated: [] }
  }
  const o = raw as Record<string, unknown>

  if (o['defaultShell'] !== undefined) {
    const v = String(o['defaultShell'])
    if ((SHELL_IDS as readonly string[]).includes(v)) out.defaultShell = v as ShellId
    else problems.push(`defaultShell "${v}" is not one of ${SHELL_IDS.join(', ')} — kept ${out.defaultShell}`)
  }

  for (const [k, def] of [
    ['rightClickPastes', DEFAULT_SETTINGS.rightClickPastes],
    ['copyOnSelect', DEFAULT_SETTINGS.copyOnSelect],
  ] as const) {
    if (o[k] !== undefined) {
      if (typeof o[k] === 'boolean') (out as unknown as Record<string, unknown>)[k] = o[k]
      else problems.push(`${k} must be true or false — kept ${def}`)
    }
  }

  for (const [k, lo, hi] of [
    ['scrollback', 200, 200_000],
    ['fontSize', 6, 48],
  ] as const) {
    if (o[k] !== undefined) {
      const n = Number(o[k])
      if (Number.isFinite(n) && n >= lo && n <= hi) (out as unknown as Record<string, unknown>)[k] = Math.round(n)
      else problems.push(`${k} must be a number between ${lo} and ${hi} — kept ${(out as unknown as Record<string, unknown>)[k]}`)
    }
  }

  // `ctrl+c` CANNOT BE REBOUND, AND THE REFUSAL IS NAMED HERE TOO.
  //
  // The renderer's `sanitiseKeymap` already refuses it, and that is the pass
  // that actually protects interrupt. But main is where the PROBLEMS LIST is
  // built, and a refusal he never sees is a refusal he will not understand —
  // he would only notice that the binding he wrote does nothing. Enforcing it
  // in both places costs one line and means the complaint reaches him at load.
  //
  // Interrupt is the one key a terminal cannot lose: without it a hung command
  // cannot be stopped, and there is no obvious way back.
  // THE KEYMAP REPLACES THE DEFAULTS WHOLESALE when present, so he can REMOVE a
  // binding by leaving it out. Merging would make an unwanted default
  // impossible to get rid of, which is the commonest complaint about keymaps.
  if (o['keymap'] !== undefined) {
    if (typeof o['keymap'] !== 'object' || o['keymap'] === null || Array.isArray(o['keymap'])) {
      problems.push('keymap must be an object of "chord": "action" — kept the defaults')
    } else {
      const km: Record<string, KeyAction> = {}
      for (const [chord, action] of Object.entries(o['keymap'] as Record<string, unknown>)) {
        if (normaliseChord(String(chord)) === 'ctrl+c') {
          problems.push(
            `keymap["${chord}"] is reserved for interrupt (SIGINT) and cannot be rebound — ignored`,
          )
          continue
        }
        const a = String(action)
        if (!(KEY_ACTIONS as readonly string[]).includes(a)) {
          problems.push(`keymap["${chord}"] = "${a}" is not a known action — ignored`)
          continue
        }
        const norm = normaliseChord(chord)
        if (!norm) {
          problems.push(`keymap key "${chord}" is not a readable chord — ignored`)
          continue
        }
        km[norm] = a as KeyAction
      }
      out.keymap = km
      if (Object.keys(km).length === 0) {
        problems.push('keymap ended up empty — every binding was rejected; the terminal has no shortcuts')
      }
    }
  }

  // ── VERSION MIGRATION ─────────────────────────────────────────────────────
  const fileVersion = Number(o['version'] ?? 1)
  const migrated: string[] = []
  if (fileVersion < SETTINGS_VERSION && o['keymap'] !== undefined) {
    const boundActions = new Set(Object.values(out.keymap))
    const takenChords = new Set(Object.keys(out.keymap))
    for (const [chord, action] of Object.entries(DEFAULT_SETTINGS.keymap)) {
      if (boundActions.has(action)) continue
      if (takenChords.has(chord)) continue
      out.keymap[chord] = action
      takenChords.add(chord)
      migrated.push(`${chord} -> ${action}`)
    }
    if (migrated.length) {
      problems.push(
        `settings upgraded to v${SETTINGS_VERSION}: added ${migrated.length} new binding(s) ` +
          `(${migrated.join(', ')}) — delete any you do not want and they will stay deleted`,
      )
    }
  }
  if (fileVersion < SETTINGS_VERSION) {
    try {
      writeFileSync(
        path,
        JSON.stringify({ version: SETTINGS_VERSION, ...out }, null, 2) + '\n',
        'utf8',
      )
    } catch (err) {
      problems.push(`could not write the upgraded settings file: ${(err as Error).message}`)
    }
  }

  return { settings: out, problems, path, existed: true, migrated }
}

/**
 * "Ctrl + Shift + C" -> "ctrl+shift+c", modifiers always in the same order.
 *
 * Order is normalised so `shift+ctrl+c` and `ctrl+shift+c` are the same binding
 * — otherwise he writes one, the terminal looks up the other, and nothing
 * happens for a reason he cannot see.
 */
export function normaliseChord(chord: string): string {
  const parts = String(chord)
    .toLowerCase()
    .split('+')
    .map((p) => p.trim())
    .filter(Boolean)
  if (parts.length === 0) return ''
  const mods = new Set<string>()
  let key = ''
  for (const p of parts) {
    if (p === 'ctrl' || p === 'control') mods.add('ctrl')
    else if (p === 'alt') mods.add('alt')
    else if (p === 'shift') mods.add('shift')
    else if (p === 'meta' || p === 'win' || p === 'cmd') mods.add('meta')
    else key = p
  }
  if (!key) return ''
  const order = ['ctrl', 'alt', 'shift', 'meta'].filter((m) => mods.has(m))
  return [...order, key].join('+')
}

/** Write the defaults out once, so there is something to edit rather than a blank. */
export function ensureSettingsFile(): { wrote: boolean; path: string } {
  const path = settingsPath()
  if (existsSync(path)) return { wrote: false, path }
  try {
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(
      path,
      JSON.stringify({ version: SETTINGS_VERSION, ...DEFAULT_SETTINGS }, null, 2) + '\n',
      'utf8',
    )
    return { wrote: true, path }
  } catch {
    return { wrote: false, path }
  }
}
