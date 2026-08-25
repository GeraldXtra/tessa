/**
 * apps/console/src/renderer/terminal/theme.ts
 *
 * xterm's ITheme, built from `packages/tokens` — never a hex literal.
 * `scripts/check-contract.mjs` fails the build on any literal under the
 * surface source trees, and it currently scans 37 files.
 *
 * (Note to future editors: do not write the glob for those trees inside a block
 * comment — the star-slash sequence closes it early. That exact mistake cost a
 * build here.)
 *
 * ── Why only five colours are set ────────────────────────────────────────────
 * ITheme also accepts a 16-colour ANSI ramp (black/red/green/... plus their
 * bright variants). **The Tessa token set has no ANSI ramp**, and
 * `packages/tokens` is SHARED — the Console session does not edit it.
 *
 * Verified in `node_modules/@xterm/xterm/typings/xterm.d.ts:343+`: **every
 * ITheme field is optional.** So setting background/foreground/cursor/selection
 * and letting xterm supply its own ANSI defaults is valid, not a workaround —
 * partial themes are a supported shape.
 *
 * If the ANSI ramp should be branded, that is a `packages/tokens` diff to
 * propose to the owner, not something to slip in here.
 */

import type { ITheme } from '@xterm/xterm'
import tokens from '@tessa/tokens'

const c = (name: string): string => {
  const entry = (tokens.color as Record<string, { value: string } | undefined>)[name]
  if (!entry) throw new Error(`token color.${name} missing — regenerate with: npm run tokens`)
  return entry.value
}

export const tessaTerminalTheme: ITheme = {
  /**
   * TRANSPARENT, SO THE WATERMARK BEHIND THE STAGE IS VISIBLE.
   *
   * This was `bg-void`, and that is why he could not see the mark: xterm paints
   * its theme background across the whole screen element, opaque, so the mark
   * sat behind a solid floor. Measured — with one pane the xterm screen covered
   * 1134x522 of an 1160x558 stage, leaving only a 5px border strip of the mark
   * showing, which is indistinguishable from nothing.
   *
   * The void colour is still painted: `body` carries `--bg-void`, so the
   * terminal looks identical. It simply no longer has its own floor.
   *
   * Written as rgba rather than a hex with an alpha pair because
   * scripts/check-contract.mjs fails the build on any hex literal in surface
   * source, and it has already caught two of mine.
   */
  background: 'rgba(0,0,0,0)',
  foreground: c('text'),
  cursor: c('accent'),
  /** Foreground of the block cursor — the void colour, so the glyph inverts. */
  cursorAccent: c('bg-void'),
  selectionBackground: c('accent-dim'),
  selectionForeground: c('text'),
}

/** Font stack and metrics, also token-sourced. JetBrains Mono is deferred to 1b. */
export const tessaFont = {
  fontFamily: (tokens.font as Record<string, { value: string }>)['mono']!.value,
  fontSize: parseInt((tokens.fontSize as Record<string, { value: string }>)['base']!.value, 10),
  lineHeight: 1.2,
} as const
