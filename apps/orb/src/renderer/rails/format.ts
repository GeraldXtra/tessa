/**
 * Shared value formatting.
 *
 * Extracted so the top bar and PULSE cannot format the same number two
 * different ways. §R.2's top bar shows uptime and API spend, and both also
 * appear in PULSE; a second implementation of either is a guaranteed future
 * disagreement between the bar and the drawer.
 */

/**
 * Format without ever printing a FALSE ZERO.
 *
 * `(0.04).toFixed(1)` is `"0.0"`, which reads as "nothing is happening" when
 * something is. A real value rendered as an unreal one is the same class of
 * error as fabricated data, so anything non-zero that would round to zero
 * prints `<0.1` instead. A genuine zero still prints `0.0`, because "nothing"
 * and "not quite nothing" are different facts and the bar should distinguish
 * them.
 */
export function formatMetric(value: number, digits: number): string {
  if (!Number.isFinite(value)) return '—';
  if (value === 0) return (0).toFixed(digits);
  const rounded = Number(value.toFixed(digits));
  if (rounded === 0) {
    // Sign is preserved deliberately. `(-0.04).toFixed(1)` is `"-0.0"`, and
    // `Number("-0.0")` is `-0`, which `=== 0` — so a naive guard here reports a
    // small NEGATIVE value as `<0.1`, claiming it is positive. No health field
    // is negative today, but rendering a real value as an unreal one is the
    // exact failure this function exists to prevent, and it should not depend
    // on the callers all happening to be non-negative.
    const epsilon = (1 / 10 ** digits).toFixed(digits);
    return value < 0 ? `>-${epsilon}` : `<${epsilon}`;
  }
  return value.toFixed(digits);
}

/** Compact uptime: 45s · 3m 12s · 2h 5m · 1d 4h. */
export function formatUptime(seconds: number): string {
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}
