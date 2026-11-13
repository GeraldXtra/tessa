import type { AgentState } from '@tessa/protocol';

export type Rgb = [number, number, number];

export const TAU = Math.PI * 2;
export const UNIT_PER_R = 0.888;
export const ORB_R = 0.84;
export const ORB_CAM = 4.6;
export const SCENE_CAM = 11;
export const GALAXY_CAM = 12;
export const PITCH = 0.3;
export const ORBITS = [1.5, 1.86, 2.26, 2.66] as const;
export const ORBIT_TILT = [0.02, -0.015, 0.025, -0.03] as const;
export const GALAXY_RINGS = [1.15, 1.6, 2.05] as const;
export const HUD_REACH = 1.44;
export const BOOT_S = 2.6;
export const VOICE_STALE_MS = 500;
export const WORKING_ENTER_MS = 900;
export const WORKING_LEAVE_MS = 1200;
export const FREEZE_AT = 0.995;

export function orbitPeriod(a: number): number {
  return 34 * Math.pow(a, 1.5);
}

export function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

export function approach(cur: number, target: number, dt: number, tau: number): number {
  if (tau <= 0) return target;
  return cur + (target - cur) * (1 - Math.exp(-dt / tau));
}

export function smooth(a: number, b: number, x: number): number {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
}

export interface Mix {
  li: number;
  th: number;
  sp: number;
  bl: number;
  ga: number;
}

export const MIX_TAU: Readonly<Mix> = { li: 0.2, th: 0.2, sp: 0.2, bl: 0.3, ga: 0.42 };

export function targetMix(state: AgentState, galaxy: boolean): Mix {
  return {
    li: state === 'listening' ? 1 : 0,
    th: state === 'thinking' ? 1 : state === 'working' ? 0.45 : 0,
    sp: state === 'speaking' ? 1 : 0,
    bl: state === 'blocked' ? 1 : 0,
    ga: galaxy && state === 'working' ? 1 : 0,
  };
}

export function stepMix(cur: Mix, target: Mix, dt: number, reduced: boolean): void {
  const k = reduced ? 1e6 : dt;
  cur.li = approach(cur.li, target.li, k, MIX_TAU.li);
  cur.th = approach(cur.th, target.th, k, MIX_TAU.th);
  cur.sp = approach(cur.sp, target.sp, k, MIX_TAU.sp);
  cur.bl = approach(cur.bl, target.bl, k, MIX_TAU.bl);
  cur.ga = approach(cur.ga, target.ga, k, MIX_TAU.ga);
  if (target.bl >= 1 && cur.bl >= FREEZE_AT) cur.bl = 1;
}

export function pace(mix: Mix): number {
  return mix.bl >= 1 ? 0 : 1 - mix.bl;
}

export function settledStill(mix: Mix, target: Mix): boolean {
  return target.bl >= 1 && mix.bl >= 1 && Math.abs(mix.li - target.li) < 0.004 && Math.abs(mix.th - target.th) < 0.004 && Math.abs(mix.sp - target.sp) < 0.004 && Math.abs(mix.ga - target.ga) < 0.004;
}

export interface Clocks {
  clock: number;
  outer: number;
  spin: number;
  flow: number;
  orbit: number;
  wind: number;
  pulse: number;
  hudA: number;
  hudB: number;
  scan: number;
  dust: number;
  gal: number;
  gspin: number;
  cam: number;
  spec: number;
  glyph: number;
}

export function newClocks(): Clocks {
  return { clock: 0, outer: 0, spin: 0, flow: 0, orbit: 0, wind: 0, pulse: 0, hudA: 0, hudB: 0, scan: 0, dust: 0, gal: 0, gspin: 0, cam: 0, spec: 0, glyph: 0 };
}

export function advanceClocks(k: Clocks, dt: number, m: Mix, voice: number, reduced: boolean): void {
  const p = pace(m) * (reduced ? 0.35 : 1);
  if (p <= 0 || dt <= 0) return;
  const d = dt * p;
  k.clock += d;
  k.outer += d * 0.09;
  k.spin += d * (0.1 + 0.25 * m.th);
  k.flow += d * (1 + 2.2 * m.th + 0.8 * m.sp * voice);
  k.orbit += d * (1 + 0.6 * m.th);
  k.wind += d * (1 + 1.5 * m.th);
  k.pulse = (k.pulse + d * (1 + 1.2 * m.th + 0.8 * m.sp * voice)) % 100;
  k.hudA += d * (0.12 + 0.3 * m.th);
  k.hudB += d * (0.2 + 0.4 * m.th);
  k.scan += d * (1 + 1.5 * m.li);
  k.dust += d * 0.012;
  k.gal += d * (1 + 0.8 * m.th);
  k.gspin += d * 0.012;
  k.cam += d;
  k.spec = (k.spec + d) % 1000;
  k.glyph = (k.glyph + d) % 1000;
}

