/**
 * The ambient Orb — a small sphere pinned to the top-right corner, all day.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY THIS IS A SECOND WINDOW AND NOT A MODE OF THE FIRST
 *
 * The full Orb is a maximised, framed-in-void surface with a drawer, rails and
 * a chat panel. The widget is 132 px of transparent nothing with a sphere in
 * it. They differ in every window-level property that exists — transparency,
 * always-on-top, taskbar presence, click-through, resizability — and every one
 * of those is fixed at construction in Electron. `setAlwaysOnTop` can be
 * toggled; `transparent` cannot, and neither can `frame`. A single window that
 * tried to be both would have to be transparent and frameless ALWAYS, which
 * would give the full Orb no title bar, no resize border and a compositor path
 * it does not need.
 *
 * So: two windows, one renderer bundle, one engine, one WebSocket. `broadcast`
 * in main/index.ts already sends every daemon push to EVERY BrowserWindow, so
 * the widget receives agent state with no new client, no new subscription and
 * no change to ws-client.ts.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * ⚠ THE FOUR SETTINGS THAT ARE EASY TO GET WRONG, AND WHAT EACH ONE COSTS
 *
 *   transparent: true       Without it, the corner is a BLACK BOX. It also
 *                           cannot be changed after construction, and on
 *                           Windows it is mutually exclusive with a `frame`.
 *   backgroundColor         Must be fully transparent ('#00000000'). A named
 *                           colour or an opaque hex silently defeats
 *                           `transparent: true` — this is the single most
 *                           common way a transparent window renders black.
 *   frame: false            A title bar on a 132 px sphere is absurd, and on
 *                           Windows a framed window cannot be transparent.
 *   skipTaskbar: true       An always-on-top ornament should not eat a slot in
 *                           his Alt+Tab or his taskbar.
 *
 * ⚠ AND THE CLICK-THROUGH RULE, WHICH IS THE ONE WITH TEETH
 *
 * A 132x132 always-on-top window sitting over his screen would swallow every
 * click in that square — including the window controls of whatever is
 * maximised underneath, which on a 1366x768 screen is exactly where the close
 * button lives. That is not a cosmetic bug; it would make his top-right corner
 * unusable all day.
 *
 * So the window is created click-through (`setIgnoreMouseEvents(true, {
 * forward: true })`) and the RENDERER turns it off only while the pointer is
 * actually inside the sphere's disc. `forward: true` is what makes that
 * possible: it keeps delivering move events to the renderer while the window
 * ignores clicks, so the page can still see where the pointer is.
 */

import { join } from 'node:path';

import { BrowserWindow, screen } from 'electron';

import tokens from '@tessa/tokens';

/**
 * Content size, in CSS px, at 1366x768.
 *
 * 132 is a considered number rather than a round one. The sphere's projected
 * radius is 43% of canvas height (sphere-engine.ts), so a 132 px canvas draws a
 * ~113 px disc — about the size of a large desktop icon, readable as a state
 * indicator from across a room, and 1.5% of a 1366x768 screen. Bigger starts
 * covering real estate on a small panel; much smaller and the particle lattice
 * stops being legible as the sphere he built and becomes a smudge.
 */
export const WIDGET_SIZE = 132;

/** Gap from the work area's top-right corner. */
export const WIDGET_MARGIN = 12;

export interface WidgetOptions {
  isDev: boolean;
  rendererUrl: string | undefined;
  /** Called when the sphere is clicked. */
  onExpand: () => void;
}

/**
 * Top-right of the WORK AREA, not of the screen.
 *
 * workArea excludes the taskbar. On this machine the taskbar is at the bottom
 * so the two agree at the top — but on a machine with a top or right taskbar
 * they do not, and a widget half-under the taskbar is the kind of bug that only
 * appears on someone else's setup.
 */
export function widgetBounds(): { x: number; y: number; width: number; height: number } {
  const { workArea } = screen.getPrimaryDisplay();
  return {
    width: WIDGET_SIZE,
    height: WIDGET_SIZE,
    x: workArea.x + workArea.width - WIDGET_SIZE - WIDGET_MARGIN,
    y: workArea.y + WIDGET_MARGIN,
  };
}

