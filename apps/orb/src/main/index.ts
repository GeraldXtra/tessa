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

import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { AGENT_STATES } from '@zoey/protocol';

import { developmentCsp, PRODUCTION_CSP } from '../shared/csp.ts';
import {
  IPC,
  type AuditEntry,
  type BootstrapInfo,
  type ConnectionStatus,
  type DaemonHealth,
  type MicState,
  type PermissionRequest,
  type PtySession,
  type PttMode,
} from '../shared/ipc-contract.ts';
import { gpuFeatureSummary, probeGpu } from './gpu-probe.ts';
import { PttController } from './ptt-controller.ts';
import { isThemeId, loadTheme, orbThemePath, saveTheme } from './theme-state.ts';
import { createOrbWindow, hardenWebContents, isInstrumentedLaunch } from './window.ts';
import { DaemonConnection } from './ws-client.ts';

const isDev = !app.isPackaged;
const rendererUrl = process.env['ELECTRON_RENDERER_URL'];

/**
 * Dev capture needs the compositor to keep producing frames for a window
 * nobody is looking at.
 *
 * `capturePage()` removed the FOREGROUND dependency from measurement. It did
 * not remove the VISIBILITY one: Chromium tracks native window occlusion on
 * Windows and stops producing frames for a covered window, so a capture of one
 * fails with `UnknownVizError` — which is exactly what happened the moment a
 * run stopped maximising the window first.
 *
 * Turning occlusion tracking off keeps the renderer drawing while covered.
 * Scanned at module scope because `appendSwitch` has to run before the app is
 * ready, and gated on the capture flag so a normal launch keeps Chromium's
 * power-saving behaviour intact.
 */
const wantsCapture = isDev && process.argv.some((a) => a.startsWith('--capture-every='));
if (wantsCapture) {
  app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion');
}

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

/**
 * Dev-only `--fixture-transcript`. A LAYOUT FIXTURE, and nothing else.
 *
 * The long-answer rendering has to be verified against a long answer, and the
 * daemon's router replies in one sentence — so there is no way to see the
 * three-paragraph case without supplying one. This injects two lines through
 * the real IPC path so the actual render is exercised, not a mock of it.
 *
 * It is fabricated text and it is therefore fenced hard: dev builds only, off
 * unless the flag is passed, logged loudly when it fires, and the content says
 * what it is in its own first words so a screenshot of it can never be mistaken
 * for something the daemon said. The ban on fabricated data is about the
 * surface asserting invented values as real; a labelled fixture used to measure
 * a layout asserts nothing.
 */
const FIXTURE_LINES = [
  {
    role: 'user',
    text: 'fixture: what is causing the slowdown',
  },
  {
    role: 'assistant',
    text:
      'FIXTURE TEXT, not a real answer — the three main causes are memory pressure, ' +
      'disk contention and thermal throttling, and they tend to arrive in that order. ' +
      'Memory pressure shows up first because the working set exceeds physical RAM and ' +
      'the allocator starts returning pages that have to be faulted back in. Disk ' +
      'contention follows once swapping begins, because the same spindle is now serving ' +
      'both the page file and whatever the application was actually trying to read. ' +
      'Thermal throttling is last and is usually a symptom rather than a cause: sustained ' +
      'load raises the package temperature until the processor reduces its own clock, at ' +
      'which point everything above gets slower and the queue depth grows further. On a ' +
      'two-core part the effect compounds, because there is no spare core to absorb the ' +
      'work while one is stalled on a page fault.',
  },
] as const;

