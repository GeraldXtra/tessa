export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface PlanetOnScreen {
  x: number;
  y: number;
  r: number;
  vis: number;
  front: boolean;
}

export interface Slot {
  planet: number;
  side: -1 | 1;
  x: number;
  y: number;
  lostAt: number;
}

export const CALL_W = 196;
export const CALL_H = 50;
export const CALL_GAP = 34;
export const CALL_GRACE_MS = 400;

export function boxOf(p: PlanetOnScreen, side: -1 | 1): Box {
  const x0 = side < 0 ? p.x - p.r - CALL_GAP - CALL_W : p.x + p.r + CALL_GAP;
  const y0 = p.y - CALL_H / 2;
  return { x0, y0, x1: x0 + CALL_W, y1: y0 + CALL_H };
}

function inside(b: Box, stage: Box): boolean {
  return b.x0 >= stage.x0 && b.x1 <= stage.x1 && b.y0 >= stage.y0 && b.y1 <= stage.y1;
}

function hitsCircle(b: Box, cx: number, cy: number, r: number): boolean {
  const nx = Math.max(b.x0, Math.min(cx, b.x1));
  const ny = Math.max(b.y0, Math.min(cy, b.y1));
  return (nx - cx) ** 2 + (ny - cy) ** 2 < r * r;
}

function overlaps(a: Box, b: Box): boolean {
  return a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
}

export interface Disc {
  cx: number;
  cy: number;
  r: number;
}

export function fits(p: PlanetOnScreen, side: -1 | 1, stage: Box, orb: Disc, other: Box | null): boolean {
  if (p.vis < 0.6) return false;
  if (p.x < stage.x0 || p.x > stage.x1 || p.y < stage.y0 || p.y > stage.y1) return false;
  if ((p.x - orb.cx) ** 2 + (p.y - orb.cy) ** 2 < (orb.r + p.r + 6) ** 2) return false;
  const b = boxOf(p, side);
  if (!inside(b, stage)) return false;
  if (hitsCircle(b, orb.cx, orb.cy, orb.r + 10)) return false;
  if (other && overlaps(b, other)) return false;
  return true;
}

export function placeCallouts(prev: readonly (Slot | null)[], planets: readonly PlanetOnScreen[], stage: Box, orb: Disc, now: number): (Slot | null)[] {
  const out: (Slot | null)[] = [null, null];
  const sides: (-1 | 1)[] = [-1, 1];
  for (let k = 0; k < 2; k++) {
    const side = sides[k] as -1 | 1;
    const old = prev[k] ?? null;
    const otherSlot = k === 1 ? out[0] : null;
    const otherBox = otherSlot ? boxOf(planets[otherSlot.planet] as PlanetOnScreen, otherSlot.side) : null;
    if (old) {
      const p = planets[old.planet];
      if (p && fits(p, side, stage, orb, otherBox)) {
        out[k] = { planet: old.planet, side, x: p.x, y: p.y, lostAt: 0 };
        continue;
      }
      if (p && old.lostAt === 0) {
        out[k] = { ...old, lostAt: now };
        continue;
      }
      if (p && now - old.lostAt < CALL_GRACE_MS) {
        out[k] = old;
        continue;
      }
    }
    let best = -1;
    let bestScore = -1e9;
    for (let i = 0; i < planets.length; i++) {
      if (k === 1 && out[0] && out[0].planet === i) continue;
      const p = planets[i] as PlanetOnScreen;
      if ((p.x - orb.cx) * side < 0) continue;
      if (!fits(p, side, stage, orb, otherBox)) continue;
      const score = (p.front ? 1 : 0.4) - Math.abs(p.y - orb.cy) / Math.max(1, orb.r * 4);
      if (score > bestScore) {
        bestScore = score;
        best = i;
      }
    }
    if (best >= 0) {
      const p = planets[best] as PlanetOnScreen;
      out[k] = { planet: best, side, x: p.x, y: p.y, lostAt: 0 };
    }
  }
  return out;
}
