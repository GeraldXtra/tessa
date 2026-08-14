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

/**
 * Shortest length a leading excerpt may be before a sentence terminator is
 * believed.
 *
 * Abbreviations are why. "It is 6:57 P.M." has terminators after `P` and `M`,
 * and cutting at the first one yields "It is 6:57 P." — which is not a shorter
 * version of the answer, it is a different and wrong one. Requiring the excerpt
 * to reach a plausible sentence length before a terminator counts steps over
 * the common cases (P.M., e.g., Dr., 3.5) without pretending to parse English.
 *
 * It is a heuristic and it will be wrong somewhere. The failure is bounded: the
 * excerpt runs one sentence long, which reads fine, rather than one word short,
 * which reads broken.
 */
const MIN_EXCERPT_CHARS = 40;

export interface Excerpt {
  /** The leading sentence(s), whitespace-collapsed. */
  head: string;
  /** Words NOT in `head`. Zero when `head` is the whole text. */
  remainingWords: number;
}

/**
 * The first sentence of a long answer, and an honest count of what is left.
 *
 * Shared so the under-sphere line and TRACE cannot disagree about where an
 * answer's first sentence ends — the same reason `formatMetric` is shared.
 *
 * ─── why a sentence, and why the count ───
 * A character truncation cuts mid-word and reads as breakage; a sentence is a
 * complete thought and reads as information. But a first sentence shown ALONE
 * is a lie by omission when eight paragraphs follow — it looks like the whole
 * answer. That is the same class of error as a false zero: a real value
 * rendered as an unreal one. So the count travels with the excerpt, always.
 *
 * Words, not sentences, as the unit: "+212 words" is something a person can
 * size up instantly, where "+4 sentences" could be a line or a page.
 */
export function excerpt(text: string): Excerpt {
  const clean = text.trim().replace(/\s+/g, ' ');
  if (clean.length === 0) return { head: '', remainingWords: 0 };

  const terminator = /[.!?](?=\s|$)/g;
  let end = -1;
  let match: RegExpExecArray | null;
  while ((match = terminator.exec(clean)) !== null) {
    if (match.index + 1 >= MIN_EXCERPT_CHARS) {
      end = match.index + 1;
      break;
    }
  }

  // No terminator far enough in — the whole thing is one short utterance.
  if (end < 0 || end >= clean.length) return { head: clean, remainingWords: 0 };

  const rest = clean.slice(end).trim();
  const remainingWords = rest.length === 0 ? 0 : rest.split(' ').length;
  return remainingWords === 0
    ? { head: clean, remainingWords: 0 }
    : { head: clean.slice(0, end), remainingWords };
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
