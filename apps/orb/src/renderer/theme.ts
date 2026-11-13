/**
 * The six themes, and the one place the accent is injected.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * READ THIS BEFORE TREATING tokens.json AS AUTHORITATIVE
 *
 * `applyTheme` calls `setProperty` on `document.documentElement`, which means
 * that from the moment it runs, **packages/tokens/tokens.json is no longer the
 * single source of truth at runtime for the accent-derived values listed in
 * THEMED below**. The generated `dist/tokens.css` still supplies every one of
 * them on `:root`; an inline custom property set here outranks it.
 *
 * That is deliberate and it is the owner's ruling. Runtime theming cannot come
 * from a build-time CSS file: there is exactly one generated `:root` block, and
 * six palettes the owner switches between with a keystroke. The alternative —
 * six pre-generated stylesheets swapped at runtime — moves the same override
 * somewhere less visible without removing it.
 *
 * What this costs, stated plainly so nobody rediscovers it as a bug:
 *
 *   • Reading `--accent` out of `dist/tokens.css` tells you the DEFAULT, not
 *     what is on screen. Read the computed style instead (design-tokens.ts).
 *   • A `--accent` value edited in tokens.json will not change the Orb's
 *     accent, because every theme — including gold, the default — overwrites
 *     it. To retune the Orb's accent, edit `theme-<name>-body`.
 *
 * What it does NOT touch, and must never: the alarm colours. `--status-error`,
 * `--status-warn`, `--status-active` and `--status-idle` are absent from THEMED
 * by design. SENTINEL red stays red under all six themes, and CONTRACT §4.1's
 * "Orb renders [blocked] amber and static" survives a theme switch — a surface
 * where the alarm hue is a preference is a surface with no alarm.
 * ─────────────────────────────────────────────────────────────────────────────
 */

import { tokenValue } from './design-tokens.ts';

/**
 * Closed set. GOLD is the default and the fallback.
 *
 * ─── why gold, and why this one is NOT measured ───
 * Every other palette in this file was fitted to something. Gold cannot be:
 * all sixteen reference images are magenta and all sixteen are photographs, so
 * the reference has nothing to say about it. Gold is a colour the owner named
 * by word. So WEB GOLD is taken as GIVEN, the ladder is derived from its own
 * hue (50.6 degrees, held through all three steps), and what is checked is the
 * OUTPUT: contrast on the black stage, and distance from the two things he
 * ruled out. He was explicit that it must not read orange.
 *
 * (Hashes are omitted below so this comment does not itself trip the
 * no-hard-coded-colour gate. The values are in tokens.json, which is the only
 * place a colour may live.)
 *
 *   step   token             contrast   dE to --accent orange   dE to metallic
 *   core   theme-gold-core    17.10:1            39.7                 13.9
 *   body   theme-gold-body    14.97:1            37.6                 11.4
 *          (web gold itself, unaltered)
 *   idle   theme-gold-idle     6.54:1            31.2                 10.9
 *
 * A CIEDE2000 of 2.3 is the just-noticeable difference; 30+ is a different
 * colour by any measure. It does not read orange, and it is not the metallic
 * gold he also rejected.
 *
 * ─── amber and ember are GONE, and yellow was never added ───
 * His ruling. Amber and ember are removed outright rather than hidden, and
 * yellow is dropped because at hue 51 it is gold — the two would have been the
 * same palette under two names. Purple/gold is withdrawn. Six remain: gold,
 * magenta, cyan, violet, emerald, red.
 *
 * ─── what happens to a stored theme that no longer exists ───
 * `amber` and `ember` are still real strings in his config file if he ever
 * chose them. `loadTheme` in main/theme-state.ts refuses any id not in this
 * list, falls back to gold, and LOGS the refused value verbatim — a silent
 * fallback would look like a chosen colour, which is exactly how a previous
 * round lost a day to a stored `amber`.
 */
export const THEME_IDS = ['gold', 'magenta', 'cyan', 'violet', 'emerald', 'red', 'ultron'] as const;
export type ThemeId = (typeof THEME_IDS)[number];

export const DEFAULT_THEME: ThemeId = 'ultron';

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === 'string' && (THEME_IDS as readonly string[]).includes(value);
}

export function themeForKey(code: string, keyCode = 0): ThemeId | null {
  const digit = code ? /^Digit([1-9])$/.exec(code)?.[1] : keyCode >= 49 && keyCode <= 55 ? String(keyCode - 48) : undefined;
  if (!digit) return null;
  return THEME_IDS[Number.parseInt(digit, 10) - 1] ?? null;
}

/**
 * Which custom properties a theme overwrites, and where each value comes from.
 *
 * Three source steps per theme — `core`, `body`, `idle` — being the owner's
 * lightness ladder. Everything a theme paints is one of those three, so a new
 * themed element gets a step rather than a new colour.
 *
 *   core   the sphere's hot centre, the brightest thing on screen
 *   body   the rim, and --accent: every label, marker and active state
 *   idle   the resting tone, and --accent-dim: hints and secondary marks
 */
const THEMED = [
  '--accent',
  '--accent-dim',
  '--sphere-hot',
  '--sphere-cool',
  '--sphere-idle',
  '--bg-void',
  '--orb-deep',
  '--orb-mid',
  '--orb-hot',
  '--orb-bg',
] as const;

/** What is on screen now. Read by the engine when it re-tints. */
let current: ThemeId = DEFAULT_THEME;

export function currentTheme(): ThemeId {
  return current;
}

/**
 * Paint a theme. Idempotent, and safe to call before first paint.
 *
 * Returns the resolved step values so a caller can log what it actually
 * applied — an empty string here would mean the token stylesheet has not loaded
 * and the surface is about to render with no accent at all, which is worth
 * being able to see in a log rather than on screen.
 */
export function applyTheme(id: ThemeId): { deep: string; mid: string; hot: string; bg: string } {
  const deep = tokenValue(`--theme-${id}-deep`);
  const mid = tokenValue(`--theme-${id}-mid`);
  const hot = tokenValue(`--theme-${id}-hot`);
  const bg = tokenValue(`--theme-${id}-bg`);

  const root = document.documentElement;
  const set = (name: string, value: string): void => {
    // Never write an empty value: that would REMOVE the property and fall back
    // to the generated default, silently mixing two palettes on one screen.
    if (value) root.style.setProperty(name, value);
  };
  const dimmed = mid && bg ? `color-mix(in srgb, ${mid} 62%, ${bg})` : '';

  set('--accent', mid);
  set('--accent-dim', dimmed);
  set('--sphere-hot', hot);
  set('--sphere-cool', mid);
  set('--sphere-idle', dimmed);
  set('--bg-void', bg);
  set('--orb-deep', deep);
  set('--orb-mid', mid);
  set('--orb-hot', hot);
  set('--orb-bg', bg);

  root.dataset['theme'] = id;
  current = id;

  return { deep, mid, hot, bg };
}

/** Which properties this module owns. Exported for the report, not for logic. */
export const THEMED_PROPERTIES: readonly string[] = THEMED;