/**
 * Dev-only `--fixture-approval=<kind>[,<kind>…]`. APPROVAL CARD FIXTURES.
 *
 * ─── why these exist, and what they are not ───
 * The card cannot be proven against a live daemon in this session: `core/` has
 * no handler for `cmd.permission.respond` (see the report), and the only path
 * that emits `evt.permission.request` runs inside a voice turn, which needs a
 * daemon this session may not start and a microphone it may not touch.
 *
 * These drive the REAL path — main's pending map, the real IPC channel, the
 * real store, the real component — with a payload main invented. What they
 * prove is rendering, editing, stacking, the one-shot guard and the escaping.
 * What they cannot prove is that anything EXECUTES, and nothing here pretends
 * otherwise: every fixture carries `fixture: true`, the card draws a banner
 * saying so, and main refuses to put a fixture decision on the wire.
 *
 * The kinds are chosen to be the four cases that are hard, not the easy one:
 *
 *   tweet   the dictation case, verbatim from the brief — Whisper turned
 *           "tweet that I'm building an AI assistant" into this. Editing it is
 *           the primary path, not a fallback.
 *   markup  angle brackets, an entity, and text that TRIES to look like the
 *           card's own chrome and buttons.
 *   huge    5,000 characters, to prove APPROVE and REJECT stay reachable.
 *   two     two independent requests, to prove answering one leaves the other
 *           untouched — the failure that would be worst and least visible.
 */
