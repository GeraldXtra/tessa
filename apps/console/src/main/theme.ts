/**
 * apps/console/src/main/theme.ts — the Console follows the Orb's companion.
 *
 * HIS RULING: switching companion recolours BOTH surfaces. The Orb owns the
 * choice and persists it; the Console reads it. There is no second source of
 * truth and the Console never writes this file — Session 2 owns it, and two
 * writers is how a preference starts flickering.
 *
 * THE FILE IS READ, NOT ASSUMED. Session 2 is moving this value and migrating
 * the stored theme to gold in the same session as this work, so anything
 * hard-coded here would be wrong within the hour. An unrecognised name falls
 * back and says so rather than rendering an unstyled surface.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * The six companions, mapped to the theme tokens that actually exist.
 *
 * `packages/tokens` is SHARED and Session 2 has it this session, so this maps
 * onto what is published rather than adding tokens: there is no
 * `--theme-gold-*` or `--theme-red-*`, but `amber` and `ember` are exactly
 * those hues and are already measured. If Session 2 publishes real gold/red
 * tokens later, the renderer prefers them automatically — see app.css, which
 * keys off the theme NAME and would simply gain two more blocks.
 */
export const THEMES = ['gold', 'magenta', 'cyan', 'violet', 'emerald', 'red'] as const
export type ThemeName = (typeof THEMES)[number]

export const DEFAULT_THEME: ThemeName = 'gold'

function themePath(): string {
  const base = process.env['LOCALAPPDATA'] ?? process.env['APPDATA'] ?? ''
  return join(base, 'Tessa', 'orb-theme.json')
}

export interface ThemeRead {
  theme: ThemeName
  /** The raw value on disk, for the log when it was not recognised. */
  raw: string
  source: 'orb' | 'default'
  problem?: string
}

/** Never throws. A missing or unreadable file is the default, said out loud. */
export function readTheme(): ThemeRead {
  const path = themePath()
  let raw = ''
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf8')) as { theme?: unknown }
    raw = String(parsed.theme ?? '')
  } catch (err) {
    return {
      theme: DEFAULT_THEME,
      raw: '',
      source: 'default',
      problem: `could not read ${path}: ${(err as Error).message}`,
    }
  }
  const name = raw.trim().toLowerCase()
  if ((THEMES as readonly string[]).includes(name)) {
    return { theme: name as ThemeName, raw, source: 'orb' }
  }
  return {
    theme: DEFAULT_THEME,
    raw,
    source: 'default',
    problem: `theme "${raw}" is not one of ${THEMES.join(', ')} — using ${DEFAULT_THEME}`,
  }
}
