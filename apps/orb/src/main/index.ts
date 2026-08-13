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

import {
  app,
  BrowserWindow,
  globalShortcut,
  ipcMain,
  Menu,
  screen,
  session,
  type Session,
} from 'electron';

import { AGENT_STATES } from '@zoey/protocol';

import { developmentCsp, PRODUCTION_CSP } from '../shared/csp.ts';
import {
  IPC,
  type AuditEntry,
  type BootstrapInfo,
  type ConnectionStatus,
  type DaemonHealth,
  type MicState,
  type PtySession,
  type PttMode,
} from '../shared/ipc-contract.ts';
import { gpuFeatureSummary, probeGpu } from './gpu-probe.ts';
import { PttController } from './ptt-controller.ts';
import { createOrbWindow, hardenWebContents } from './window.ts';
import { DaemonConnection } from './ws-client.ts';

const isDev = !app.isPackaged;
const rendererUrl = process.env['ELECTRON_RENDERER_URL'];

/** Dev-only. See the note where it is used, in the health handler. */
const beatsStopAfterMs = (() => {
  if (!isDev) return null;
  const flag = process.argv.find((a) => a.startsWith('--stop-beats-after='));
  if (!flag) return null;
  const seconds = Number.parseFloat(flag.slice('--stop-beats-after='.length));
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : null;
})();
const startedAt = Date.now();
let beatsBlocked = false;

/**
 * Dev-only `--force-state=<agent state>`.
 *
 * The Alt+1…6 cycler is a keystroke, and a keystroke is a dependency on window
 * focus, on synthetic input being accepted, and on the renderer having mounted
 * its listener. During verification one of those failed silently and a leg was
 * recorded against `idle` while it was labelled `blocked` — the sphere was
 * animating throughout a stability measurement. A flag cannot miss.
 */
function forcedState(): string | null {
  if (!isDev) return null;
  const flag = process.argv.find((a) => a.startsWith('--force-state='));
  if (!flag) return null;
  const value = flag.slice('--force-state='.length);
  return (AGENT_STATES as readonly string[]).includes(value) ? value : null;
}