export function voiceTarget(state: AgentState, level: number, ageMs: number, reduced: boolean): number {
  if (state !== 'speaking' || reduced || !(ageMs <= VOICE_STALE_MS)) return 0;
  return Number.isFinite(level) ? clamp01(level) : 0;
}

export function voiceStep(cur: number, target: number, dt: number): number {
  return approach(cur, target, dt, target > cur ? 0.05 : 0.18);
}

export class WorkingDwell {
  private since = -1;
  private leftAt = -1;
  private on = false;

  update(state: AgentState, nowMs: number): boolean {
    if (state === 'working') {
      this.leftAt = -1;
      if (this.since < 0) this.since = nowMs;
      if (!this.on && nowMs - this.since >= WORKING_ENTER_MS) this.on = true;
    } else {
      this.since = -1;
      if (this.on) {
        if (this.leftAt < 0) this.leftAt = nowMs;
        if (nowMs - this.leftAt >= WORKING_LEAVE_MS) {
          this.on = false;
          this.leftAt = -1;
        }
      }
    }
    return this.on;
  }

  pending(nowMs: number): boolean {
    return (this.since >= 0 && !this.on) || (this.on && this.leftAt >= 0 && nowMs - this.leftAt < WORKING_LEAVE_MS);
  }
}

export function parseColour(value: string): Rgb | null {
  const v = value.trim();
  if (v.length === 7 && v[0] === '#') {
    const n = Number.parseInt(v.slice(1), 16);
    if (!Number.isFinite(n)) return null;
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  const m = /^rgba?\(([^)]+)\)$/.exec(v);
  if (!m) return null;
  const parts = (m[1] ?? '').split(/[\s,/]+/).filter(Boolean).map((p) => Number.parseFloat(p));
  if (parts.length < 3 || parts.slice(0, 3).some((p) => !Number.isFinite(p))) return null;
  return [(parts[0] ?? 0) / 255, (parts[1] ?? 0) / 255, (parts[2] ?? 0) / 255];
}

export function toLinear(c: number): number {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

export function linRgb(c: Rgb): Rgb {
  return [toLinear(c[0]), toLinear(c[1]), toLinear(c[2])];
}

export function mixRgb(a: Rgb, b: Rgb, t: number): Rgb {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

export interface ThemeSteps {
  core: Rgb;
  body: Rgb;
  idle: Rgb;
  deep: Rgb;
}

export interface ScenePalette {
  faint: Rgb;
  dim: Rgb;
  mid: Rgb;
  bright: Rgb;
  hot: Rgb;
  tintA: Rgb;
  tintB: Rgb;
  grade: Rgb;
}

export function luminance(c: Rgb): number {
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

export function sceneLift(linearBright: Rgb): number {
  const l = luminance(linearBright);
  return Math.min(1.7, Math.max(1, 0.48 / Math.max(l, 1e-4)));
}

export function scenePalette(t: ThemeSteps): ScenePalette {
  const raw = linRgb(t.body);
  const lift = sceneLift(raw);
  const scale = (c: Rgb, k: number): Rgb => [c[0] * k, c[1] * k, c[2] * k];
  const bright = scale(raw, lift);
  const hot = scale(linRgb(t.core), Math.sqrt(lift));
  const mid = scale(linRgb(t.idle), lift);
  const faint = scale(linRgb(t.deep), lift);
  const dim = mixRgb(mid, faint, 0.5);
  const top = Math.max(raw[0], raw[1], raw[2], 1e-4);
  const grade: Rgb = [
    0.85 + 0.2 * Math.sqrt(raw[0] / top),
    0.85 + 0.2 * Math.sqrt(raw[1] / top),
    0.85 + 0.2 * Math.sqrt(raw[2] / top),
  ];
  return { faint, dim, mid, bright, hot, tintA: [...t.body] as Rgb, tintB: [...t.core] as Rgb, grade };
}

export function blendPalette(a: ScenePalette, b: ScenePalette, k: number): ScenePalette {
  return {
    faint: mixRgb(a.faint, b.faint, k),
    dim: mixRgb(a.dim, b.dim, k),
    mid: mixRgb(a.mid, b.mid, k),
    bright: mixRgb(a.bright, b.bright, k),
    hot: mixRgb(a.hot, b.hot, k),
    tintA: mixRgb(a.tintA, b.tintA, k),
    tintB: mixRgb(a.tintB, b.tintB, k),
    grade: mixRgb(a.grade, b.grade, k),
  };
}
