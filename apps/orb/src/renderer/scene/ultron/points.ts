import { ORB_R, TAU, orbitPeriod } from './model.ts';

export function seeded(seed: number): () => number {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5;
    s >>>= 0;
    return s / 4294967296;
  };
}

type Vec3 = [number, number, number];

function unit(v: Vec3): Vec3 {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}

function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function onSphere(rnd: () => number): Vec3 {
  const z = rnd() * 2 - 1;
  const t = rnd() * TAU;
  const q = Math.sqrt(Math.max(0, 1 - z * z));
  return [q * Math.cos(t), z, q * Math.sin(t)];
}

export const ORB_STRIDE = 12;

export interface OrbCloud {
  data: Float32Array;
  fixed: number;
  total: number;
  arcs: number;
}

export const RINGS: readonly { r: number; n: Vec3; axis: Vec3; w: number }[] = [
  { r: 0.92, n: [0.2, 0.93, 0.3], axis: [0.55, 0.25, 0.8], w: 0.065 },
  { r: 0.97, n: [-0.6, 0.5, 0.62], axis: [0.12, 0.98, 0.15], w: -0.055 },
  { r: 1.02, n: [0.68, 0.33, -0.65], axis: [-0.28, 0.92, 0.27], w: 0.06 },
];

export const RING_RATE: readonly number[] = [0.11, -0.085, 0.13];
export const ARC_COUNT = 7;
export const ARC_POINTS = 240;

