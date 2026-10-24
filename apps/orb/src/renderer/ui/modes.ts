import type { OrbMode } from '../../shared/ipc-contract.ts';
import { createStore } from '../state/store.ts';

export const MODE_CYCLE: Readonly<Record<OrbMode, OrbMode>> = { full: 'ambient', ambient: 'compact', compact: 'full' };

export const modeStore = createStore<OrbMode>('full');

export interface PaletteState {
  open: boolean;
  query: string;
  kept: string;
  opened: number;
}

export const paletteStore = createStore<PaletteState>({ open: false, query: '', kept: '', opened: 0 });

export function openPalette(report: (line: string) => void, via: string): void {
  const p = paletteStore.get();
  if (p.open) return;
  paletteStore.set({ open: true, query: p.kept, kept: '', opened: p.opened + 1 });
  report(`PALETTE open via=${via}${p.kept ? ` (his ${p.kept.length} typed chars restored)` : ''}`);
}

export function closePalette(report: (line: string) => void, why: string): void {
  const p = paletteStore.get();
  if (!p.open) return;
  paletteStore.set({ open: false, query: '', kept: why === 'card' ? p.query : '', opened: p.opened });
  report(`PALETTE closed (${why})${why === 'card' && p.query ? `, his ${p.query.length} typed chars kept for the next Ctrl K` : ''}`);
}

export function setPaletteQuery(query: string): void {
  const p = paletteStore.get();
  if (p.open) paletteStore.set({ ...p, query });
}

export function setMode(next: OrbMode, report: (line: string) => void, via: string): void {
  const prev = modeStore.get();
  if (prev === next) return;
  if (next === 'compact') closePalette(report, 'compact');
  const el = document.activeElement;
  if (next !== 'full' && el instanceof HTMLTextAreaElement && el.dataset['place'] === 'dock') el.blur();
  modeStore.set(next);
  report(`MODE ${prev} -> ${next} via=${via}`);
}

export function cycleMode(report: (line: string) => void, via: string): void {
  setMode(MODE_CYCLE[modeStore.get()], report, via);
}