const APPROVAL_FIXTURES: Record<string, Omit<PermissionRequest, 'receivedAt' | 'fixture'>[]> = {
  tweet: [
    {
      requestId: 'fixture-tweet-01',
      tier: 'red',
      tool: 'x.post',
      provenance: 'agent',
      expiresAt: '',
      args: {
        text: "Tweet, that's I am, Beauty and AI assis...",
        account: '@gerald',
      },
    },
  ],
  markup: [
    {
      requestId: 'fixture-markup-01',
      tier: 'red',
      tool: 'x.post',
      provenance: 'external',
      expiresAt: '',
      args: {
        text:
          '<script>alert(1)</script> <button class="approval__btn">approve</button> ' +
          '&lt;already-escaped&gt; <b>bold?</b> — and a line that lies: ' +
          '"ZOEY · approval required · red · APPROVE / REJECT"',
        replyTo: '<img src=x onerror="1">',
      },
    },
  ],
  huge: [
    {
      requestId: 'fixture-huge-01',
      tier: 'red',
      tool: 'shell.execute',
      provenance: 'agent',
      expiresAt: '',
      args: {
        // Exactly 5,000 characters, and every 100th is marked so the top and
        // the bottom of the box can be told apart in a capture.
        command: Array.from({ length: 50 }, (_, i) =>
          `[${String(i * 100).padStart(4, '0')}]`.padEnd(100, ' abcdefghij'),
        )
          .join('')
          .slice(0, 5000),
      },
    },
  ],
  two: [
    {
      requestId: 'fixture-two-A',
      tier: 'red',
      tool: 'fs.delete',
      provenance: 'agent',
      expiresAt: '',
      args: { path: 'C:\\Users\\SERIOUS-PC\\Documents\\quarterly-2026.xlsx', toRecycleBin: true },
    },
    {
      requestId: 'fixture-two-B',
      tier: 'red',
      tool: 'x.post',
      provenance: 'agent',
      expiresAt: '',
      args: { text: 'Second request. Answering this one must not touch the first.' },
    },
  ],
};

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

  /**
   * Approvals the DAEMON is holding, as far as main knows. The authority.
   *
   * This map is what makes the one-shot guarantee real. The renderer has its
   * own `sent` flag and disables the buttons, but a renderer flag is a
   * renderer's promise: a double click that lands in the same tick, a replayed
   * IPC message, or simply a bug, would send twice. Main deletes the entry
   * BEFORE it writes to the socket, so the second message finds nothing and is
   * refused. An approval that fires twice is a tweet posted twice or a file
   * deleted twice, and that guarantee does not belong in the sandbox.
   *
   * It is also what stops the renderer inventing a requestId. `approvalRespond`
   * is the only bridge channel carrying a caller-supplied string, and an id
   * that is not in here never reaches the daemon.
   */
  const pendingApprovals = new Map<string, PermissionRequest>();

  /**
   * Decisions that are on the wire and have not been answered yet.
   *
   * The one-shot rule deletes from `pendingApprovals` BEFORE the send, so a
   * second message with the same id finds nothing. That is still exactly right,
   * and it left a gap: when the daemon refuses an edit it puts the request back
   * on ITS side, and main had already thrown away the only copy it had of the
   * request — so the card could not be restored and he would lose a correction
   * he had already typed once.
   *
   * This holds it for the round trip and nothing longer. An entry leaves on the
   * reply, on a resolution, or with the daemon instance.
   */
  const refusableApprovals = new Map<string, PermissionRequest>();

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

    /**
     * The theme, and the rule that stops a measurement overwriting his choice.
     *
     * This is the window-state failure, pre-empted. `orb-window.json` was
     * corrupted by this session's own harness — a resize the persistence layer
     * could not tell apart from a deliberate one — and the owner's Orb opened
     * at 984x652 for days. A theme is easier to get wrong and harder to notice,
     * because a wrong colour looks like a choice.
     *
     * So an INSTRUMENTED launch (--force-*, --capture-*, --dev-*, --fixture-*,
     * --probe-*, --stop-*, --ptt-*) neither reads nor writes the theme file.
     * `--force-theme=<id>` exists so a capture run can hold a palette, and it
     * matches `--force-` precisely so it can never persist.
     */
    const instrumented = isInstrumentedLaunch();
    const forcedTheme = (() => {
      if (!isDev) return null;
      const flag = process.argv.find((a) => a.startsWith('--force-theme='));
      if (!flag) return null;
      const value = flag.slice('--force-theme='.length);
      return isThemeId(value) ? value : null;
    })();

    const themeLoad = instrumented
      ? { theme: 'cyan' as const, reason: 'instrumented launch — theme file not read' }
      : loadTheme();
    const theme = forcedTheme ?? themeLoad.theme;
    const themeReason = forcedTheme
      ? `--force-theme=${forcedTheme} (instrumented; will NOT be saved)`
      : themeLoad.reason;
    log(`theme: ${theme} — ${themeReason}`);

    const bootstrap: BootstrapInfo = {
      surfaceVersion: app.getVersion(),
      isDev,
      gpu,
      theme,
      themeReason,
      probeGeometryMs: probeFlagMs('probe-geometry'),
      probePulseMs: probeFlagMs('probe-pulse'),
      probeLimbMs: probeFlagMs('probe-limb'),
      probeCentreMs: probeFlagMs('probe-centre'),
      devOverlay: isDev && process.argv.includes('--dev-overlay'),
      forcedState: forcedState(),
      devScript: (() => {
        if (!isDev) return null;
        const flag = process.argv.find((a) => a.startsWith('--dev-drive='));
        return flag ? flag.slice('--dev-drive='.length) : null;
      })(),
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

    /**
     * Drop every pending approval and tell the renderer why.
     *
     * One function, one caller in production — the connection going
     * non-connected. Extracted so the daemon-death path is a named thing that
     * can be invoked directly by `--fixture-daemon-death`, rather than being
     * reachable only by killing a daemon this session may not start.
     */
    const invalidatePendingApprovals = (why: string): number => {
      if (pendingApprovals.size === 0) return 0;
      const ids = [...new Set([...pendingApprovals.keys(), ...refusableApprovals.keys()])];
      pendingApprovals.clear();
      // Anything mid-flight dies with the daemon too — its reply can never
      // arrive over a socket that closed, and the process it was addressed to
      // no longer exists.
      refusableApprovals.clear();
      log(
        `INVALIDATING ${ids.length} pending approval(s) — ${why}. ` +
          `ids: ${ids.join(', ')}. The daemon no longer holds them.`,
      );
      for (const requestId of ids) {
        broadcast(IPC.approvalCleared, { requestId, reason: 'daemonRestarted' });
      }
      return ids.length;
    };

    /* ── push-to-talk ─────────────────────────────────────────────────────── */

    /**
     * Dev fixture hooks, armed only by a --fixture-* flag. Empty otherwise.
     *
     * An array rather than one slot because there are two fixtures now, and
     * they must be able to coexist: a themed capture wants the approval card
     * AND the transcript on screen at once.
     */
    const onSnapshotHooks: (() => void)[] = [];

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

      /**
       * A red-tier action is waiting on the owner. CONTRACT §4.1.
       *
       * Held here as well as pushed, because the push channel only delivers
       * what happens NEXT and this can arrive while the renderer is still
       * parsing its bundle — the race that left SENTINEL empty while main's log
       * said it had forwarded 100 entries. Losing THIS one would leave a red
       * action pending for 30 minutes with no card ever drawn for it.
       */
      onApprovalRequest: (request) => {
        pendingApprovals.set(request.requestId, request);
        log(`approval pending: ${request.requestId} (${pendingApprovals.size} open)`);
        broadcast(IPC.approvalRequested, request);
      },

      /**
       * A handshake completed. Apply whichever of Session 1's two rulings fits.
       *
       * `changed === false` on a reconnect is the case that used to be handled
       * wrongly: the same daemon is still holding the same requests, so the
       * cards that survived the outage are legitimate and become actionable
       * again with no further ceremony.
       */
      onDaemonInstance: (instance, changed) => {
        if (!changed) {
          if (pendingApprovals.size > 0) {
            log(
              `daemon instance ${instance} unchanged — ${pendingApprovals.size} card(s) are ` +
                `still live and actionable again`,
            );
          }
          return;
        }
        invalidatePendingApprovals(
          `the daemon restarted (now ${instance}); a new process has forgotten every ` +
            `request the old one held`,
        );
      },

      /**
       * The daemon refused. Whether the card comes back depends entirely on
       * whether the daemon put the request back, and that is not guesswork —
       * `core/brain/executor.py::execute_approved` pops the request first and
       * restores it on exactly one path.
       *
       *   protocol.badEnvelope  `resolve_edit` rejected the edit — a key that
       *                         was not in the request, a changed type, or over
       *                         the 16 KB cap. executor.py restores the request
       *                         (`self.approvals.pending[request_id] = pending`)
       *                         so he does not lose a correction he already
       *                         made. The card MUST come back, with his text.
       *   notFound              never was, or already answered. Gone.
       *   permission.expired    the 30-minute window closed. Gone.
       *   internal              two sub-cases, both gone: the tool ran and
       *                         failed on its own terms (NOT restored — an
       *                         attempted action must not be re-offered), or
       *                         the tool has no executor wired.
       */
      onApprovalRefused: (requestId, code, message) => {
        const stillPending = code === 'protocol.badEnvelope';
        if (stillPending) {
          const request = refusableApprovals.get(requestId);
          if (request) pendingApprovals.set(requestId, request);
        }
        refusableApprovals.delete(requestId);
        log(
          `approval REFUSED ${requestId}: ${code} — ${message} ` +
            `(request ${stillPending ? 'is still pending, card restored' : 'is gone'})`,
        );
        broadcast(IPC.approvalRefused, {
          requestId,
          code,
          message,
          requestStillPending: stillPending,
        });
      },

      onApprovalResolved: (requestId, decision, decidedBy) => {
        // May be the Console answering, or the daemon expiring it. Either way
        // this request is gone and the card must not stay approvable.
        refusableApprovals.delete(requestId);
        const had = pendingApprovals.delete(requestId);
        log(
          `approval resolved: ${requestId} → ${decision} by ${decidedBy}` +
            `${had ? '' : ' (not one of ours — clearing the card anyway)'}`,
        );
        broadcast(IPC.approvalCleared, { requestId, reason: 'resolved', decision });
      },

      onStatus: (status: ConnectionStatus) => {
        // Log every phase transition. Verification depends on being able to
        // prove, from the log alone, that an offline Orb opened no sockets.
        const changed = status.phase !== lastPhase;
        if (changed) {
          const from = lastPhase;
          lastPhase = status.phase;
          log(`connection: ${status.phase}${status.detail ? ` — ${status.detail}` : ''}`);

          /**
           * A DROPPED SOCKET NO LONGER CLEARS ANYTHING, and that reversal is
           * the important part of this revision.
           *
           * The previous build invalidated every card the moment the link went
           * down. Session 1 has since ruled that a pending request SURVIVES the
           * deciding surface's disconnect: the daemon keeps holding it and any
           * surface may decide it. So clearing here destroyed cards for actions
           * that were still live and still waiting on him — and worse, it did
           * so silently enough to look correct.
           *
           * What actually kills a request is the DAEMON restarting, because
           * `ApprovalGate.pending` is an in-memory dict rebuilt per process.
           * That is detected on the next handshake by comparing the daemon
           * instance, not here. See `onDaemonInstance`.
           *
           * The cards stay, and go un-actionable instead: main will refuse to
           * send with no socket, and the renderer greys them and says the link
           * is down rather than pretending they can still be answered.
           */
          if (status.phase !== 'connected' && from !== null && pendingApprovals.size > 0) {
            log(
              `connection is ${status.phase} with ${pendingApprovals.size} approval(s) open — ` +
                `KEEPING them. A pending request survives this surface's disconnect ` +
                `(it dies only if the daemon restarts).`,
            );
          }
        }
        for (const window of BrowserWindow.getAllWindows()) {
          if (!window.isDestroyed()) window.webContents.send(IPC.connectionChanged, status);
        }
      },
    });

    ipcMain.handle(IPC.bootstrap, () => bootstrap);
    ipcMain.handle(IPC.getConnection, () => connection?.current ?? { phase: 'offline' });
    ipcMain.handle(IPC.getSnapshot, () => {
      // Dev fixture hooks only; empty in every normal run. See FIXTURE_LINES.
      // Run BEFORE the snapshot is built so a fixture that seeds main's own
      // state is included in it rather than racing the push channel.
      for (const hook of onSnapshotHooks) hook();
      return snapshot();
    });
    const snapshot = () => ({
      connection: connection?.current ?? { phase: 'offline' },
      health: lastHealth,
      audit: lastAudit,
      ptySessions: lastPtySessions,
      mic: lastMic,
      approvals: [...pendingApprovals.values()],
    });

    /**
     * The owner's answer, and the last gate before the wire.
     *
     * Four refusals, in order, and each one is a real failure mode:
     *
     *  1. A malformed message. The renderer is sandboxed, not trusted.
     *  2. A decision outside `approve|deny`. CONTRACT §5.1 — `expired` is the
     *     daemon's alone, and a surface sending it would be a violation.
     *  3. An id main is not holding. This is the double-approve guard AND the
     *     forged-id guard in one: the entry is deleted before the send, so the
     *     second click of a double click finds nothing. It is also why a
     *     renderer cannot answer a request that was never made.
     *  4. A fixture. `--fixture-approval` produces cards with no daemon request
     *     behind them; putting one of those on the wire would be fabricating a
     *     response to something that never happened.
     */
    ipcMain.on(IPC.approvalRespond, (_event, message: unknown) => {
      if (typeof message !== 'object' || message === null) return;
      const { requestId, decision, editedArgs } = message as {
        requestId?: unknown;
        decision?: unknown;
        editedArgs?: unknown;
      };
      if (typeof requestId !== 'string' || !requestId) return;
      if (decision !== 'approve' && decision !== 'deny') {
        log(`!! refused an approval response with decision=${JSON.stringify(decision)}`);
        return;
      }
      /**
       * `editedArgs` must be a plain object or absent. Arrays excluded on
       * purpose: `resolve_edit` does `set(edited) - set(pending.args)` on it,
       * and an array's "keys" are indices, so `["x"]` would reach the daemon as
       * an unknown-key refusal rather than as the type error it is. Refusing it
       * here says the true thing one hop earlier.
       */
      const edited =
        typeof editedArgs === 'object' && editedArgs !== null && !Array.isArray(editedArgs)
          ? (editedArgs as Record<string, unknown>)
          : undefined;
      if (editedArgs !== undefined && edited === undefined) {
        log(`!! refused an approval response whose editedArgs was not a plain object`);
        return;
      }
      if (edited && decision === 'deny') {
        // Nonsensical, and worth refusing rather than quietly dropping: a deny
        // executes nothing, so edited arguments attached to one can only mean
        // the surface has confused two code paths.
        log(`!! refused a 'deny' carrying editedArgs for ${requestId}`);
        return;
      }

      const request = pendingApprovals.get(requestId);
      if (!request) {
        // Loud. This is either the second half of a double click — in which
        // case the guard just did its job and that is worth seeing — or a
        // renderer answering something that does not exist.
        log(
          `!! refused approval '${decision}' for ${requestId}: main is not holding that ` +
            `request. Either it was already answered (double-fire refused) or it never existed.`,
        );
        return;
      }

      // Delete BEFORE the send. Everything after this point is unreachable for
      // a second message carrying the same id. Kept aside so a refused edit can
      // put the card back — see refusableApprovals.
      pendingApprovals.delete(requestId);
      refusableApprovals.set(requestId, request);

      if (request.fixture) {
        log(
          `FIXTURE approval '${decision}'${edited ? ' (EDITED)' : ''} for ${requestId} — ` +
            `NOT sent to the daemon. There is no daemon request behind a fixture card.`,
        );
        refusableApprovals.delete(requestId);
        broadcast(IPC.approvalCleared, { requestId, reason: 'resolved', decision });
        return;
      }

      const result = connection?.sendPermissionResponse(requestId, decision, edited) ?? {
        ok: false as const,
        detail: 'no connection object',
      };
      if (result.ok) {
        // The literal frame, so what went on the wire can be read out of the
        // log rather than inferred from the code that built it. The ARGUMENT
        // VALUES are in it, and that is deliberate here and only here: this is
        // the record of what he authorised, and it is the one line that answers
        // "what did the Orb actually send".
        log(`APPROVAL-OUT ${result.frame}`);
      } else {
        // Nothing left the machine, so the request is untouched daemon-side.
        // Put it straight back rather than clearing: under Session 1's ruling 2
        // it is still pending and still his to answer once the link returns.
        log(`!! could not send approval '${decision}' for ${requestId}: ${result.detail}`);
        refusableApprovals.delete(requestId);
        pendingApprovals.set(requestId, request);
        broadcast(IPC.approvalRefused, {
          requestId,
          code: 'unavailable',
          message: result.detail,
          requestStillPending: true,
        });
      }
    });

    /**
     * Persist the theme. Display already changed in the renderer; this only
     * decides what the NEXT launch paints.
     *
     * Refuses to write on an instrumented launch, for the reason spelled out
     * where the theme is loaded: a capture run holding ember must not leave
     * ember behind. That is the window-state failure, and it cost the owner a
     * mis-sized Orb for days before anyone traced it.
     */
    ipcMain.on(IPC.themeSet, (_event, value: unknown) => {
      if (!isThemeId(value)) {
        log(`!! refused theme ${JSON.stringify(value)} — not one of the five`);
        return;
      }
      if (instrumented) {
        log(`theme ${value} NOT saved — instrumented launch (would overwrite his choice)`);
        return;
      }
      saveTheme(value);
      log(`theme ${value} saved to ${orbThemePath()}`);
    });

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

    /**
     * `--capture-every=<ms>` — dev only. Writes the window's own pixels.
     *
     * A GDI screen grab photographs whatever owns the foreground at those
     * coordinates, which under load is repeatedly not the Orb: a whole run of
     * captures came back refused, and one earlier came back as the owner's
     * browser. `capturePage()` asks the window for its own contents, so
     * occlusion, focus and z-order stop being inputs to the measurement — the
     * same move as reading the drawing buffer instead of the screen.
     *
     * It captures the full window INCLUDING the DOM, which `gl.readPixels`
     * cannot: the rails, the status bar and the transcript are all DOM.
     */
    const captureEveryMs = (() => {
      if (!isDev) return 0;
      const flag = process.argv.find((a) => a.startsWith('--capture-every='));
      if (!flag) return 0;
      const ms = Number.parseInt(flag.slice('--capture-every='.length), 10);
      return Number.isFinite(ms) && ms >= 500 ? ms : 0;
    })();

    if (captureEveryMs > 0) {
      const dir = process.env['ZOEY_CAPTURE_DIR'];
      const [win] = BrowserWindow.getAllWindows();
      if (dir && win) {
        let n = 0;
        const MAX_CAPTURES = 24;
        const timer = setInterval(() => {
          if (n >= MAX_CAPTURES || win.isDestroyed()) {
            clearInterval(timer);
            return;
          }
          const index = n;
          n += 1;
          void win.webContents
            .capturePage()
            .then((image) =>
              writeFile(join(dir, `cap-${String(index).padStart(2, '0')}.png`), image.toPNG()),
            )
            .then(() => log(`capture cap-${String(index).padStart(2, '0')}.png`))
            .catch((err: unknown) => log(`capture failed: ${String(err)}`));
        }, captureEveryMs);
      } else {
        log('!! --capture-every needs ZOEY_CAPTURE_DIR');
      }
    }

    // Layout fixture. See FIXTURE_LINES — dev only, off by default, and loud.
    if (isDev && process.argv.includes('--fixture-transcript')) {
      /**
       * Fired when the renderer asks for its snapshot, not on a timer.
       *
       * `did-finish-load` means the document loaded, not that React has
       * attached its IPC listeners, and injecting there lost one of two lines.
       * The first version of this fix waited 2000 ms — which is a guess at how
       * long React takes, and a guess is the same class of thing that lost the
       * audit history in the first place.
       *
       * `getSnapshot` is the signal that already exists: the renderer invokes it
       * from its mount effect, and registers `onTranscriptLine` synchronously in
       * that same tick. By the time this handler runs, the listener is attached.
       * No timing assumption, and no fixture code inside the production snapshot
       * path — the fixture waits on an existing signal rather than joining it.
       */
      let fired = false;
      onSnapshotHooks.push(() => {
        if (fired) return;
        fired = true;
        log(`!! FIXTURE TRANSCRIPT injected (${FIXTURE_LINES.length} lines) — NOT from the daemon`);
        FIXTURE_LINES.forEach((line, i) => {
          broadcast(IPC.transcriptLine, {
            messageId: `fixture-${i}`,
            role: line.role,
            provenance: line.role === 'user' ? 'human' : 'agent',
            text: line.text,
            ts: new Date().toISOString(),
          });
        });
      });
    }

    /**
     * `--fixture-approval=<kind>[,<kind>…]` — dev only. See APPROVAL_FIXTURES.
     *
     * Seeded into main's real pending map, so the cards arrive by BOTH real
     * paths: the snapshot (which is how a request that beat the renderer's
     * mount gets drawn) and the push channel (which is how a live one does).
     * `approvalArrived` is idempotent by requestId, so receiving each twice is
     * the point rather than a problem — it proves the replay path cannot
     * duplicate a card.
     */
    const fixtureApproval = isDev
      ? process.argv.find((a) => a.startsWith('--fixture-approval='))?.slice('--fixture-approval='.length)
      : undefined;
    if (fixtureApproval) {
      const kinds = fixtureApproval.split(',').map((k) => k.trim()).filter(Boolean);
      const expiresAt = new Date(Date.now() + 30 * 60_000).toISOString();
      let seeded = 0;
      for (const kind of kinds) {
        const set = APPROVAL_FIXTURES[kind];
        if (!set) {
          log(`!! --fixture-approval: unknown kind '${kind}' (have ${Object.keys(APPROVAL_FIXTURES).join(', ')})`);
          continue;
        }
        for (const base of set) {
          const request: PermissionRequest = {
            ...base,
            expiresAt,
            receivedAt: Date.now(),
            fixture: true,
          };
          pendingApprovals.set(request.requestId, request);
          seeded += 1;
        }
      }
      log(
        `!! FIXTURE APPROVAL injected: ${seeded} card(s) from [${kinds.join(', ')}] — ` +
          `NOT from the daemon, and no decision on one will ever reach the wire`,
      );
      onSnapshotHooks.push(() => {
        for (const request of pendingApprovals.values()) {
          if (request.fixture) broadcast(IPC.approvalRequested, request);
        }
      });
    }

    /**
     * `--fixture-daemon-death=<ms>` — dev only. Runs the REAL invalidation.
     *
     * The brief asks what happens to an open card when the daemon dies. The
     * honest way to answer that is to kill a daemon, and this session cannot:
     * it may not start one (the two-daemon mistake forked the audit chain once
     * already), and the owner's is not running.
     *
     * So this calls `invalidatePendingApprovals` — the identical function the
     * connection handler calls, with the identical broadcast — on a timer. What
     * it proves is that the invalidation path works end to end and what the
     * card does when it fires. What it does NOT prove is that a real socket
     * close reaches this function; that is one `if` in the status handler
     * above, and it is stated as reasoning rather than as a measurement.
     */
    const deathAfterMs = (() => {
      if (!isDev) return 0;
      const flag = process.argv.find((a) => a.startsWith('--fixture-daemon-death='));
      if (!flag) return 0;
      const ms = Number.parseInt(flag.slice('--fixture-daemon-death='.length), 10);
      return Number.isFinite(ms) && ms > 0 ? ms : 0;
    })();
    /**
     * `--probe-permission-wire` — dev only. ONE frame, to answer one question.
     *
     * Item 1 of the brief: what does the daemon ACTUALLY accept to approve a
     * pending action? `core/server.py` answers that in the source —
     * `cmd.permission.respond` is in KNOWN_COMMANDS at line 125 but absent from
     * the handler map at 485–493, so `_dispatch` falls through to line 495 and
     * returns `err.internal`. This puts that on the wire instead of leaving it
     * as a reading of somebody else's code.
     *
     * WHY THIS IS SAFE TO SEND AT A DAEMON I DO NOT OWN:
     *   • It is a contract-legal command from a legitimate surface (§5.1).
     *   • The requestId is deliberately synthetic and matches nothing, so it
     *     cannot approve anything. There is nothing pending to approve.
     *   • `decision` is `deny`. If a handler DID exist and did match something,
     *     the outcome would be a refusal, never an execution. The probe is
     *     built so that being wrong about the daemon still cannot run a red
     *     action.
     */
    const wireProbe = isDev
      ? process.argv.find((a) => a === '--probe-permission-wire' || a.startsWith('--probe-permission-wire='))
      : undefined;
    if (wireProbe) {
      /**
       * Optional value: `--probe-permission-wire=<requestId>`.
       *
       * With no value it sends a synthetic id and `deny`. With one it sends
       * `approve` AND an `editedArgs` object for that id, which is how the
       * card's refusal rendering gets exercised by a real daemon error rather
       * than by a store call: point it at a fixture card's id, and the reply
       * comes back through `onApprovalRefused` and lands on that actual card.
       *
       * SAFE TO AIM AT A LIVE DAEMON, and the ordering is why. The handler
       * looks the request up BEFORE it looks at anything else
       * (`core/server.py:1108`), so a `requestId` that is not pending returns
       * `err.notFound` and never reaches `execute_approved`. The ids used here
       * are `fixture-…` strings, and a real one is `secrets.token_hex(16)` —
       * 32 hex characters — so a collision is not merely unlikely, it is
       * structurally impossible.
       */
      const target = wireProbe.includes('=')
        ? wireProbe.slice(wireProbe.indexOf('=') + 1)
        : '';
      /**
       * Wait for the RENDERER, not just for the socket.
       *
       * The first run of this probe fired ~4 ms after the handshake, which is
       * long before the 725 kB bundle has parsed and registered
       * `onApprovalRefused`. The daemon's refusal arrived, main classified it
       * correctly, and it was broadcast at a window with no listener — so the
       * log said REFUSED and the card showed nothing. That is the same race
       * that once lost the audit history and half the transcript fixture.
       *
       * It is a PROBE artefact and not a product defect: in the real flow the
       * only thing that can provoke a refusal is a decision the renderer just
       * sent, so the renderer is mounted by construction. The probe is the one
       * caller that can talk to the daemon with nobody listening, so the probe
       * is what has to wait.
       */
      let rendererUp = false;
      onSnapshotHooks.push(() => {
        rendererUp = true;
      });
      let probed = false;
      const probe = setInterval(() => {
        if (probed || !rendererUp) return;
        if (connection?.current.phase !== 'connected') return;
        probed = true;
        clearInterval(probe);
        const requestId = target || 'orb-wire-probe-not-a-real-request';
        const result = target
          ? connection.sendPermissionResponse(requestId, 'approve', {
              text: 'PROBE edited value — this request does not exist daemon-side',
            })
          : connection.sendPermissionResponse(requestId, 'deny');
        log(
          result.ok
            ? `PROBE sent cmd.permission.respond${target ? ' WITH editedArgs' : ''}: ${result.frame}`
            : `PROBE could not send: ${result.detail}`,
        );
      }, 500);
      setTimeout(() => clearInterval(probe), 60_000);
    }

    if (deathAfterMs > 0) {
      setTimeout(() => {
        const n = invalidatePendingApprovals(
          '--fixture-daemon-death fired (the real invalidation path, on a timer)',
        );
        log(`!! FIXTURE DAEMON DEATH: invalidated ${n} card(s)`);
      }, deathAfterMs);
    }

    connection.start();
  });

  app.on('window-all-closed', () => app.quit());

  app.on('before-quit', () => {
    connection?.dispose();
    connection = null;
  });
}
