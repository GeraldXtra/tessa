/**
 * Theme persistence — %LOCALAPPDATA%\Tessa\orb-theme.json.
 *
 * Sits beside orb-window.json and follows exactly the same discipline, because
 * it is exactly the same hazard. `orb-window.json` was silently corrupted by
 * this session's own verification runs: a harness resized the window, the
 * persistence layer could not tell that resize apart from a deliberate one, and
 * the owner's Orb opened at 984x652 for days. A theme file is easier to corrupt
 * and easier to miss — a wrong colour looks like a choice.
 *
 * So: an INSTRUMENTED launch neither reads nor writes this file. A capture run
 * that forces violet cannot leave violet behind, and cannot be misled by
 * whatever the previous capture run forced. THAT GUARD ALSO PROTECTS THE
 * MIGRATION BELOW: a --force-theme run must not trip the one-shot rewrite and
 * burn it on a value he never chose.
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
 * an id main writes that the renderer rejects falls back to gold and says so.
 */
/**
 * DUPLICATED FROM renderer/theme.ts, ON PURPOSE, AND IT MUST BE KEPT IN STEP.
 *
 * Main cannot import from the renderer bundle, and this list is the validator
 * that stops a compromised renderer writing an arbitrary string into his config
 * — so it has to live on this side. The cost is that the two can drift, and a
 * drift here is silent: main would refuse a theme the renderer thinks is valid.
 * Adding magenta in one place and not the other is exactly that bug.
 */
const THEME_IDS = ['gold', 'magenta', 'cyan', 'violet', 'emerald', 'red', 'ultron'] as const;
export type ThemeId = (typeof THEME_IDS)[number];

export const DEFAULT_THEME: ThemeId = 'ultron';

const THEME_FILE_VERSION = 3;

export const SWITCH_TO: ThemeId = 'ultron';

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === 'string' && (THEME_IDS as readonly string[]).includes(value);
}

function themePath(): string {
  const base =
    process.env['LOCALAPPDATA'] ??
    (process.platform === 'win32'
      ? join(homedir(), 'AppData', 'Local')
      : join(homedir(), '.local', 'share'));
  return join(base, 'Tessa', 'orb-theme.json');
}

export { themePath as orbThemePath };

export type ThemeLoad = {
  theme: ThemeId;
  /** True when the stored value was rewritten. Logged, never silent. */
  migrated: boolean;
  /** Why this value, in words fit for a log line. Always populated. */
  reason: string;
  was?: string | null;
};

export function themeFileAllowed(instrumented: boolean, persistFlag: boolean): boolean {
  return !instrumented || persistFlag;
}

function storedWas(path: string): string | null {
  try {
    const raw = readFileSync(path, 'utf8');
    const record = JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw) as { was?: unknown } | null;
    return typeof record?.was === 'string' ? record.was.slice(0, 40) : null;
  } catch {
    return null;
  }
}

/**
 * Read the saved theme, or fall back to GOLD and say which it was.
 *
 * Every failure lands on the SAME rung — gold, named — rather than propagating
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
      migrated: false,
      reason:
        code === 'ENOENT'
          ? `no saved theme — falling back to ${DEFAULT_THEME}`
          : `cannot read the theme file (${code}) — falling back to ${DEFAULT_THEME}`,
    };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw);
  } catch {
    return {
      theme: DEFAULT_THEME,
      migrated: false,
      reason: `theme file is not valid JSON — falling back to ${DEFAULT_THEME}`,
    };
  }

  const record = parsed as { theme?: unknown; v?: unknown } | null;
  const value = record?.theme;
  const version = typeof record?.v === 'number' ? record.v : 1;

  if (version < THEME_FILE_VERSION) {
    const was = typeof value === 'string' ? value.slice(0, 40) : null;
    saveTheme(SWITCH_TO, was);
    return {
      theme: SWITCH_TO,
      migrated: true,
      was,
      reason:
        `SWITCHED stored theme ${JSON.stringify(value)} -> '${SWITCH_TO}' once (file was v${version}, ` +
        `now v${THEME_FILE_VERSION}). The old value is kept in the file as "was"; any theme chosen from now on is honoured.`,
    };
  }

  /**
   * A STORED THEME THAT NO LONGER EXISTS — `amber` and `ember` are the live
   * cases. It falls back to the default and the refused value is logged
   * VERBATIM, because a silent fallback looks exactly like a chosen colour.
   */
  if (!isThemeId(value)) {
    const retired = value === 'amber' || value === 'ember' ? ' (retired this round)' : '';
    return {
      theme: DEFAULT_THEME,
      migrated: false,
      reason:
        `theme file holds ${JSON.stringify(value)}${retired}, which is not one of ` +
        `${THEME_IDS.join('/')} — falling back to ${DEFAULT_THEME}`,
    };
  }

  return { theme: value, migrated: false, reason: `restored ${value} from ${themePath()}` };
}

export function saveTheme(theme: ThemeId, was?: string | null): void {
  try {
    const path = themePath();
    mkdirSync(dirname(path), { recursive: true });
    const keep = was === undefined ? storedWas(path) : was;
    // `v` is a SIBLING of `theme`, never a replacement for it. The key and the
    // path are both unchanged; see THEME_FILE_VERSION for why the stamp exists
    // and for the note that Session 1's Console also reads this file.
    const body: { theme: ThemeId; v: number; was?: string } = { theme, v: THEME_FILE_VERSION };
    if (typeof keep === 'string') body.was = keep;
    writeFileSync(path, `${JSON.stringify(body, null, 2)}\n`, 'utf8');
  } catch (err) {
    // Never fatal, same as the window state. Forgetting a colour preference is
    // not worth taking down an always-on surface.
    console.warn(`[orb] could not save theme: ${(err as Error).message}`);
  }
}
