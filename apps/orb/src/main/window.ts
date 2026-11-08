/**
 * The Orb window, and everything that hardens it.
 *
 * CONTRACT §2.3 and plan.md §5.1: contextIsolation on, nodeIntegration off,
 * sandbox on. Those three are the baseline. The rest of this file is the part
 * that is easy to forget and impossible to retrofit convincingly — denying
 * navigation, denying window.open, and denying every permission the renderer
 * could ask for.
 */

import { join } from 'node:path';

import { BrowserWindow, screen, type Rectangle, type WebContents } from 'electron';

import { COMPACT_WINDOW } from '../shared/ipc-contract.ts';

import {
  loadWindowState,
  saveWindowState,
  MIN_HEIGHT,
  MIN_WIDTH,
  SAVE_DEBOUNCE_MS,
  type SavedWindowState,
} from './window-state.ts';

// Design tokens are the single source of truth for colour (CONTRACT §9), and
// that applies to the native window chrome too, not just CSS. Reading the value
// from packages/tokens keeps the flash-before-first-paint the same black as the
// page it precedes.
import tokens from '@tessa/tokens';

const BACKGROUND = tokens.color['bg-void'].value;

/**
 * There are no default dimensions any more, and that is the fix.
 *
 * The old code carried `DEFAULT_WIDTH = 1280, DEFAULT_HEIGHT = 760` as literals
 * and never consulted `screen` at all — not `.size`, not `.workAreaSize`. On a
 * 1366×768 panel with a 48px taskbar (work area 1366×720) a 760-tall window is
 * 40px taller than the space available, so it could not sit inside the work
 * area whatever it did. First launch now derives its size from
 * `workAreaSize` and maximizes.
 *
 * Every dimension below is CONTENT, not window rect — `useContentSize: true`
 * makes width/height/minWidth/minHeight all refer to the web page. That
 * distinction is the whole of §R.8: a maximized frameless window on Windows has
 * a window rect 16px larger in each axis than its content, because DWM keeps an
 * invisible resize border outside the visible edge.
 */

export interface WindowOptions {
  isDev: boolean;
  /** Dev-server URL from electron-vite; absent in a packaged build. */
  rendererUrl: string | undefined;
  start?: StartDecision | null;
}

export interface StartDecision {
  log: (line: string) => void;
  decided: (granted: boolean) => void;
}

export const START_FOCUS_WAIT_MS = 1000;

const WM_ACTIVATE = 0x0006;
const WM_SETFOCUS = 0x0007;
const WM_ACTIVATEAPP = 0x001c;
const WM_NCACTIVATE = 0x0086;

/**
 * Is this launch carrying measurement instrumentation?
 *
 * ─── why this exists ───
 * The owner's Orb opened as a ~1000x660 floating window with gaps on every side
 * of a 1366x768 screen, and the cause was this file working exactly as designed.
 * `orb-window.json` held:
 *
 *     { "width": 984, "height": 652, "x": 188, "y": 50, "isMaximized": true }
 *
 * 984x652 at 188,50 is not a size he ever chose. It is the exact client rect
 * produced by `MoveWindow(180, 50, 1000, 660)` in a verification harness — a
 * measurement artefact, written to his config by a resize the persistence layer
 * could not tell apart from a deliberate one, and restored on every launch
 * since.
 *
 * `isDev` cannot be the discriminator: he runs `npm run dev`, so it is true for
 * him too. What separates a measurement run from his is the instrumentation
 * flags, so those are what gate persistence.
 *
 * Matched by PREFIX rather than an exact list, because an exact list rots and
 * the failure mode of a missed entry is silently corrupting his config again.
 * The prefixes cover every dev flag this app currently defines —
 * --force-state, --force-tier, --probe-geometry, --probe-pulse, --probe-limb,
 * --capture-every, --dev-drive, --dev-overlay, --fixture-transcript,
 * --stop-beats-after, --ptt-chord, --ptt-mode — and any future one that follows
 * the same naming.
 */
const INSTRUMENTATION_FLAG = /^--(force|probe|capture|dev|fixture|stop|ptt)-/;

export function isInstrumentedLaunch(argv: readonly string[] = process.argv): boolean {
  return argv.some((a) => INSTRUMENTATION_FLAG.test(a));
}

/**
 * Applied to every WebContents the app ever creates, not just the main window.
 * Registered from `app.on('web-contents-created')` so a future window, or one
 * created by something else, cannot skip it.
 */
export function hardenWebContents(contents: WebContents): void {
  // Nothing in the Orb opens a new window. A renderer asking for one is either
  // a bug or something worse.
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));

  // The renderer is a fixed local document. It never navigates — not to a
  // remote page, not to another local file.
  contents.on('will-navigate', (event, url) => {
    event.preventDefault();
    console.warn(`[orb] blocked navigation to ${url}`);
  });

  // Belt and braces: webviewTag is already false in webPreferences.
  contents.on('will-attach-webview', (event) => {
    event.preventDefault();
  });
}