/** Dev-only. `--probe-geometry=<ms>` / `--probe-pulse=<ms>`; 0 when absent. */
function probeFlagMs(name: string): number {
  if (!isDev) return 0;
  const prefix = `--${name}=`;
  const flag = process.argv.find((a) => a.startsWith(prefix));
  if (!flag) return 0;
  const ms = Number.parseFloat(flag.slice(prefix.length));
  return Number.isFinite(ms) && ms > 0 ? ms : 0;
}

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
  let healthBeats = 0;

  // Retained so a renderer that mounts after these arrived can still see them.
  let lastHealth: DaemonHealth | null = null;
  let lastAudit: AuditEntry[] = [];
  let lastPtySessions: PtySession[] = [];

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
      probeGeometryMs: probeFlagMs('probe-geometry'),
      probePulseMs: probeFlagMs('probe-pulse'),
      forcedState: forcedState(),
    };
    if (bootstrap.probeGeometryMs || bootstrap.probePulseMs || bootstrap.forcedState) {
      log(
        `probe: geometry=${bootstrap.probeGeometryMs}ms pulse=${bootstrap.probePulseMs}ms` +
          ` state=${bootstrap.forcedState ?? 'live'}`,
      );
    }

    const broadcast = (channel: string, payload: unknown): void => {
      for (const window of BrowserWindow.getAllWindows()) {
        if (!window.isDestroyed()) window.webContents.send(channel, payload);
      }
    };

    /* ── push-to-talk ─────────────────────────────────────────────────────── */

    let lastMic: MicState;
    const ptt = new PttController({
      send: (action) => connection?.sendPushToTalk(action) ?? false,
      onState: (state) => {
        lastMic = state;
        broadcast(IPC.micState, state);
      },
      notify: (note) => broadcast(IPC.notify, note),
      log,
    });
    lastMic = ptt.state;

    /**
     * The global chord: `Ctrl+Alt+Shift+Space`.
     *
     * ─── the first answer was wrong, and the machine said so ───
     * This was `Ctrl+Alt+Space`, chosen by reasoning: Space is the push-to-talk
     * convention, Windows reserves only Ctrl+Alt+Delete / Ctrl+Shift+Esc /
     * Win+*, and it avoids Ctrl+Space, which is the IME toggle and editor
     * completion. Every step of that was true and the conclusion was still
     * wrong — on THIS machine `register()` returns false for it. Probed with no
     * Orb running, so nothing of ours held it:
     *
     *     Control+Alt+Space          register()=false   ← refused
     *     Control+Shift+Space        register()=true
     *     Alt+Space                  register()=true
     *     Control+Alt+V              register()=true
     *     Control+Alt+Z              register()=true
     *     Control+Alt+Shift+Space    register()=true
     *     Control+Alt+Shift+V        register()=true
     *     Control+Alt+Q              register()=true
     *
     * Something already owns it — Ctrl+Alt+Space is a width-toggle in several
     * Microsoft IMEs. Which one does not matter; that it is taken does.
     *
     * ─── why this one, of the seven that were free ───
     * Most of the free ones are free of the OS and not of the tools Gerald
     * lives in, and a global grab is OS-WIDE: it takes the key away from the
     * focused app, so shadowing an editor binding is not rudeness, it is
     * breaking that editor everywhere.
     *
     *   Alt+Space            the Windows system menu for every window. Free to
     *                        take, and taking it breaks move/size/close.
     *   Ctrl+Shift+Space     parameter hints in VS Code and JetBrains.
     *   Ctrl+Alt+V           paste-special in Office; introduce-variable in
     *                        JetBrains.
     *   Ctrl+Alt+Z / +Q      revert and quick-doc in JetBrains.
     *
     * Three modifiers is the zone nothing standard reaches into, and Space
     * keeps the convention. I had dismissed three-modifier chords as miserable
     * to press one-handed — that is true of a LETTER, and false of Space:
     * Ctrl, Shift and Alt are one cluster under the left palm and the thumb
     * takes Space independently.
     *
     * `--ptt-chord=` overrides it, which is also how the failure path is tested:
     * pointing it at Control+Alt+Space reproduces a real collision rather than a
     * simulated one.
     */
    const PTT_CHORD = process.argv.find((a) => a.startsWith('--ptt-chord='))
      ? (process.argv.find((a) => a.startsWith('--ptt-chord=')) as string).slice(
          '--ptt-chord='.length,
        )
      : 'Control+Alt+Shift+Space';

    /**
     * Register, and treat failure as news.
     *
     * `globalShortcut.register` returns false when the OS has already given the
     * chord to someone else. Swallowing that would produce the worst possible
     * outcome: a trigger that looks configured, does nothing, and gives Gerald
     * no reason to suspect why. It goes to the §R.2 notification stack — a
     * mechanism that has been built and dark since it was written, whose first
     * message should be a true one — and into the mic state, so the status bar
     * can say the chord is dead rather than implying it works.
     */
    const registerChord = (): void => {
      let ok = false;
      try {
        ok = globalShortcut.register(PTT_CHORD, () => ptt.chordPressed());
      } catch (err) {
        // An unparseable accelerator throws rather than returning false.
        log(`!! global shortcut '${PTT_CHORD}' is not a valid accelerator: ${String(err)}`);
      }
      // Believe isRegistered over the return value: they disagree on some
      // Windows builds when another process holds the chord.
      ok = ok && globalShortcut.isRegistered(PTT_CHORD);
      ptt.setChord(PTT_CHORD, ok);

      if (ok) {
        log(`global shortcut ${PTT_CHORD} → push-to-talk`);
      } else {
        log(`!! global shortcut ${PTT_CHORD} REFUSED — another process holds it`);
        broadcast(IPC.notify, {
          id: 'ptt-chord-failed',
          level: 'error',
          title: 'Push-to-talk shortcut unavailable',
          body: `${PTT_CHORD} is already held by another application. Push-to-talk still works while the Orb has focus.`,
        });
      }
    };

    connection = new DaemonConnection({
      surfaceVersion: bootstrap.surfaceVersion,
      log,

      onHealth: (health: DaemonHealth) => {
        // Dev-only beat log. The equatorial pulse fires once per arrival, so the
        // interval between these lines IS the pulse interval — measured at the
        // thing that drives it rather than inferred from the animation.
        if (isDev) {
          healthBeats += 1;
          log(`beat #${healthBeats} uptimeS=${health.uptimeS}`);
        }
        lastHealth = health;

        /**
         * `--stop-beats-after=<seconds>` — dev only.
         *
         * §R.1 requires the equatorial pulse to STOP when beats stop, and the
         * only honest way to observe that is to stop them. Killing the daemon
         * is not available: it belongs to the other session, and two sessions
         * fighting over one daemon is what forked the audit chain. This blocks
         * the beat at the client instead, which exercises the identical path —
         * nothing calls engine.beat(), so the in-flight pulse finishes its
         * travel and uPulseGain drops to 0 — without touching a process I do
         * not own.
         *
         * NOTE the `beat #` line above is logged BEFORE this check, on purpose:
         * it counts heartbeats ARRIVING from the daemon, which is what proves
         * the daemon is still alive and the block is the thing suppressing the
         * pulse. It is not a count of beats delivered to the renderer.
         */
        if (beatsStopAfterMs !== null && Date.now() - startedAt > beatsStopAfterMs) {
          if (!beatsBlocked) {
            beatsBlocked = true;
            log(`--stop-beats-after reached: no further beats will reach the renderer`);
          }
          return;
        }

        broadcast(IPC.healthChanged, health);
      },

      // SENTINEL's two real sources, and TRACE's. All four are pass-through:
      // main validates shape at the socket boundary in ws-client.ts and does not
      // interpret further, because the rendering decisions belong in the
      // renderer and the token is the only thing main is guarding here.
      onAuditHistory: (entries) => {
        lastAudit = entries;
        log(`audit history → renderer: ${entries.length} entries`);
        broadcast(IPC.auditHistory, entries);
      },
      onAuditAppended: (entry) => {
        lastAudit = [...lastAudit, entry].slice(-200);
        broadcast(IPC.auditAppended, entry);
      },
      onPtySessions: (sessions) => {
        lastPtySessions = sessions;
        log(`pty sessions → renderer: ${sessions.length}`);
        broadcast(IPC.ptySessions, sessions);
      },
      onTranscriptLine: (line) => broadcast(IPC.transcriptLine, line),

      // The two halves of a push-to-talk round trip. Neither of them decides
      // anything here — both go straight to the controller, which owns the one
      // copy of "is the microphone claimed".
      onVoiceAck: (action, active) => ptt.onAck(action, active),
      onVoiceRefused: (action, detail) => ptt.onRefused(action, detail),

      // The daemon is authoritative for agent state. Nothing emits
      // evt.agent.state today, so in practice the Alt+1…6 dev cycler still owns
      // the sphere — but the wiring is live, and the moment core/ grows a brain
      // this takes over with no further change here.
      onAgentState: (state) => {
        log(`agent state: ${state}`);
        broadcast(IPC.agentStateChanged, state);
      },

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
    ipcMain.handle(IPC.getSnapshot, () => ({
      connection: connection?.current ?? { phase: 'offline' },
      health: lastHealth,
      audit: lastAudit,
      ptySessions: lastPtySessions,
      mic: lastMic,
    }));
    ipcMain.on(IPC.retryConnection, () => connection?.retryNow());
    // The renderer reports a key EDGE. It does not get to name an action, in
    // keeping with the rest of this bridge — main decides what an edge means.
    ipcMain.on(IPC.pttEdge, (_event, edge: unknown) => {
      if (edge === 'down' || edge === 'up') ptt.keyEdge(edge);
    });
    ipcMain.on(IPC.pttSetMode, (_event, mode: unknown) => {
      if (mode !== 'toggle' && mode !== 'hold') return;
      applyPttMode(mode);
    });
    ipcMain.on(IPC.windowMinimize, (event) =>
      BrowserWindow.fromWebContents(event.sender)?.minimize(),
    );
    ipcMain.on(IPC.windowClose, (event) => BrowserWindow.fromWebContents(event.sender)?.close());

    // Dev only. In a packaged build the channel is simply never handled, so a
    // renderer that somehow sent on it would be talking to nothing.
    if (isDev) {
      ipcMain.on(IPC.devMetrics, (_event, line: string) => log(`metrics ${line}`));

      /**
       * Geometry, reported by Electron itself rather than by GetWindowRect.
       *
       * Last session's "1382x736" came from a Win32 GetWindowRect, which
       * includes the invisible DWM resize border and is therefore 16px larger
       * per axis than the content. Measuring the thing we actually specify —
       * content — from the process that owns it removes that whole class of
       * error from the numbers.
       */
      const reportGeometry = (): void => {
        const [win] = BrowserWindow.getAllWindows();
        if (!win || win.isDestroyed()) return;
        const c = win.getContentBounds();
        const b = win.getBounds();
        const primary = screen.getPrimaryDisplay();
        log(
          `geometry content=${c.width}x${c.height}@${c.x},${c.y} ` +
            `window=${b.width}x${b.height}@${b.x},${b.y} ` +
            `workArea=${primary.workAreaSize.width}x${primary.workAreaSize.height} ` +
            `screen=${primary.size.width}x${primary.size.height} ` +
            `scale=${primary.scaleFactor} ` +
            `maximized=${win.isMaximized()} fullscreen=${win.isFullScreen()}`,
        );
      };
      setInterval(reportGeometry, 2000);
    }

    // The window is created only AFTER every handler is registered. The
    // renderer calls zoey.bootstrap() as its first act; creating the window
    // first leaves a window — small, but real — in which that invoke rejects
    // with "no handler registered" and the surface comes up with no GPU tier.
    createOrbWindow({ isDev, rendererUrl });

    /**
     * §R.8 item 8. A display change can alter the refresh rate out from under
     * the sphere's frame divider, which would produce a wrong frame rate with
     * no error anywhere. Tell the renderer to throw away its refresh estimate
     * and measure again.
     *
     * `display-metrics-changed` covers a mode change on an existing display;
     * added/removed cover docking and undocking. All three matter, and all
     * three also invalidate the saved window position, which is re-validated on
     * the next launch by window-state.ts::isReachable.
     */
    const onDisplaysChanged = (what: string) => () => {
      const primary = screen.getPrimaryDisplay();
      log(
        `display ${what}: work area ${primary.workAreaSize.width}x${primary.workAreaSize.height}, ` +
          `scale ${primary.scaleFactor} — asking the renderer to re-probe refresh`,
      );
      broadcast(IPC.displayChanged, null);
    };
    screen.on('display-metrics-changed', onDisplaysChanged('metrics-changed'));
    screen.on('display-added', onDisplaysChanged('added'));
    screen.on('display-removed', onDisplaysChanged('removed'));

    /**
     * Toggle owns the global chord; hold gives it up.
     *
     * They cannot coexist. A registered global shortcut consumes the keydown
     * before any window sees it, and `globalShortcut` has no key-release
     * callback — so hold, which needs both edges, can only be served by the
     * renderer's own keydown/keyup, which only fire when the Orb has focus.
     */
    const applyPttMode = (mode: PttMode): void => {
      ptt.setMode(mode);
      if (mode === 'toggle') {
        registerChord();
      } else {
        globalShortcut.unregister(PTT_CHORD);
        ptt.setChord(PTT_CHORD, false);
        log(`ptt mode hold — ${PTT_CHORD} released; hold works only while the Orb has focus`);
      }
    };

    const initialMode: PttMode =
      process.argv.includes('--ptt-mode=hold') ? 'hold' : 'toggle';
    applyPttMode(initialMode);

    // Hold mode's key-up will never arrive if the window loses focus mid-press.
    for (const win of BrowserWindow.getAllWindows()) {
      win.on('blur', () => ptt.focusLost());
    }

    // A microphone claim must not outlive the surface that made it. Registered
    // here rather than beside the other app-level handlers because `ptt` lives
    // in this scope, and reaching it from outside would mean hoisting a mutable
    // reference for no gain.
    app.on('before-quit', () => ptt.shutdown());
    app.on('will-quit', () => globalShortcut.unregisterAll());

    connection.start();
  });

  app.on('window-all-closed', () => app.quit());

  app.on('before-quit', () => {
    connection?.dispose();
    connection = null;
  });
}
