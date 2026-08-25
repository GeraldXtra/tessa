/**
 * Window geometry persistence — %LOCALAPPDATA%\Tessa\orb-window.json.
 *
 * Sits beside runtime.json but is unrelated to it: no secret, no ACL
 * requirement, and losing it costs nothing but a maximized window. Every read
 * is defensive, because the one failure that matters here is opening the window
 * somewhere the owner cannot reach it.
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

import { screen } from 'electron';

export interface SavedWindowState {
  width: number;
  height: number;
  x: number;
  y: number;
  isMaximized: boolean;
}

/** Below this the collapsed layout stops being honest. CONTENT pixels. */
export const MIN_WIDTH = 900;
export const MIN_HEIGHT = 600;

/** Writes are coalesced: a drag emits a resize event per frame. */
export const SAVE_DEBOUNCE_MS = 400;

function statePath(): string {
  const base =
    process.env['LOCALAPPDATA'] ??
    (process.platform === 'win32'
      ? join(homedir(), 'AppData', 'Local')
      : join(homedir(), '.local', 'share'));
  return join(base, 'Tessa', 'orb-window.json');
}

export { statePath as windowStatePath };

function isFiniteInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * Is this rectangle reachable on a display that is connected RIGHT NOW?
 *
 * The case this exists for: the owner docks a second monitor, moves the Orb
 * onto it, undocks, and relaunches. The saved x would place the window on a
 * display that no longer exists, and a frameless window with no taskbar entry
 * for its title bar is genuinely hard to recover from. Requiring a real overlap
 * — not merely a corner touching — means a window half-off the edge also gets
 * discarded rather than restored barely-visible.
 */
export function isReachable(state: SavedWindowState): boolean {
  const displays = screen.getAllDisplays();
  const MIN_VISIBLE = 200;

  return displays.some((display) => {
    const area = display.workArea;
    const overlapX = Math.min(state.x + state.width, area.x + area.width) - Math.max(state.x, area.x);
    const overlapY =
      Math.min(state.y + state.height, area.y + area.height) - Math.max(state.y, area.y);
    return overlapX >= MIN_VISIBLE && overlapY >= MIN_VISIBLE;
  });
}

export type LoadResult =
  | { ok: true; state: SavedWindowState }
  | { ok: false; reason: 'absent' | 'malformed' | 'offscreen'; detail: string };

export function loadWindowState(): LoadResult {
  let raw: string;
  try {
    raw = readFileSync(statePath(), 'utf8');
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === 'ENOENT') return { ok: false, reason: 'absent', detail: 'no saved window state' };
    return { ok: false, reason: 'malformed', detail: `cannot read window state (${code})` };
  }

  let parsed: unknown;
  try {
    // Same BOM tolerance as runtime-file.ts: anything that hand-edits this file
    // on Windows is likely to add one.
    parsed = JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw);
  } catch {
    return { ok: false, reason: 'malformed', detail: 'window state is not valid JSON' };
  }

  if (typeof parsed !== 'object' || parsed === null) {
    return { ok: false, reason: 'malformed', detail: 'window state is not an object' };
  }
  const d = parsed as Record<string, unknown>;

  if (
    !isFiniteInt(d['width']) ||
    !isFiniteInt(d['height']) ||
    !isFiniteInt(d['x']) ||
    !isFiniteInt(d['y']) ||
    typeof d['isMaximized'] !== 'boolean'
  ) {
    return { ok: false, reason: 'malformed', detail: 'window state has missing or non-numeric fields' };
  }

  const state: SavedWindowState = {
    // Clamp up front so a saved 100x100 cannot slip past the minimum.
    width: Math.max(MIN_WIDTH, Math.round(d['width'])),
    height: Math.max(MIN_HEIGHT, Math.round(d['height'])),
    x: Math.round(d['x']),
    y: Math.round(d['y']),
    isMaximized: d['isMaximized'],
  };

  if (!isReachable(state)) {
    return {
      ok: false,
      reason: 'offscreen',
      detail: `saved position ${state.x},${state.y} is not on any connected display`,
    };
  }

  return { ok: true, state };
}

export function saveWindowState(state: SavedWindowState): void {
  try {
    const path = statePath();
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(state, null, 2), 'utf8');
  } catch (err) {
    // Never fatal. Failing to remember a window position is not worth crashing
    // an always-on surface over.
    console.warn(`[orb] could not save window state: ${(err as Error).message}`);
  }
}
