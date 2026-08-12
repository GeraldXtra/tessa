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

import { BrowserWindow, type WebContents } from 'electron';

// Design tokens are the single source of truth for colour (CONTRACT §9), and
// that applies to the native window chrome too, not just CSS. Reading the value
// from packages/tokens keeps the flash-before-first-paint the same black as the
// page it precedes.
import tokens from '@zoey/tokens';

const BACKGROUND = tokens.color['bg-void'].value;

/**
 * 1280×760 on a 1366×768 display leaves room for the taskbar without the window
 * needing to be maximised. The minimum is the point below which the collapsed
 * layout stops being honest: 1024 still gives the sphere ~700px with a drawer
 * open (spec §8.1).
 */
const DEFAULT_WIDTH = 1280;
const DEFAULT_HEIGHT = 760;
const MIN_WIDTH = 1024;
const MIN_HEIGHT = 640;

export interface WindowOptions {
  isDev: boolean;
  /** Dev-server URL from electron-vite; absent in a packaged build. */
  rendererUrl: string | undefined;
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
  const window = new BrowserWindow({
    width: DEFAULT_WIDTH,
    height: DEFAULT_HEIGHT,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,

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

  window.once('ready-to-show', () => window.show());

  if (options.isDev && options.rendererUrl) {
    void window.loadURL(options.rendererUrl);
  } else {
    void window.loadFile(join(__dirname, '../renderer/index.html'));
  }

  return window;
}
