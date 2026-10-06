import { RING_SLOTS, TAU } from './plasma-model.ts';

export interface FallbackView {
  w: number;
  h: number;
  dpr: number;
  transparent: boolean;
  cx: number;
  cy: number;
  r: number;
  baseR: number;
  energy: number;
  rim: number;
  core: number;
  hollow: number;
  sweepAmt: number;
  sweepPhase: number;
  spark: number;
  rot: number;
  flow: number;
  tight: number;
  tightPhase: number;
  tick: number;
  heart: number;
  flare: number;
  dim: number;
  muted: number;
  night: number;
  load: number;
  deep: Float32Array;
  mid: Float32Array;
  hot: Float32Array;
  bg: Float32Array;
  tickCol: Float32Array;
  flareCol: Float32Array;
  amber: Float32Array;
  ringA: Float32Array;
}

function channel(v: number): number {
  return Math.max(0, Math.min(255, Math.round(v * 255)));
}

function paint(c: Float32Array, k: number, grey: number, a: number): string {
  const r = c[0] ?? 0;
  const g = c[1] ?? 0;
  const b = c[2] ?? 0;
  const l = 0.299 * r + 0.587 * g + 0.114 * b;
  return `rgba(${channel((r + (l - r) * grey) * k)},${channel((g + (l - g) * grey) * k)},${channel((b + (l - b) * grey) * k)},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
}

function mixInto(out: Float32Array, a: Float32Array, b: Float32Array, t: number): Float32Array {
  for (let i = 0; i < 3; i++) out[i] = (a[i] ?? 0) + ((b[i] ?? 0) - (a[i] ?? 0)) * t;
  return out;
}

const scratch = new Float32Array(3);
const HALO_SPAN = 3;
const HALO_STOPS = 10;

export function drawFallback(ctx: CanvasRenderingContext2D, v: FallbackView): void {
  const grey = Math.min(0.9, 0.8 * v.dim + 0.35 * v.muted);
  const lift = (1 - 0.8 * v.dim) * (1 - 0.33 * v.night);
  const { cx, cy, r } = v;
  ctx.setTransform(v.dpr, 0, 0, v.dpr, 0, 0);
  if (v.transparent) {
    ctx.clearRect(0, 0, v.w, v.h);
  } else {
    ctx.fillStyle = paint(v.bg, lift, grey, 1);
    ctx.fillRect(0, 0, v.w, v.h);
  }

  const near = 3.8 - 1.6 * v.load;
  const nearA = 0.15 + 0.07 * v.energy + 0.42 * v.load;
  const farA = 0.03 + 0.1 * v.load;
  const haloK = (1 - 0.5 * v.night) * (1 + 0.5 * v.heart) * lift;
  const halo = ctx.createRadialGradient(cx, cy, r, cx, cy, r * (1 + HALO_SPAN));
  for (let i = 0; i <= HALO_STOPS; i++) {
    const t = i / HALO_STOPS;
    const out = t * HALO_SPAN;
    halo.addColorStop(t, paint(v.mid, 1, grey, (Math.exp(-out * near) * nearA + Math.exp(-out * 0.9) * farA) * haloK));
  }
  ctx.fillStyle = halo;
  ctx.fillRect(0, 0, v.w, v.h);

  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, TAU);
  ctx.clip();
  const heat = Math.max(0, v.energy * (0.55 + 0.35 * v.core)) * lift;
  const centre = 1 - 0.88 * v.hollow;
  const disc = ctx.createRadialGradient(cx - 0.18 * r, cy - 0.22 * r, r * 0.04, cx, cy, r);
  disc.addColorStop(0, paint(v.hot, heat * centre, grey, 1));
  disc.addColorStop(0.5, paint(v.mid, heat * (0.75 * centre + 0.12), grey, 1));
  disc.addColorStop(0.86, paint(v.mid, lift * (0.45 + 0.35 * v.hollow), grey, 1));
  disc.addColorStop(1, paint(v.hot, lift * Math.min(1.2, 0.55 + 0.4 * v.rim), grey, 1));
  ctx.fillStyle = disc;
  ctx.fillRect(cx - r, cy - r, 2 * r, 2 * r);

  const flick = 1 + v.spark * 0.25 * Math.sin(v.flow * 7.3) * Math.sin(v.flow * 3.7 + 1.3);
  for (let k = 0; k < 3; k++) {
    const ang = v.rot * 2.2 + (k * TAU) / 3;
    const bx = cx + Math.cos(ang) * r * 0.42;
    const by = cy + Math.sin(ang) * r * 0.3;
    const blob = ctx.createRadialGradient(bx, by, 0, bx, by, r * 0.42);
    blob.addColorStop(0, paint(v.hot, lift * flick, grey, 0.3 * (1 - v.hollow) * Math.min(1.4, v.energy)));
    blob.addColorStop(1, paint(v.hot, lift, grey, 0));
    ctx.fillStyle = blob;
    ctx.fillRect(cx - r, cy - r, 2 * r, 2 * r);
  }

  if (v.sweepAmt > 0.01) {
    const x = cx + r * (2 * (v.sweepPhase / Math.PI) - 1);
    const band = ctx.createLinearGradient(x - r * 0.18, 0, x + r * 0.18, 0);
    band.addColorStop(0, paint(v.hot, lift, grey, 0));
    band.addColorStop(0.5, paint(v.hot, lift, grey, 0.5 * v.sweepAmt));
    band.addColorStop(1, paint(v.hot, lift, grey, 0));
    ctx.fillStyle = band;
    ctx.fillRect(x - r * 0.18, cy - r, r * 0.36, 2 * r);
  }

  if (v.tight > 0.01) {
    ctx.lineWidth = 1.5;
    for (let k = 0; k < 3; k++) {
      const f = 1 - ((v.tightPhase * 6.5) / TAU + k / 3 - Math.floor((v.tightPhase * 6.5) / TAU + k / 3));
      ctx.strokeStyle = paint(v.hot, lift, grey, 0.22 * v.tight * f);
      ctx.beginPath();
      ctx.arc(cx, cy, r * f, 0, TAU);
      ctx.stroke();
    }
  }
  ctx.restore();

  ctx.lineWidth = 2;
  ctx.strokeStyle = paint(v.hot, lift, grey, Math.min(1, 0.35 + 0.4 * v.rim + 0.5 * v.heart));
  ctx.beginPath();
  ctx.arc(cx, cy, r - 1, 0, TAU);
  ctx.stroke();
  if (v.tick > 0.01) {
    ctx.lineWidth = 3;
    ctx.strokeStyle = paint(v.tickCol, lift, grey, 0.9 * v.tick);
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, TAU);
    ctx.stroke();
  }
  if (v.flare > 0.01) {
    ctx.lineWidth = 6;
    ctx.strokeStyle = paint(v.flareCol, lift, grey, Math.min(1, v.flare));
    ctx.beginPath();
    ctx.arc(cx, cy, r + 2, 0, TAU);
    ctx.stroke();
  }

  const R = v.baseR;
  for (let k = 0; k < RING_SLOTS.length; k++) {
    const slot = RING_SLOTS[k];
    const alpha = v.ringA[k * 4 + 2] ?? 0;
    if (!slot || alpha < 0.002) continue;
    const prog = v.ringA[k * 4 + 1] ?? 0;
    const amb = v.ringA[k * 4 + 3] ?? 0;
    const col = mixInto(scratch, v.mid, v.amber, amb);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(slot.c);
    ctx.scale(1, Math.sin(slot.phi));
    ctx.lineWidth = 1.2 / Math.max(0.2, Math.sin(slot.phi));
    ctx.strokeStyle = paint(col, lift, grey, 0.2 * alpha);
    ctx.beginPath();
    ctx.arc(0, 0, slot.r * R, 0, TAU);
    ctx.stroke();
    ctx.strokeStyle = paint(col, lift, grey, (prog >= 0.999 ? 1 : 0.75) * alpha);
    ctx.beginPath();
    ctx.arc(0, 0, slot.r * R, 0, TAU * Math.max(0.001, prog));
    ctx.stroke();
    ctx.restore();
  }

  if (v.hollow > 0.01) {
    ctx.lineWidth = 2;
    ctx.strokeStyle = paint(mixInto(scratch, v.mid, v.hot, 0.35), lift, grey, 0.9 * v.hollow);
    ctx.beginPath();
    ctx.arc(cx, cy, R * 1.17, 0, TAU);
    ctx.stroke();
  }
  if (v.muted > 0.01) {
    const seg = (TAU * R * 1.09) / 64;
    ctx.setLineDash([seg * 0.55, seg * 0.45]);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = paint(v.mid, lift, 0, 0.6 * v.muted);
    ctx.beginPath();
    ctx.arc(cx, cy, R * 1.09, 0, TAU);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}