export function createWidgetWindow(options: WidgetOptions): BrowserWindow {
  const bounds = widgetBounds();

  const window = new BrowserWindow({
    ...bounds,
    useContentSize: true,

    // ── the four that make it an ornament rather than a window ──
    frame: false,
    transparent: true,
    // FULLY transparent. '#00000000' — eight hex digits, the last two alpha.
    // Six digits here means opaque black and the whole feature reads as broken.
    backgroundColor: `${tokens.color['theme-void'].value}00`,
    skipTaskbar: true,

    alwaysOnTop: true,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    focusable: false,
    // Never steal focus from whatever he is typing in. A sphere that grabs the
    // keyboard when it appears is worse than no sphere.
    show: false,
    hasShadow: false,
    // Excluded from screen capture would be wrong here — he may well want it in
    // a screenshot — but it must not appear in the window switcher.
    autoHideMenuBar: true,

    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),

      // The same three non-negotiables as the main window (CONTRACT §2.3).
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      experimentalFeatures: false,
      webviewTag: false,
      spellcheck: false,
      devTools: options.isDev,

      /**
       * ⚠ FALSE HERE, AND IT IS THE OPPOSITE OF THE MAIN WINDOW'S SETTING.
       *
       * The main window sets `backgroundThrottling: true` with the note "a
       * sphere nobody is looking at has no claim on them". Correct there: that
       * window is either focused or minimised.
       *
       * This one is NEVER focused — `focusable: false` — and is always visible.
       * Chromium throttles an unfocused window's timers and rAF to roughly 1 Hz,
       * which would mean the ONE moment the widget matters (she starts speaking
       * and it should come alive) renders at one frame a second. The battery
       * saving that flag would buy is bought properly instead, by stopping the
       * loop outright when idle — see `setAnimating` in sphere-engine.ts.
       */
      backgroundThrottling: false,
    },
  });

  // ⚠ ALWAYS-ON-TOP, ABOVE FULL-SCREEN WINDOWS TOO.
  //
  // The bare `alwaysOnTop: true` constructor option puts it above NORMAL
  // windows only; a maximised video or a full-screen game still covers it. The
  // 'screen-saver' level is the documented way to sit above those. It is the
  // level a notification uses, which is exactly what this is.
  window.setAlwaysOnTop(true, 'screen-saver');

  // Visible on every virtual desktop. An ambient indicator that vanishes when
  // he switches desktops is not ambient.
  window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  /**
   * ⚠ CLICK-THROUGH BY DEFAULT. The renderer opts back IN over the disc.
   *
   * `forward: true` keeps mousemove flowing to the page while clicks pass
   * through to whatever is underneath, which is the only way the renderer can
   * know the pointer has entered the sphere and ask for interactivity.
   */
  window.setIgnoreMouseEvents(true, { forward: true });

  window.once('ready-to-show', () => window.showInactive());

  /**
   * Query parameters, and why they are how the widget is configured.
   *
   * `?cheap=1` picks the low tier. `?state=<agent state>` pins the initial
   * state, which exists so the two battery conditions — FROZEN and ANIMATING —
   * can each be measured on their own, without needing a daemon that is
   * mid-turn at the moment the sampler runs. A query string reaches a renderer
   * that has no Node and no argv of its own; the alternative was another IPC
   * round trip before first paint.
   */
  const params = new URLSearchParams();
  const forced = process.argv.find((a) => a.startsWith('--widget-state='));
  if (forced) params.set('state', forced.slice('--widget-state='.length));
  const query = params.toString() ? `?${params.toString()}` : '';

  if (options.isDev && options.rendererUrl) {
    void window.loadURL(new URL(`widget.html${query}`, options.rendererUrl).toString());
  } else {
    void window.loadFile(join(__dirname, '../renderer/widget.html'), {
      search: params.toString(),
    });
  }

  /**
   * The renderer's OS process id, logged.
   *
   * This is what makes the battery claim measurable rather than asserted: a
   * sampler can watch THIS pid's processor time across the frozen and animating
   * conditions, instead of watching the whole Electron process tree and
   * attributing the main window's own sphere to the widget.
   */
  window.webContents.once('did-finish-load', () => {
    if (window.isDestroyed()) return;
    console.log(`[orb] widget renderer pid=${window.webContents.getOSProcessId()}`);
  });

  /**
   * Re-pin on a display change. A resolution change or an undock moves the
   * corner; without this the widget stays at coordinates that may no longer be
   * on any screen.
   */
  const repin = (): void => {
    if (window.isDestroyed()) return;
    window.setBounds(widgetBounds());
  };
  screen.on('display-metrics-changed', repin);
  screen.on('display-added', repin);
  screen.on('display-removed', repin);
  window.on('closed', () => {
    screen.off('display-metrics-changed', repin);
    screen.off('display-added', repin);
    screen.off('display-removed', repin);
  });

  return window;
}