export function ringBasis(n: Vec3): [Vec3, Vec3] {
  const nn = unit(n);
  const u = unit(cross(nn, Math.abs(nn[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0]));
  return [u, cross(nn, u)];
}

function gauss(rnd: () => number): number {
  return (rnd() + rnd() + rnd() + rnd() - 2) * 0.866;
}

export function buildOrb(seed = 20261111): OrbCloud {
  const rnd = seeded(seed);
  const fixedRows: number[][] = [];
  const rest: number[][] = [];
  const put = (into: number[][], p: Vec3, kind: number, axis: Vec3, w: number, size: number, bright: number, param: number, phase: number): void => {
    into.push([p[0], p[1], p[2], kind, axis[0], axis[1], axis[2], w, size, bright, param, phase]);
  };
  const up: Vec3 = [0, 1, 0];
  for (let k = 0; k < ARC_COUNT; k++) {
    for (let j = 0; j < ARC_POINTS; j++) put(fixedRows, [k, j / (ARC_POINTS - 1), 0], 6, up, 0, 1.25 + 0.55 * rnd(), 0.95, 0, rnd());
  }
  const arcs = fixedRows.length;
  RINGS.forEach((g, k) => {
    const nn = unit(g.n);
    const [u, v] = ringBasis(g.n);
    const ax = unit(g.axis);
    const lanes = 4;
    const per = 1800;
    for (let lane = 0; lane < lanes; lane++) {
      const off = (lane - (lanes - 1) / 2) * 0.0085;
      const weight = 1 - Math.abs(lane - (lanes - 1) / 2) * 0.3;
      for (let q = 0; q < per; q++) {
        const f = (q + rnd()) / per;
        const t = f * TAU;
        const rr = g.r * (1 + off * 0.45 + gauss(rnd) * 0.0012);
        const c = Math.cos(t) * rr;
        const s = Math.sin(t) * rr;
        put(fixedRows, [u[0] * c + v[0] * s + nn[0] * off, u[1] * c + v[1] * s + nn[1] * off, u[2] * c + v[2] * s + nn[2] * off], 1, ax, g.w, 1.15 + 0.65 * weight, 0.18 + 0.45 * weight, k, f);
      }
    }
  });
  for (let i = 0; i < 13000; i++) {
    const d = onSphere(rnd);
    const r = ORB_R * (1 + gauss(rnd) * 0.003);
    put(rest, [d[0] * r, d[1] * r, d[2] * r], 0, up, 0.045, 1.45 + 1.15 * rnd(), 0.48 + 0.34 * rnd(), 0, rnd());
  }
  for (let i = 0; i < 5500; i++) {
    const d = onSphere(rnd);
    const r = ORB_R * 0.945 * (1 + gauss(rnd) * 0.004);
    put(rest, [d[0] * r, d[1] * r, d[2] * r], 0, up, 0.045, 1.2 + 1.0 * rnd(), 0.16 + 0.14 * rnd(), 0, rnd());
  }
  for (let i = 0; i < 18000; i++) {
    const u = rnd();
    const r = 0.045 + 0.54 * Math.pow(u, 0.8);
    const rn = (r - 0.045) / 0.54;
    const onArm = rnd() < 0.7;
    const th = onArm ? (rnd() < 0.5 ? 0 : Math.PI) + 3.15 * Math.log(r / 0.04) * 0.72 + gauss(rnd) * (0.16 + 0.32 * rn) : rnd() * TAU;
    const h = gauss(rnd) * (0.01 + 0.026 * (1 - rn));
    put(rest, [Math.cos(th) * r, h, Math.sin(th) * r], 2, up, -0.24, 1.05 + 1.15 * rnd() * (1 - 0.45 * rn), (onArm ? 0.62 : 0.28) * (1.2 - 0.55 * rn) + 0.1 * rnd(), rn, rnd());
  }
  for (let i = 0; i < 2500; i++) {
    const d = onSphere(rnd);
    const r = 0.11 * Math.pow(rnd(), 1.5);
    put(rest, [d[0] * r, d[1] * r * 0.55, d[2] * r], 2, up, -0.24, 1.15 + rnd(), 0.72 + 0.38 * rnd(), 0.04 * rnd(), rnd());
  }
  for (let i = 0; i < 1500; i++) {
    const r = 0.074 + gauss(rnd) * 0.004;
    const th = rnd() * TAU;
    put(rest, [Math.cos(th) * r, gauss(rnd) * 0.0015, Math.sin(th) * r], 2, up, -0.85, 1.3 + 0.8 * rnd(), 1 + 0.3 * rnd(), 0.02, rnd());
  }
  for (let i = 0; i < 2500; i++) {
    const d = onSphere(rnd);
    const r = 0.012 + 0.095 * Math.pow(rnd(), 1.7);
    const sign = rnd() < 0.5 ? -1 : 1;
    put(rest, [d[0] * r, d[1] * r, d[2] * r], 3, unit([rnd() - 0.5, 1.6, rnd() - 0.5]), (0.45 + 0.75 * rnd()) * sign, 1.4 + 1.5 * rnd(), 0.36 + 0.36 * (1 - r / 0.107), 0, rnd());
  }
  for (let i = 0; i < 1800; i++) {
    const d = onSphere(rnd);
    const r = ORB_R * (1.07 + 0.55 * Math.pow(rnd(), 1.6));
    put(rest, [d[0] * r, d[1] * r, d[2] * r], 4, up, 0.025 + 0.05 * rnd(), 0.9 + 0.9 * rnd(), 0.1 + 0.22 * rnd(), 0, rnd());
  }
  const blobs: { c: Vec3; s: number }[] = [];
  for (let i = 0; i < 9; i++) {
    const d = onSphere(rnd);
    const r = 0.25 + 0.45 * rnd();
    blobs.push({ c: [d[0] * r, d[1] * r, d[2] * r], s: 0.12 + 0.16 * rnd() });
  }
  for (let made = 0, guard = 0; made < 15000 && guard < 600000; guard++) {
    const d = onSphere(rnd);
    const r = ORB_R * 0.9 * Math.cbrt(rnd());
    if (r < 0.17) continue;
    const p: Vec3 = [d[0] * r, d[1] * r, d[2] * r];
    let dens = 0;
    for (const b of blobs) {
      const q = ((p[0] - b.c[0]) ** 2 + (p[1] - b.c[1]) ** 2 + (p[2] - b.c[2]) ** 2) / (b.s * b.s);
      dens += Math.exp(-q);
    }
    if (rnd() > 0.12 + 0.88 * Math.min(1, dens)) continue;
    const rn = r / (ORB_R * 0.9);
    put(rest, p, 5, up, 0.05 + 0.1 * (1 - rn), 1.4 + 1.5 * rnd(), (0.08 + 0.22 * Math.min(1, dens)) * (1.1 - 0.4 * rn), rn, rnd());
    made++;
  }
  for (let i = rest.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    const t = rest[i] as number[];
    rest[i] = rest[j] as number[];
    rest[j] = t;
  }
  const total = fixedRows.length + rest.length;
  const data = new Float32Array(total * ORB_STRIDE);
  let o = 0;
  for (const r of fixedRows) {
    data.set(r, o);
    o += ORB_STRIDE;
  }
  for (const r of rest) {
    data.set(r, o);
    o += ORB_STRIDE;
  }
  return { data, fixed: fixedRows.length, total, arcs };
}

export const DUST_STRIDE = 8;

export interface DustCloud {
  data: Float32Array;
  always: number;
  inflowStart: number;
  inflowCount: number;
  galaxyStart: number;
  galaxyCount: number;
  total: number;
}

export function buildDust(seed = 20261112): DustCloud {
  const rnd = seeded(seed);
  const rows: number[][] = [];
  for (let i = 0; i < 1000; i++) {
    const rr = 3.5 + Math.pow(rnd(), 0.65) * 5.5;
    const d = onSphere(rnd);
    rows.push([d[0] * rr, d[1] * rr, d[2] * rr, 0, rnd(), 0, 0, 0]);
  }
  for (let i = 0; i < 800; i++) {
    const r0 = 2.05 + gauss(rnd) * 0.06;
    rows.push([r0, gauss(rnd) * 0.015, rnd() * TAU, 1, 0.3 + rnd() * 1.1, TAU / orbitPeriod(r0), 0.25 + rnd() * 0.5, rnd()]);
  }
  for (let i = 0; i < 1100; i++) rows.push([rnd() * TAU, Math.pow(rnd(), 1.5), rnd(), 3, rnd(), 0.05 + rnd() * 0.11, rnd(), 0]);
  const always = rows.length;
  for (let i = 0; i < 900; i++) {
    const d = onSphere(rnd);
    rows.push([d[0], d[1], d[2], 2, rnd(), 0.22 + 0.26 * rnd(), rnd(), rnd() < 0.5 ? -1 : 1]);
  }
  const galaxyStart = rows.length;
  for (let i = 0; i < 3400; i++) rows.push([gauss(rnd) * 0.5, rnd() * TAU, 0, 4, rnd(), 0.85 + 1.45 * Math.sqrt(rnd()), 0, 0]);
  const data = new Float32Array(rows.length * DUST_STRIDE);
  rows.forEach((r, i) => data.set(r, i * DUST_STRIDE));
  return { data, always, inflowStart: always, inflowCount: 900, galaxyStart, galaxyCount: 3400, total: rows.length };
}

export const LINE_STRIDE = 12;

export const HUD = { bezel: 1.09, notch: 1.19, dash: 1.215, bars: 1.23, cardinal: 1.34, arcA: 1.3, arcB: 1.325, numbers: 1.15, letters: 1.43 } as const;

export const LG = {
  orbit0: 0,
  galaxy0: 4,
  bezel: 7,
  card: 8,
  arcA: 9,
  arcB: 10,
  screen: 11,
} as const;

export const LK = { plain: 0, card: 3, hud: 4, spec: 5, orbit: 6 } as const;
export const TONE = { faint: 0, dim: 1, mid: 2, bright: 3, hot: 4 } as const;

export interface LineSet {
  data: Float32Array;
  count: number;
  galaxyFrom: number;
  galaxyTo: number;
}

export function buildLines(): LineSet {
  const rows: number[][] = [];
  const seg = (a: Vec3, b: Vec3, tone: number, alpha: number, group: number, kind: number, lane = 0): void => {
    rows.push([a[0], a[1], a[2], tone, b[0], b[1], b[2], alpha, group, kind, lane, 0]);
  };
  const circle = (group: number, kind: number, tone: number, alpha: number): void => {
    const n = 240;
    for (let i = 0; i < n; i++) {
      const a0 = (i / n) * TAU;
      const a1 = ((i + 1) / n) * TAU;
      seg([Math.cos(a0), 0, Math.sin(a0)], [Math.cos(a1), 0, Math.sin(a1)], tone, alpha, group, kind);
    }
  };
  for (let k = 0; k < 4; k++) circle(LG.orbit0 + k, LK.orbit, TONE.mid, 1);
  const arc = (r: number, a0: number, a1: number, n: number, tone: number, alpha: number, group: number, kind: number): void => {
    for (let i = 0; i < n; i++) {
      const t0 = a0 + ((a1 - a0) * i) / n;
      const t1 = a0 + ((a1 - a0) * (i + 1)) / n;
      seg([r * Math.cos(t0), r * Math.sin(t0), 0], [r * Math.cos(t1), r * Math.sin(t1), 0], tone, alpha, group, kind);
    }
  };
  arc(HUD.bezel, 0, TAU, 360, TONE.mid, 0.36, LG.bezel, LK.hud);
  seg([-0.025, HUD.notch + 0.025, 0], [0, HUD.notch, 0], TONE.hot, 0.95, LG.bezel, LK.hud);
  seg([0, HUD.notch, 0], [0.025, HUD.notch + 0.025, 0], TONE.hot, 0.95, LG.bezel, LK.hud);
  const top = Math.PI / 2;
  for (let i = 0; i < 120; i++) {
    const a0 = (i / 120) * TAU;
    if (Math.abs(a0 - top) < 0.07) continue;
    arc(HUD.dash, a0, a0 + TAU / 240, 1, TONE.dim, 0.45, LG.bezel, LK.hud);
  }
  for (let i = 0; i < 144; i++) {
    const a = ((i + 0.5) / 144) * TAU;
    if (Math.abs(a - top) < 0.07) continue;
    const p: Vec3 = [HUD.bars * Math.cos(a), HUD.bars * Math.sin(a), 0];
    seg(p, p, TONE.bright, 0.6, LG.bezel, LK.spec, 1);
  }
  for (const a of [0, top, Math.PI, 1.5 * Math.PI]) seg([HUD.cardinal * Math.cos(a), HUD.cardinal * Math.sin(a), 0], [(HUD.cardinal + 0.05) * Math.cos(a), (HUD.cardinal + 0.05) * Math.sin(a), 0], TONE.bright, 0.6, LG.bezel, LK.hud);
  for (let i = 0; i < 180; i++) {
    const a = top + (i / 180) * TAU;
    const big = i % 5 === 0;
    const r1 = HUD.bezel + (big ? 0.03 : 0.015);
    seg([HUD.bezel * Math.cos(a), HUD.bezel * Math.sin(a), 0], [r1 * Math.cos(a), r1 * Math.sin(a), 0], big ? TONE.bright : TONE.mid, big ? 0.75 : 0.32, LG.card, LK.card);
  }
  for (const r of [HUD.arcA, HUD.arcA + 0.012]) arc(r, 0, (72 / 360) * TAU, 72, TONE.bright, 0.5, LG.arcA, LK.hud);
  arc(HUD.arcB, Math.PI, Math.PI + (30 / 360) * TAU, 30, TONE.bright, 0.5, LG.arcB, LK.hud);
  const galaxyFrom = rows.length;
  for (let k = 0; k < 3; k++) circle(LG.galaxy0 + k, LK.plain, TONE.mid, 1);
  const galaxyTo = rows.length;
  const data = new Float32Array(rows.length * LINE_STRIDE);
  rows.forEach((r, i) => data.set(r, i * LINE_STRIDE));
  return { data, count: rows.length, galaxyFrom, galaxyTo };
}

export const HUD_CHARS = ' 0123456789.:-/%ABCDEFGHIJKLMNOPQRSTUVWXYZ';
export const HUD_STRIDE = 8;

export function buildHudText(): { data: Float32Array; count: number } {
  const rows: number[][] = [];
  const text = (s: string, th: number, r: number, size: number, group: number): void => {
    for (let i = 0; i < s.length; i++) {
      const cell = Math.max(0, HUD_CHARS.indexOf(s[i] ?? ' '));
      rows.push([th, r, i - (s.length - 1) / 2, size, cell, group, 0, 0]);
    }
  };
  for (let d = 0; d < 360; d += 30) text(String(d).padStart(3, '0'), Math.PI / 2 + (d / 360) * TAU, HUD.numbers, 0.055, 0);
  text('N', Math.PI / 2, HUD.letters, 0.06, 1);
  text('S', -Math.PI / 2, HUD.letters, 0.06, 1);
  text('E', Math.PI, HUD.letters, 0.06, 1);
  text('W', 0, HUD.letters, 0.06, 1);
  const data = new Float32Array(rows.length * HUD_STRIDE);
  rows.forEach((r, i) => data.set(r, i * HUD_STRIDE));
  return { data, count: rows.length };
}

export const STREAM_WORDS: readonly string[] = [
  'fn()', 'let', 'mov', 'jmp', 'ret', 'nop', 'xor', 'push', 'pop', 'call', 'loop', '=>', '->', '::',
  '&&', '||', 'i++', 'map', 'fold', 'read', 'write', 'sync', 'ack', 'ok', 'if', 'else', 'for', 'while',
  'const', 'yield', 'await', 'async', 'case', 'enum', 'impl', 'type', 'void', 'null', 'true', 'false',
  'new', 'self',
];
export const STREAM_STRIDE = 8;
export const STREAM_COUNT = 260;

export function buildStream(seed = 20261113): { data: Float32Array; count: number } {
  const rnd = seeded(seed);
  const data = new Float32Array(STREAM_COUNT * STREAM_STRIDE);
  for (let i = 0; i < STREAM_COUNT; i++) {
    const cell = Math.floor(rnd() * STREAM_WORDS.length);
    data.set([rnd() * TAU, 0.01 + rnd() * 0.025, rnd(), 0.028 + rnd() * 0.032, cell, 0.3 + rnd() * 0.5, rnd(), 0], i * STREAM_STRIDE);
  }
  return { data, count: STREAM_COUNT };
}
