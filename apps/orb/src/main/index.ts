/**
 * Zoey Orb — Electron main process.
 *
 * Three things live here and nowhere else: the WebSocket to the daemon, the
 * auth token, and the security posture of the renderer. See CONTRACT §2.3.
 *
 * DELIBERATELY ABSENT: this app does NOT register the `zoey://` protocol
 * handler. That belongs to apps/console (CONTRACT §6.6, its deeplink.ts). Two
 * registrants would race for the same scheme, and whichever won would be a
 * coin toss on the owner's machine.
 */

import { app, BrowserWindow, ipcMain, Menu, session, type Session } from 'electron';

import { developmentCsp, PRODUCTION_CSP } from '../shared/csp.ts';
import { IPC, type BootstrapInfo, type ConnectionStatus } from '../shared/ipc-contract.ts';
import { gpuFeatureSummary, probeGpu } from './gpu-probe.ts';
import { createOrbWindow, hardenWebContents } from './window.ts';
import { DaemonConnection } from './ws-client.ts';

const isDev = !app.isPackaged;
const rendererUrl = process.env['ELECTRON_RENDERER_URL'];

function log(message: string): void {
  console.log(`[orb] ${message}`);
}

/* ─────────────────────────────────────────────────────────── session policy */

function applyContentSecurityPolicy(ses: Session): void {
  // In production the enforcing copy is the <meta> tag injected at build time —
  // a file:// document has no response headers. This header is the dev-server
  // path, and harmless redundancy in production.
  const policy =
    isDev && rendererUrl ? developmentCsp(new URL(rendererUrl).origin) : PRODUCTION_CSP;

  ses.webRequest.onHeadersReceived((details, callback) => {
    const headers: Record<string, string | string[]> = { ...details.responseHeaders };
    // Replace rather than append: two CSP headers are intersected, and a
    // leftover permissive one from the dev server would muddy the policy.
    for (const key of Object.keys(headers)) {
      if (key.toLowerCase() === 'content-security-policy') delete headers[key];
    }
    headers['Content-Security-Policy'] = [policy];
    callback({ responseHeaders: headers });
  });
}

function denyAllPermissions(ses: Session): void {
  // The Orb is a voice UI. In Phase 1 it has no voice, so the microphone denial
  // is written BEFORE any code that could want a microphone — the ordering is
  // the point. When voice lands it will be an explicit, reviewed allowance for
  // exactly one permission, not the removal of a blanket deny nobody remembers.
  ses.setPermissionRequestHandler((_contents, permission, callback) => {
    log(`denied permission request: ${permission}`);
    callback(false);
  });

  // The request handler covers prompts; the check handler covers the silent
  // queries (navigator.permissions.query, getUserMedia's internal check).
  ses.setPermissionCheckHandler((_contents, permission) => {
    log(`denied permission check: ${permission}`);
    return false;
  });

  ses.setDevicePermissionHandler(() => false);
}

/* ──────────────────────────────────────────────────────────────── lifecycle */

// Must be called before the app is ready.
app.enableSandbox();

// A second Orb would open a second socket with the same credential and double
// the exposure for no benefit. Focus the existing window instead.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.setAppUserModelId('com.titanwave.zoey.orb');

  // No application menu at all.
  //
  // The window is frameless and the design has no menu bar, but Electron
  // installs a default one anyway, and `autoHideMenuBar` only HIDES it — the
  // accelerators stay live. Two consequences, both bad here:
  //
  //   • Alt activates the invisible menu bar and swallows the keystroke, which
  //     is why Alt+1…6 never reached the renderer.
  //   • The default menu ships Ctrl+R (reload) and Ctrl+Shift+I (devtools).
  //     A shipped always-on agent surface should not carry a one-chord path to
  //     an interactive JS console in the process that talks to the daemon.
  Menu.setApplicationMenu(null);

  let connection: DaemonConnection | null = null;
  let lastPhase: ConnectionStatus['phase'] | null = null;

  app.on('second-instance', () => {
    const [existing] = BrowserWindow.getAllWindows();
    if (existing) {
      if (existing.isMinimized()) existing.restore();
      existing.focus();
    }
  });

  // Every WebContents, not just the first window.
  app.on('web-contents-created', (_event, contents) => hardenWebContents(contents));

  void app.whenReady().then(() => {
    applyContentSecurityPolicy(session.defaultSession);
    denyAllPermissions(session.defaultSession);

    // Must be after whenReady — before that the GPU process has not reported in
    // and every feature reads as 'unknown'.
    const gpu = probeGpu(process.argv);
    log(
      `gpu: webgl2=${gpu.webgl2} compositing=${gpu.gpuCompositing}` +
        `${gpu.softwareSuspected ? ' (software suspected)' : ''}` +
        `${gpu.forcedTier ? ` forced=${gpu.forcedTier}` : ''}`,
    );
    // The full table, because the key names are not stable across Electron
    // majors and a missing key reads as 'unknown' rather than announcing itself.
    if (isDev) log(`gpu features: ${gpuFeatureSummary()}`);

    const bootstrap: BootstrapInfo = {
      surfaceVersion: app.getVersion(),
      isDev,
      gpu,
    };

    connection = new DaemonConnection({
      surfaceVersion: bootstrap.surfaceVersion,
      log,
      onStatus: (status: ConnectionStatus) => {
        // Log every phase transition. Verification depends on being able to
        // prove, from the log alone, that an offline Orb opened no sockets.
        if (status.phase !== lastPhase) {
          lastPhase = status.phase;
          log(`connection: ${status.phase}${status.detail ? ` — ${status.detail}` : ''}`);
        }
        for (const window of BrowserWindow.getAllWindows()) {
          if (!window.isDestroyed()) window.webContents.send(IPC.connectionChanged, status);
        }
      },
    });

    ipcMain.handle(IPC.bootstrap, () => bootstrap);
    ipcMain.handle(IPC.getConnection, () => connection?.current ?? { phase: 'offline' });
    ipcMain.on(IPC.retryConnection, () => connection?.retryNow());
    ipcMain.on(IPC.windowMinimize, (event) =>
      BrowserWindow.fromWebContents(event.sender)?.minimize(),
    );
    ipcMain.on(IPC.windowClose, (event) => BrowserWindow.fromWebContents(event.sender)?.close());

    // The window is created only AFTER every handler is registered. The
    // renderer calls zoey.bootstrap() as its first act; creating the window
    // first leaves a window — small, but real — in which that invoke rejects
    // with "no handler registered" and the surface comes up with no GPU tier.
    createOrbWindow({ isDev, rendererUrl });

    connection.start();
  });

  app.on('window-all-closed', () => app.quit());

  app.on('before-quit', () => {
    connection?.dispose();
    connection = null;
  });
}
