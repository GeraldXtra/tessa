/**
 * A 1px trace. §R.7: "Sparklines: 1px stroke, 40px tall, no axes, no grid, no
 * legend." This is the column's smaller cousin — 14px tall, same rules.
 *
 * ─── it renders NOTHING until it can be honest ───
 * A trace drawn from two samples is a line segment pretending to be a history.
 * Below `MIN_SAMPLES` this returns null and the caller shows the bare number
 * instead, so the surface degrades to a true smaller statement rather than to a
 * misleading smaller picture. At 5 s per heartbeat, ten samples is fifty
 * seconds — the point at which the shape means something.
 *
 * ─── one path string, not N elements ───
 * The whole trace is a single `<path d>`. A polyline of forty `<line>` elements
 * would be forty DOM nodes replaced every second on a two-core machine, and the
 * only reason this is affordable at 1 Hz is that it is one attribute write.
 */

export const MIN_SAMPLES = 10;

interface SparklineProps {
  /** Oldest first. Rendered left to right. */
  values: readonly number[];
  /**
   * Upper bound for the vertical scale. When omitted the trace self-scales to
   * its own maximum, which is right for CPU (unbounded, spiky) and wrong for
   * anything with a real ceiling.
   */
  max?: number;
  /** Marks the trace as tracking something that has crossed a threshold. */
  level?: 'ok' | 'warning' | 'critical';
}

const W = 92;
const H = 14;

export function Sparkline({ values, max, level = 'ok' }: SparklineProps) {
  if (values.length < MIN_SAMPLES) return null;

  const points = values.slice(-40);
  const ceiling = Math.max(max ?? 0, ...points, Number.EPSILON);
  const step = points.length > 1 ? W / (points.length - 1) : W;

  // Built as one string. `1 -` because SVG y grows downward and a trace reads
  // upward; the 0.5 inset keeps a 1px stroke on the pixel grid rather than
  // straddling two rows and rendering as a 2px grey smear.
  const d = points
    .map((v, i) => {
      const x = i * step;
      const y = 0.5 + (1 - Math.min(1, Math.max(0, v / ceiling))) * (H - 1);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg
      className="spark spark--inline"
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      data-level={level}
      aria-hidden="true"
      preserveAspectRatio="none"
    >
      <path d={d} />
    </svg>
  );
}
