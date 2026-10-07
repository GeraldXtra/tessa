import type { ReactNode } from 'react';

import { hms } from './format.ts';

export function Val({ text, className = 'v' }: { text: string; className?: string }) {
  return (
    <span key={text} className={`${className} xf`}>
      {text}
    </span>
  );
}

export function Bar({ value, amber = false }: { value: number; amber?: boolean }) {
  const v = Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
  return (
    <span className={`bar${amber ? ' amber' : ''}`} style={{ ['--v' as string]: `${(v * 100).toFixed(1)}%` }}>
      <i />
    </span>
  );
}

export function sparkPoints(values: readonly number[], h: number, min: number, max: number, n = 60): string {
  if (values.length === 0) return '';
  const off = n - values.length;
  const span = max - min || 1;
  return values
    .map((v, i) => `${(((i + off) / (n - 1)) * 300).toFixed(1)},${(h - 1 - ((v - min) / span) * (h - 3)).toFixed(1)}`)
    .join(' ');
}

export function Spark({ values, h, min = 0, max = 1 }: { values: readonly number[]; h: number; min?: number; max?: number }) {
  return (
    <svg className="spark" width="100%" height={h} viewBox={`0 0 300 ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <line className="base" x1="0" y1={h - 0.5} x2="300" y2={h - 0.5} />
      <polyline className="ln" points={sparkPoints(values, h, min, max)} />
    </svg>
  );
}

const SCALE_W = 280;

export function TickScale({ value }: { value: number | null }) {
  const ticks: ReactNode[] = [];
  for (let i = 0; i <= 20; i++) {
    const x = (i / 20) * (SCALE_W - 1) + 0.5;
    const h = i % 10 === 0 ? 8 : i % 2 === 0 ? 5 : 3;
    ticks.push(<line key={i} x1={x} y1="0" x2={x} y2={h} />);
  }
  const mx = value === null ? null : Math.min(1, Math.max(0, value)) * (SCALE_W - 1) + 0.5;
  return (
    <svg className="scale" width="100%" height="22" viewBox={`0 0 ${SCALE_W} 22`} preserveAspectRatio="none" aria-hidden="true">
      {ticks}
      {mx === null ? null : <line className="mk" x1={mx} y1="0" x2={mx} y2="13" />}
      <text x="0" y="21">0</text>
      <text x={SCALE_W / 2 - 5} y="21">50</text>
      <text x={SCALE_W - 16} y="21">100</text>
    </svg>
  );
}

export function NoData({ why, title = 'NO DATA' }: { why: string; title?: string }) {
  return (
    <div className="nodata">
      <b>{title}</b>
      {why}
    </div>
  );
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="loading">
      <b>LOADING</b>
      {Array.from({ length: rows }, (_, i) => (
        <i key={i} />
      ))}
    </div>
  );
}

export function ErrorLine({ text }: { text: string }) {
  return (
    <div className="errline">
      <b>ERROR</b>
      {text}
    </div>
  );
}

export function OfflineBanner({ lastBeatAt }: { lastBeatAt: number | null }) {
  return (
    <div className="nodata banner">
      <b>DAEMON OFFLINE</b>
      {lastBeatAt === null
        ? 'No heartbeat has arrived this session. Nothing below is live.'
        : `These are the last values, from the beat at ${hms(new Date(lastBeatAt))}. They are not live.`}
    </div>
  );
}

export function Sec({ label, right, children }: { label: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="sec">
      <div className="sh">
        <span className="lab">{label}</span>
        <span className="lab">{right ?? ''}</span>
      </div>
      {children}
    </section>
  );
}