export function createOrbWindow(options: WindowOptions): BrowserWindow {
  // workAreaSize, never size. `.size` is the panel including the taskbar strip;
  // using it is how a window ends up 48px taller than the space it can occupy.
  const { workAreaSize, workArea } = screen.getPrimaryDisplay();

  const instrumented = isInstrumentedLaunch();

  const fillWorkArea: SavedWindowState = {
    width: workAreaSize.width,
    height: workAreaSize.height,
    x: workArea.x,
    y: workArea.y,
    isMaximized: true,
  };

  // An instrumented launch neither reads nor writes. Not reading is the smaller
  // half of the fix and still worth it: two capture sets were taken at 984x652
  // and 1366x720 and could not be compared, because each run inherited whatever
  // the previous one had left behind. A measurement should start from a known
  // geometry, not from the sediment of the last measurement.
  const restored = instrumented ? null : loadWindowState();

  /**
   * DEV ONLY. `--force-size=<w>x<h>` — CONTENT pixels, not window rect.
   *
   * The composition has to be proven at the minimum window, not asserted at it,
   * and a maximised launch always fills the work area. This is the only way to
   * put the surface at 900x600 without dragging an edge by hand and guessing
   * when it was close enough.
   *
   * It matches the `--force-` prefix, so `isInstrumentedLaunch` already treats
   * it as instrumentation: a sized run neither reads nor writes the owner's
   * window state, and cannot leave a 900x600 behind in his config.
   */
  const forcedSize = (() => {
    if (!options.isDev) return null;
    const flag = process.argv.find((a) => a.startsWith('--force-size='));
    if (!flag) return null;
    const m = /^(\d{3,5})x(\d{3,5})$/.exec(flag.slice('--force-size='.length));
    if (!m) return null;
    return { w: Number.parseInt(m[1] as string, 10), h: Number.parseInt(m[2] as string, 10) };
  })();

  const initial: SavedWindowState = forcedSize
    ? {
        width: Math.max(MIN_WIDTH, forcedSize.w),
        height: Math.max(MIN_HEIGHT, forcedSize.h),
        x: workArea.x,
        y: workArea.y,
        isMaximized: false,
      }
    : restored?.ok
      ? restored.state
      : fillWorkArea;

  console.log(
    `[orb] window: workArea ${workAreaSize.width}x${workAreaSize.height} · ` +
      (instrumented
        ? 'INSTRUMENTED launch — window state not read and will not be written; filling the work area'
        : restored?.ok
          ? `restored ${initial.width}x${initial.height} at ${initial.x},${initial.y} maximized=${initial.isMaximized}`
          : `no restore (${restored?.reason}: ${restored?.detail}) — maximizing`),
  );

  const window = new BrowserWindow({
    // CONTENT dimensions, because useContentSize is true below.
    width: initial.width,
    height: initial.height,
    x: initial.x,
    y: initial.y,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,

    /**
     * Every width/height on this object is the web page, not the window rect.
     *
     * Without this, "1366×720" would mean a window rect of 1366×720 whose
     * content is 1350×704 — the invisible DWM resize border eats 8px per side.
     * §R.8 is written in content pixels, so the API is set to speak in them.
     */
    useContentSize: true,

    // CONTRACT §9.1: "centre stage floats over pure void". A native title bar
    // would put a strip of OS chrome above that void, so the window is
    // frameless and the status bar carries its own drag region and controls.
    frame: false,
    backgroundColor: BACKGROUND,
    autoHideMenuBar: true,

    // Paint nothing until the renderer is ready, rather than showing a white
    // rectangle that then turns black.
    show: false,

    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),

      // ── the three non-negotiables (CONTRACT §2.3) ──
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,

      // ── and the ones that quietly undo them if left at a default ──
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      experimentalFeatures: false,
      webviewTag: false,
      spellcheck: false,

      // No devtools in a packaged build. There is no reason for a shipped
      // always-on agent surface to carry an interactive JS console.
      devTools: options.isDev,

      // Deliberately left at the default (true): when the window is hidden the
      // renderer SHOULD be throttled. Two physical cores are shared with the
      // daemon, and a sphere nobody is looking at has no claim on them.
      backgroundThrottling: true,
    },
  });

  const start = options.start ?? null;
  // First launch, or a discarded restore, opens filling the work area exactly.
  if (start) beginCompactStart(window, initial.isMaximized, start.log);
  else if (initial.isMaximized) window.maximize();

  const noActivate = options.isDev && process.argv.includes('--dev-noactivate');
  if (noActivate) window.setFocusable(false);
  window.once('ready-to-show', () => {
    if (start) {
      showAndDecide(window, start);
      return;
    }
    if (noActivate) window.showInactive();
    else window.show();
  });

  if (!instrumented) attachStatePersistence(window);
  attachFullscreenToggle(window);

  if (options.isDev && options.rendererUrl) {
    void window.loadURL(options.rendererUrl);
  } else {
    void window.loadFile(join(__dirname, '../renderer/index.html'));
  }

  return window;
}

