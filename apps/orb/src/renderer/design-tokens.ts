/**
 * Reading design tokens from JavaScript.
 *
 * CONTRACT §9: neither surface hard-codes a value from tokens.json. CSS gets
 * that for free with `var(--token)`, but three places need a token as a *number*
 * or a *string* in JS — the shader colour uniforms, the drawer width the sphere
 * offsets by, and the window background in main.
 *
 * The generated custom properties on :root are the single source, so this reads
 * them back out of the computed style rather than re-declaring anything.
 * scripts/check-contract.mjs enforces the same rule from the other direction:
 * a hex literal in any .ts/.tsx/.css under apps/orb/src fails the build.
 */

/** Raw token value, e.g. `--sphere-hot` resolves to its colour. Empty string if unset. */
export function tokenValue(property: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(property).trim();
}

/**
 * Numeric token, e.g. `--transcript-w` → 320. The fallback covers first paint,
 * before the stylesheet has applied.
 */
export function tokenPx(property: string, fallback: number): number {
  const parsed = Number.parseFloat(tokenValue(property));
  return Number.isFinite(parsed) ? parsed : fallback;
}
