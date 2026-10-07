export const TOP = 32;
export const RAILS = 104;
export const MARGIN = 16;
export const SLOT_W = 372;
export const SLOT_W_NARROW = 320;
export const NARROW_W = 1200;
export const CAL_FULL = { w: 300, h: 268 } as const;
export const CAL_COMPACT = { w: 220, h: 112 } as const;
export const RING_TIP = 1.24;
export const MIN_DIAMETER = 549;

export type CalMode = 'full' | 'compact' | 'hidden';

export interface Layout {
  W: number;
  H: number;
  slot: boolean;
  R: number;
  cx: number;
  cy: number;
  cal: CalMode;
  dw: number;
  lcW: number;
  colTop: number;
  colH: number;
  colShown: boolean;
  traceShown: boolean;
  bandX: number;
  bandW: number;
  narrow: boolean;
  guarded: boolean;
  stalled: boolean;
}

const clamp = (x: number, a: number, b: number): number => Math.min(b, Math.max(a, x));

export function computeLayout(width: number, height: number, slot: boolean): Layout {
  const W = Math.max(320, width);
  const H = Math.max(240, height);
  const Hs = H - TOP;
  const dw = slot ? (W >= NARROW_W ? SLOT_W : SLOT_W_NARROW) : 0;
  const right = W - RAILS - dw - 12;
  const R0 = (slot ? 0.34 : 0.4) * Hs;
  const Rmin = (slot ? 0.32 : 0.38) * Hs;
  const guarded = !slot && 2 * R0 >= MIN_DIAMETER;
  const Rfloor = guarded ? Math.max(Rmin, MIN_DIAMETER / 2) : Rmin;
  let R = R0;
  let cal: CalMode = W >= NARROW_W ? 'full' : 'compact';
  if (slot && W < NARROW_W) cal = 'hidden';
  const cy = TOP + Hs / 2;
  let cx = right / 2;
  let stalled = false;
  for (let guard = 0; guard < 200; guard++) {
    const cw = cal === 'full' ? CAL_FULL.w : cal === 'compact' ? CAL_COMPACT.w : 0;
    const ch = cal === 'full' ? CAL_FULL.h : cal === 'compact' ? CAL_COMPACT.h : 0;
    let minCx = RING_TIP * R + MARGIN;
    if (cw) {
      const x1 = MARGIN + cw;
      const y0 = H - MARGIN - ch;
      const dy = y0 - cy;
      const rr = R + 14;
      const b = 0.64 * R;
      if (dy < rr) minCx = Math.max(minCx, x1 + Math.sqrt(rr * rr - Math.max(dy, 0) ** 2));
      if (dy < b) minCx = Math.max(minCx, x1 + 10 + RING_TIP * R * Math.sqrt(1 - (Math.max(dy, 0) / b) ** 2));
      if (y0 < cy + R + 64) minCx = Math.max(minCx, x1 + 12 + 0.95 * R);
    }
    const maxCx = right - RING_TIP * R;
    if (minCx <= maxCx) {
      cx = clamp((W - RAILS - dw) / 2, minCx, maxCx);
      break;
    }
    if (R > Rfloor) R = Math.max(Rfloor, R * 0.985);
    else if (cal === 'full') {
      cal = 'compact';
      R = R0;
    } else if (cal === 'compact') {
      cal = 'hidden';
      R = R0;
    } else if (guarded) {
      stalled = true;
      cx = (W - RAILS - dw) / 2;
      break;
    } else R *= 0.96;
  }
  const lcW = Math.max(0, Math.min(300, cx - RING_TIP * R - MARGIN - 12));
  const bandX = cx + RING_TIP * R + 16;
  const avail = W - RAILS - 14 - bandX;
  const bandW = !slot && avail >= 200 ? Math.min(280, avail) : 0;
  const calTop = cal === 'full' ? H - MARGIN - CAL_FULL.h : cal === 'compact' ? H - MARGIN - CAL_COMPACT.h : H - MARGIN;
  const colTop = TOP + 12;
  const colH = Math.max(0, calTop - 12 - TOP - 12);
  const colShown = lcW >= 140;
  return {
    W,
    H,
    slot,
    R,
    cx,
    cy,
    cal,
    dw,
    lcW,
    colTop,
    colH,
    colShown,
    traceShown: colShown && lcW >= 200 && colH >= 330,
    bandX,
    bandW,
    narrow: W < NARROW_W,
    guarded,
    stalled,
  };
}
