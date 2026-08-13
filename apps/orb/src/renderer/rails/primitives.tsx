/**
 * The pieces every drawer is built from. §R.7, "Inside a drawer" and
 * "Data display".
 *
 * They exist as one file because §R.7 specifies them once for all five rails:
 * section headers, row height, alternating rows, tabular numbers, the empty
 * state, sparklines, bars, tier badges and timestamps. Five copies of these
 * would be five chances to drift from the section.
 */

import type { ReactNode } from 'react';

/* ─────────────────────────────────────────────────────────────── sections */

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="sec">
      <h3 className="sec__head">{title}</h3>
      {children}
    </section>
  );
}

/**
 * §R.7: "Empty state: the word NO DATA in --text-muted, --fs-label. Never a
 * graphic, never an illustration."
 *
 * This is the single most-used component in the build today, and that is
 * correct. Most of this dashboard has no data source yet, and saying so is the
 * honest rendering. A plausible-looking placeholder would be a lie that
 * survives into screenshots.
 */
export function NoData() {
  return <p className="nodata">NO DATA</p>;
}

/* ───────────────────────────────────────────────────────────────── rows */

export function Row({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="row">
      <span className="row__label">{label}</span>
      <span className="row__value">{value}</span>
      {hint ? <span className="row__hint">{hint}</span> : null}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────── bars */

/**
 * §R.7: "Bars: 4px tall, --radius-pill, track at 6% white."
 *
 * Colour follows the same normal/warning/critical scale as everything else.
 * The thresholds here are the obvious ones for a budget — three-quarters spent
 * is worth noticing, at the cap it is critical — and are the one numeric choice
 * in this file not taken verbatim from §R.7.
 */
export function Bar({ value, max }: { value: number; max: number }) {
  const safeMax = max > 0 ? max : 0;
  const pct = safeMax > 0 ? Math.min(100, (value / safeMax) * 100) : 0;
  const level = safeMax > 0 && pct >= 100 ? 'critical' : pct >= 75 ? 'warning' : 'normal';
  return (
    <div className="bar" data-level={level}>
      <div className="bar__fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────── sparklines */

/**
 * §R.7: "Sparklines: 1px stroke, 40px tall, no axes, no grid, no legend."
 *
 * Redrawn from props, and the props only change when a new 5 s sample lands —
 * so this satisfies "sparklines redraw on a 1 Hz tick, not per sample" by
 * being slower than 1 Hz, and "nothing on the rails animates continuously"
 * because there is no transition on the path at all.
 */
export function Sparkline({
  points,
  max,
  level = 'normal',
}: {
  points: readonly number[];
  max: number;
  level?: 'normal' | 'warning' | 'critical';
}) {
  if (points.length < 2) return <NoData />;

  const H = 40;
  const W = 100;
  const ceiling = max > 0 ? max : 1;
  const step = W / (points.length - 1);
  const d = points
    .map((p, i) => {
      const x = (i * step).toFixed(2);
      const y = (H - Math.min(1, p / ceiling) * H).toFixed(2);
      return `${i === 0 ? 'M' : 'L'}${x},${y}`;
    })
    .join(' ');

  return (
    <svg
      className="spark"
      data-level={level}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}

/* ────────────────────────────────────────────────────────── tier badges */

/**
 * §R.7: "pill, --fs-label, 2px/6px padding — green/amber/red, ALWAYS with the
 * tier word, never colour alone."
 *
 * The words matter beyond style: CONTRACT §6.4 says surfaces render tiers and
 * never evaluate them, and a colour with no word invites the reader to infer a
 * meaning the surface is not entitled to assign.
 */
export function TierBadge({ tier }: { tier: string }) {
  return (
    <span className="tier" data-tier={tier}>
      {tier}
    </span>
  );
}

/* ───────────────────────────────────────────────────────── timestamps */

/**
 * §R.7: "relative under an hour (4m ago), absolute beyond (14:32)".
 */
export function formatTimestamp(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  const ageMs = now - t;
  if (ageMs < 0) return new Date(t).toTimeString().slice(0, 5);
  if (ageMs < 60_000) return `${Math.max(1, Math.floor(ageMs / 1000))}s ago`;
  if (ageMs < 3_600_000) return `${Math.floor(ageMs / 60_000)}m ago`;
  return new Date(t).toTimeString().slice(0, 5);
}
