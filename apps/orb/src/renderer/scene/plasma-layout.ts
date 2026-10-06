import { RIBBON_HEIGHT_R, RIBBON_TOP_R, RIBBON_WIDTH_R } from './plasma-model.ts';

export interface Bands {
  top: number;
  bottom: number;
  readout: number;
}

export const STAGE_BANDS: Bands = { top: 44, bottom: 8, readout: 62 };
export const RADIUS_FLOOR = 40;
export const READOUT_GAP = 6;
export const READOUT_MAX_W = 560;
export const EDGE_MARGIN = 12;
export const CALENDAR_MARGIN = 12;
export const DRAWER_CLEARANCE = 28;
export const CARD_RING_EXTENT = 1.2;
export const CARD_MARGIN = 12;

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface PlasmaLayoutInput {
  width: number;
  height: number;
  calendar: Box | null;
  drawerLeft: number | null;
  cardLeft: number | null;
  bands?: Bands;
}

export interface PlasmaLayout {
  cx: number;
  cy: number;
  r: number;
  naturalR: number;
  fit: number;
  offsetX: number;
  offsetY: number;
  ribbonTop: number;
  ribbonBottom: number;
  ribbonLeft: number;
  ribbonRight: number;
  readoutTop: number;
  readoutWidth: number;
  calendarClash: boolean;
}

function avail(h: number, b: Bands): number {
  return h - b.top - b.bottom - b.readout - READOUT_GAP;
}

export function naturalRadius(w: number, h: number, bands: Bands = STAGE_BANDS): number {
  return Math.max(RADIUS_FLOOR, Math.min(w / 3.4, avail(h, bands) / 2.67));
}

function centreY(r: number, h: number, b: Bands): number {
  return b.top + 1.05 * r + Math.max(0, (avail(h, b) - 2.67 * r) / 2);
}

function bandHits(cy: number, r: number, cal: Box | null, b: Bands): boolean {
  if (!cal) return false;
  const top = cy + RIBBON_TOP_R * r;
  const bottom = cy + (RIBBON_TOP_R + RIBBON_HEIGHT_R) * r + READOUT_GAP + b.readout;
  return bottom > cal.top - CALENDAR_MARGIN && top < cal.bottom + CALENDAR_MARGIN;
}

function discHits(cy: number, r: number, cal: Box | null): boolean {
  if (!cal) return false;
  return cy + r > cal.top - CALENDAR_MARGIN && cy - r < cal.bottom + CALENDAR_MARGIN;
}

interface Limit {
  x: number;
  k: number;
}

function limitsFor(input: PlasmaLayoutInput): Limit[] {
  const out: Limit[] = [{ x: input.width - EDGE_MARGIN, k: 1 }];
  if (input.drawerLeft !== null) out.push({ x: input.drawerLeft - DRAWER_CLEARANCE, k: 1 });
  if (input.cardLeft !== null) out.push({ x: input.cardLeft - CARD_MARGIN, k: CARD_RING_EXTENT });
  return out;
}

function leftNeed(cy: number, r: number, input: PlasmaLayoutInput, b: Bands): { x: number; k: number } {
  const cal = input.calendar;
  const half = RIBBON_WIDTH_R / 2;
  if (cal && bandHits(cy, r, cal, b)) return { x: cal.right + CALENDAR_MARGIN, k: half };
  if (cal && discHits(cy, r, cal)) return { x: cal.right + CALENDAR_MARGIN, k: 1 };
  return { x: EDGE_MARGIN, k: 1 };
}

export function plasmaLayout(input: PlasmaLayoutInput): PlasmaLayout {
  const b = input.bands ?? STAGE_BANDS;
  const w = Math.max(1, input.width);
  const h = Math.max(1, input.height);
  const naturalR = naturalRadius(w, h, b);
  const limits = limitsFor(input);

  let r = naturalR;
  let cy = centreY(r, h, b);
  let left = leftNeed(cy, r, input, b);
  for (let pass = 0; pass < 3; pass++) {
    let fitR = r;
    for (const lim of limits) {
      const room = (lim.x - left.x) / (lim.k + left.k);
      if (room < fitR) fitR = room;
    }
    fitR = Math.max(RADIUS_FLOOR, fitR);
    if (fitR >= r - 0.01) break;
    r = fitR;
    cy = centreY(r, h, b);
    left = leftNeed(cy, r, input, b);
  }

  let rightMax = Number.POSITIVE_INFINITY;
  for (const lim of limits) rightMax = Math.min(rightMax, lim.x - lim.k * r);
  const leftMin = left.x + left.k * r;
  const cx = Math.max(leftMin, Math.min(w / 2, rightMax));

  const ribbonHalf = (RIBBON_WIDTH_R / 2) * r;
  const readoutWidth = Math.min(RIBBON_WIDTH_R * r, READOUT_MAX_W, w - 2 * EDGE_MARGIN);
  return {
    cx,
    cy,
    r,
    naturalR,
    fit: r / naturalR,
    offsetX: -2 * (cx - w / 2),
    offsetY: -2 * (cy - h / 2),
    ribbonTop: cy + RIBBON_TOP_R * r,
    ribbonBottom: cy + (RIBBON_TOP_R + RIBBON_HEIGHT_R) * r,
    ribbonLeft: cx - ribbonHalf,
    ribbonRight: cx + ribbonHalf,
    readoutTop: cy + (RIBBON_TOP_R + RIBBON_HEIGHT_R) * r + READOUT_GAP,
    readoutWidth,
    calendarClash: bandHits(cy, r, input.calendar, b) && cx - ribbonHalf < (input.calendar?.right ?? 0) + CALENDAR_MARGIN - 0.5,
  };
}