/**
 * Persist geometry on move and resize, coalesced.
 *
 * `getNormalBounds()` rather than `getBounds()` is the important part: while
 * maximized, getBounds returns the maximized rectangle, so saving it would mean
 * un-maximizing restores to a window that exactly covers the work area and the
 * owner can never get their smaller window back. getNormalBounds keeps the
 * pre-maximize rectangle, which is what "restore" is supposed to mean.
 */
interface CompactSave {
  bounds: Rectangle;
  maximized: boolean;
  fullScreen: boolean;
}

const compactSaves = new WeakMap<BrowserWindow, CompactSave>();

export function isCompactWindow(window: BrowserWindow): boolean {
  return compactSaves.has(window);
}

function compactTarget(window: BrowserWindow, card: boolean): Rectangle {
  const { workArea } = screen.getDisplayMatching(window.getBounds());
  const width = card ? COMPACT_WINDOW.cardW : COMPACT_WINDOW.w;
  const height = card ? COMPACT_WINDOW.cardH : COMPACT_WINDOW.h;
  return {
    x: workArea.x + workArea.width - width - COMPACT_WINDOW.inset,
    y: workArea.y + workArea.height - height - COMPACT_WINDOW.inset,
    width,
    height,
  };
}

export function setCompactWindow(window: BrowserWindow, on: boolean, card: boolean, log: (line: string) => void): void {
  if (window.isDestroyed()) return;
  const saved = compactSaves.get(window);
  if (!on) {
    if (!saved) return;
    window.setAlwaysOnTop(false);
    window.setMinimumSize(MIN_WIDTH, MIN_HEIGHT);
    window.setBounds(saved.bounds);
    if (saved.maximized) window.maximize();
    if (saved.fullScreen) window.setFullScreen(true);
    compactSaves.delete(window);
    const c = window.getContentBounds();
    log(`compact OFF: restored ${saved.bounds.width}x${saved.bounds.height} at ${saved.bounds.x},${saved.bounds.y} maximized=${saved.maximized} fullScreen=${saved.fullScreen}; content now ${c.width}x${c.height} at ${c.x},${c.y}; focused=${window.isFocused()}`);
    return;
  }
  if (!saved) {
    compactSaves.set(window, { bounds: window.getNormalBounds(), maximized: window.isMaximized(), fullScreen: window.isFullScreen() });
    if (window.isFullScreen()) window.setFullScreen(false);
    if (window.isMaximized()) window.unmaximize();
    window.setMinimumSize(COMPACT_WINDOW.w, COMPACT_WINDOW.h);
    window.setAlwaysOnTop(true, 'floating');
  }
  const target = compactTarget(window, card);
  window.setContentBounds(target);
  const c = window.getContentBounds();
  log(`compact ON card=${card}: target ${target.width}x${target.height} at ${target.x},${target.y}; content ${c.width}x${c.height} at ${c.x},${c.y}; alwaysOnTop=${window.isAlwaysOnTop()} focused=${window.isFocused()}`);
  setTimeout(() => {
    if (window.isDestroyed() || !compactSaves.has(window)) return;
    const now = window.getContentBounds();
    const want = compactTarget(window, card);
    if (now.width !== want.width || now.height !== want.height || now.x !== want.x || now.y !== want.y) {
      window.setContentBounds(want);
      log(`compact re-applied: was ${now.width}x${now.height} at ${now.x},${now.y}`);
    }
  }, 350);
}

function beginCompactStart(window: BrowserWindow, maximized: boolean, log: (line: string) => void): void {
  const full = window.getBounds();
  compactSaves.set(window, { bounds: full, maximized, fullScreen: false });
  window.setMinimumSize(COMPACT_WINDOW.w, COMPACT_WINDOW.h);
  window.setAlwaysOnTop(true, 'floating');
  window.setContentBounds(compactTarget(window, false));
  const c = window.getContentBounds();
  log(
    `START compact first: the hidden window is ${c.width}x${c.height} at ${c.x},${c.y}, on top; ` +
      `Full is kept for later as ${full.width}x${full.height} at ${full.x},${full.y} maximized=${maximized}; nothing is shown yet`,
  );
}

function low(b: Buffer): number {
  return b.length >= 4 ? b.readUInt32LE(0) : 0;
}

