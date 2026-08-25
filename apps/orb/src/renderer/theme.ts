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
export const THEME_IDS = ['gold', 'magenta', 'cyan', 'violet', 'emerald', 'red'] as const;
export type ThemeId = (typeof THEME_IDS)[number];

export const DEFAULT_THEME: ThemeId = 'gold';

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === 'string' && (THEME_IDS as readonly string[]).includes(value);
}

/**
 * Ctrl+Shift+<letter>. Renderer-local, NOT a `globalShortcut` — these are a
 * personal display preference, and taking six OS-wide chords away from every
 * other application for that would be indefensible.
 *
 * The collision is gone. Amber and ember are retired, so every remaining theme
 * takes its own initial and the arbitrary "ember sorts before emerald so it
 * gets E" rule can be deleted. G was magenta and is now gold, which is the one
 * remapping to be aware of.
 */
export const THEME_SHORTCUT: Readonly<Record<string, ThemeId>> = {
  G: 'gold',
  M: 'magenta',
  C: 'cyan',
  V: 'violet',
  E: 'emerald',
  R: 'red',
};

/**
 * Which theme a keydown selects, or null.
 *
 * `code` FIRST, then `key` — and the fallback is not redundant. `code` is
 * derived from the hardware scancode, and synthetic input arrives with scancode
 * 0 and therefore no usable `code`: on-screen keyboards, remote desktop,
 * accessibility tools, and the `keybd_event` injection used to verify this
 * build. This shortcut was written matching `code` alone and was silently dead
 * for every one of them — the first real keystroke test sent Ctrl+Shift+M, took
 * the foreground, and nothing happened.
 *
 * The identical trap is already documented on the Alt+1…6 cycler in App.tsx.
 * Repeating it once is careless; leaving it would make six shortcuts
 * unreachable for anyone not using a physical keyboard.
 *
 * `key` is uppercased because Shift is held: the character arrives as 'M', but
 * a layout or a tool may deliver 'm'.
 */
export function themeForKey(code: string, key: string): ThemeId | null {
  const byCode = code.startsWith('Key') ? THEME_SHORTCUT[code.slice(3)] : undefined;
  if (byCode) return byCode;
  return THEME_SHORTCUT[key.toUpperCase()] ?? null;
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
const THEMED = ['--accent', '--accent-dim', '--sphere-hot', '--sphere-cool', '--sphere-idle'] as const;

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
export function applyTheme(id: ThemeId): { core: string; body: string; idle: string } {
  const core = tokenValue(`--theme-${id}-core`);
  const body = tokenValue(`--theme-${id}-body`);
  const idle = tokenValue(`--theme-${id}-idle`);
  const void_ = tokenValue('--theme-void');

  const root = document.documentElement;
  const set = (name: string, value: string): void => {
    // Never write an empty value: that would REMOVE the property and fall back
    // to the generated default, silently mixing two palettes on one screen.
    if (value) root.style.setProperty(name, value);
  };

  set('--accent', body);
  set('--accent-dim', idle);
  set('--sphere-hot', core);
  set('--sphere-cool', body);
  set('--sphere-idle', idle);

  // Pure black stage, per the brief. Overridden here rather than in tokens.json
  // because `--bg-void` is SHARED with apps/console (CONTRACT §9) — its near-
  // black is that surface's background too, and this session does not get to
  // restyle the Console by editing a token they both read.
  set('--bg-void', void_);

  root.dataset['theme'] = id;
  current = id;

  return { core, body, idle };
}

/** Which properties this module owns. Exported for the report, not for logic. */
export const THEMED_PROPERTIES: readonly string[] = THEMED;
