/**
 * Theme persistence — %LOCALAPPDATA%\Zoey\orb-theme.json.
 *
 * Sits beside orb-window.json and follows exactly the same discipline, because
 * it is exactly the same hazard. `orb-window.json` was silently corrupted by
 * this session's own verification runs: a harness resized the window, the
 * persistence layer could not tell that resize apart from a deliberate one, and
 * the owner's Orb opened at 984x652 for days. A theme file is easier to corrupt
 * and easier to miss — a wrong colour looks like a choice.
 *
 * So: an INSTRUMENTED launch neither reads nor writes this file. A capture run
 * that forces ember cannot leave ember behind, and cannot be misled by whatever
 * the previous capture run forced.
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

/**
 * Duplicated from the renderer's theme.ts rather than imported.
 *
 * Main and renderer are separate bundles with separate tsconfigs, and main
 * importing a renderer module would drag `document` into a process that has
 * none. The list is five short strings and the mismatch is caught immediately:
 * an id main writes that the renderer rejects falls back to cyan and says so.
 */
const THEME_IDS = ['cyan', 'amber', 'violet', 'emerald', 'ember'] as const;
export type ThemeId = (typeof THEME_IDS)[number];

export const DEFAULT_THEME: ThemeId = 'cyan';

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === 'string' && (THEME_IDS as readonly string[]).includes(value);
}

function themePath(): string {
  const base =
    process.env['LOCALAPPDATA'] ??
    (process.platform === 'win32'
      ? join(homedir(), 'AppData', 'Local')
      : join(homedir(), '.local', 'share'));
  return join(base, 'Zoey', 'orb-theme.json');
}

export { themePath as orbThemePath };

export type ThemeLoad = {
  theme: ThemeId;
  /** Why this value, in words fit for a log line. Always populated. */
  reason: string;
};

/**
 * Read the saved theme, or fall back to cyan and say which it was.
 *
 * Every failure lands on the SAME rung — cyan, named — rather than propagating
 * a partial value. A theme file that parses but holds `"purple"` is not a
 * different kind of problem from one that does not parse at all: both mean the
 * surface does not know what the owner chose, and both must produce a known
 * palette rather than an unset accent.
 */
export function loadTheme(): ThemeLoad {
  let raw: string;
  try {
    raw = readFileSync(themePath(), 'utf8');
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    return {
      theme: DEFAULT_THEME,
      reason:
        code === 'ENOENT'
          ? 'no saved theme — falling back to cyan'
          : `cannot read the theme file (${code}) — falling back to cyan`,
    };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw);
  } catch {
    return { theme: DEFAULT_THEME, reason: 'theme file is not valid JSON — falling back to cyan' };
  }

  const value = (parsed as { theme?: unknown } | null)?.theme;
  if (!isThemeId(value)) {
    return {
      theme: DEFAULT_THEME,
      reason: `theme file holds ${JSON.stringify(value)}, which is not one of ${THEME_IDS.join('/')} — falling back to cyan`,
    };
  }

  return { theme: value, reason: `restored ${value} from ${themePath()}` };
}

export function saveTheme(theme: ThemeId): void {
  try {
    const path = themePath();
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, `${JSON.stringify({ theme }, null, 2)}\n`, 'utf8');
  } catch (err) {
    // Never fatal, same as the window state. Forgetting a colour preference is
    // not worth taking down an always-on surface.
    console.warn(`[orb] could not save theme: ${(err as Error).message}`);
  }
}