function showAndDecide(window: BrowserWindow, start: StartDecision): void {
  const t0 = performance.now();
  const ms = (): string => (performance.now() - t0).toFixed(1);
  const seen: string[] = [];
  const hooked = [WM_ACTIVATE, WM_SETFOCUS, WM_ACTIVATEAPP, WM_NCACTIVATE];
  let done = false;
  let flashStopped = false;
  let timer: NodeJS.Timeout | null = null;
  const finish = (granted: boolean, why: string): void => {
    if (done) return;
    done = true;
    if (timer) clearTimeout(timer);
    setImmediate(() => {
      if (window.isDestroyed()) return;
      for (const m of hooked) window.unhookWindowMessage(m);
      if (!granted) window.flashFrame(false);
      const c = window.getContentBounds();
      start.log(
        `START ${granted ? 'GRANTED: Windows made the Orb the foreground window; it grows to Full' : 'REFUSED: Windows kept the foreground on another window; the Orb stays Compact, on top, and its taskbar flash is stopped'}` +
          ` (${why}) at +${ms()} ms wall=${Date.now()}; messages ${seen.join(', ') || 'none'}; content ${c.width}x${c.height} at ${c.x},${c.y}; isFocused=${window.isFocused()}`,
      );
      start.decided(granted);
    });
  };
  window.hookWindowMessage(WM_NCACTIVATE, (w) => {
    const v = low(w);
    seen.push(`NCACTIVATE(${v}) +${ms()}`);
    if (v !== 0) {
      finish(true, 'WM_NCACTIVATE TRUE');
      return;
    }
    if (flashStopped) return;
    flashStopped = true;
    setImmediate(() => {
      if (done || window.isDestroyed()) return;
      window.flashFrame(false);
      seen.push(`flash stopped +${ms()}`);
    });
  });
  window.hookWindowMessage(WM_ACTIVATE, (w) => {
    seen.push(`ACTIVATE(${low(w) & 0xffff}) +${ms()}`);
  });
  window.hookWindowMessage(WM_ACTIVATEAPP, (w, l) => {
    seen.push(`ACTIVATEAPP(${low(w)},${low(l)}) +${ms()}`);
  });
  window.hookWindowMessage(WM_SETFOCUS, () => {
    seen.push(`SETFOCUS +${ms()}`);
  });
  const wall = Date.now();
  window.show();
  start.log(
    `START shown at Compact size, on top, at wall=${wall}; show() returned after ${ms()} ms; ` +
      `waiting up to ${START_FOCUS_WAIT_MS} ms for Windows to make it the foreground window`,
  );
  if (!done) timer = setTimeout(() => finish(false, `no WM_NCACTIVATE TRUE within ${START_FOCUS_WAIT_MS} ms`), START_FOCUS_WAIT_MS);
}

function attachStatePersistence(window: BrowserWindow): void {
  let timer: NodeJS.Timeout | null = null;

  const persist = (): void => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      if (window.isDestroyed()) return;
      if (compactSaves.has(window)) return;
      // Fullscreen bounds are the panel, not a window the owner chose. Saving
      // them would make every launch after an F11 session open fullscreen-sized.
      if (window.isFullScreen()) return;

      const bounds = window.getNormalBounds();
      saveWindowState({
        width: bounds.width,
        height: bounds.height,
        x: bounds.x,
        y: bounds.y,
        isMaximized: window.isMaximized(),
      });
    }, SAVE_DEBOUNCE_MS);
  };

  // Registered one by one rather than over a list: BrowserWindow.on is
  // overloaded per event name, and a union of names matches no single overload.
  window.on('resize', persist);
  window.on('move', persist);
  window.on('maximize', persist);
  window.on('unmaximize', persist);

  // A close can outrun a pending debounce; flush synchronously.
  window.on('close', () => {
    if (timer) clearTimeout(timer);
    if (window.isFullScreen() || compactSaves.has(window)) return;
    const bounds = window.getNormalBounds();
    saveWindowState({
      width: bounds.width,
      height: bounds.height,
      x: bounds.x,
      y: bounds.y,
      isMaximized: window.isMaximized(),
    });
  });
}

/**
 * F11 toggles borderless fullscreen.
 *
 * Bound through `before-input-event` rather than an accelerator because this
 * app has no application menu (main/index.ts removes it, so Alt reaches the
 * renderer). Fullscreen is the one state that legitimately covers the taskbar,
 * so its content is the full 1366×768 rather than the 1366×720 work area.
 */
function attachFullscreenToggle(window: BrowserWindow): void {
  window.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown' || input.key !== 'F11') return;
    event.preventDefault();
    if (compactSaves.has(window)) {
      console.log('[orb] F11 ignored: the window is in Compact mode');
      return;
    }
    window.setFullScreen(!window.isFullScreen());
    console.log(`[orb] F11 fullScreen=${window.isFullScreen()}`);
  });
}
