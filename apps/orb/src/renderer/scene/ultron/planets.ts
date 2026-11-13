import type { CalendarToday } from '../../../shared/ipc-contract.ts';
import { ORBITS, orbitPeriod, type Rgb } from './model.ts';

export type PlanetKey = 'phone' | 'telegram' | 'calendar' | 'x' | 'files' | 'code' | 'browser';

export interface PlanetLook {
  a: Rgb;
  b: Rgb;
  atm: Rgb;
  atmK: number;
  bands: number;
  noise: number;
  ring: boolean;
  spin: number;
}

export interface PlanetDef {
  n: string;
  key: PlanetKey;
  name: string;
  orbit: 0 | 1 | 2 | 3;
  phase: number;
  size: number;
  look: PlanetLook;
}

export const PLANETS: readonly PlanetDef[] = [
  { n: '01', key: 'phone', name: 'PHONE', orbit: 0, phase: 0.15, size: 8.5, look: { a: [0.52, 0.52, 0.55], b: [0.26, 0.25, 0.27], atm: [0.7, 0.7, 0.74], atmK: 0.12, bands: 0, noise: 7, ring: false, spin: 0.3 } },
  { n: '02', key: 'telegram', name: 'TELEGRAM', orbit: 1, phase: 0.62, size: 11, look: { a: [0.08, 0.23, 0.56], b: [0.85, 0.9, 0.95], atm: [0.36, 0.62, 1], atmK: 0.95, bands: 0, noise: 4.5, ring: false, spin: 0.5 } },
  { n: '03', key: 'calendar', name: 'CALENDAR', orbit: 1, phase: 0.12, size: 9.5, look: { a: [0.67, 0.34, 0.18], b: [0.4, 0.2, 0.11], atm: [0.95, 0.58, 0.38], atmK: 0.4, bands: 0, noise: 5.5, ring: false, spin: 0.45 } },
  { n: '04', key: 'x', name: 'X', orbit: 2, phase: 0.38, size: 17, look: { a: [0.81, 0.66, 0.48], b: [0.55, 0.38, 0.25], atm: [0.95, 0.86, 0.72], atmK: 0.45, bands: 17, noise: 3, ring: false, spin: 0.9 } },
  { n: '05', key: 'files', name: 'FILES', orbit: 2, phase: 0.88, size: 12, look: { a: [0.6, 0.81, 0.86], b: [0.43, 0.64, 0.72], atm: [0.55, 0.9, 0.96], atmK: 0.75, bands: 7, noise: 2.5, ring: false, spin: 0.7 } },
  { n: '06', key: 'code', name: 'CODE', orbit: 3, phase: 0.55, size: 14, look: { a: [0.87, 0.76, 0.53], b: [0.69, 0.56, 0.37], atm: [0.96, 0.88, 0.7], atmK: 0.4, bands: 13, noise: 3, ring: true, spin: 0.8 } },
  { n: '07', key: 'browser', name: 'BROWSER', orbit: 3, phase: 0.05, size: 12.5, look: { a: [0.2, 0.33, 0.79], b: [0.13, 0.21, 0.6], atm: [0.42, 0.6, 1], atmK: 0.85, bands: 9, noise: 2.5, ring: false, spin: 0.6 } },
];

export const NO_DATA = 'NO DATA';

export interface PlanetSources {
  calendar: CalendarToday | null;
  linkUp: boolean;
}

export interface PlanetStatus {
  text: string;
  real: boolean;
}

export function planetStatus(key: PlanetKey, src: PlanetSources): PlanetStatus {
  if (key === 'calendar' && src.calendar) {
    const c = src.calendar;
    if (!c.connected) return { text: 'NOT CONNECTED', real: true };
    const n = c.events.length;
    const stale = c.stale || !src.linkUp ? ' · STALE' : '';
    return { text: `CONNECTED · ${n} TODAY${stale}`, real: true };
  }
  return { text: NO_DATA, real: false };
}

export function nameLine(p: PlanetDef): string {
  return `PLANET ${p.n} · ${p.name}`;
}

export function orbitLine(p: PlanetDef): string {
  const a = ORBITS[p.orbit];
  return `ORBIT ${a.toFixed(2)} R · ${Math.round(orbitPeriod(a))} S`;
}
