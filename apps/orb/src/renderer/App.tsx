/**
 * The collapsed layout — spec §8.1's "design the collapsed layout first".
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │ status bar                                          28px │
 *   ├────┬───────────────────────────────┬─────────────────────┤
 *   │rail│         sphere stage          │  drawer (overlay)   │
 *   │ 48 │      floats over the void     │        320          │
 *   └────┴───────────────────────────────┴─────────────────────┘
 *
 * At 1366×768 with a drawer open that is 368px of chrome and ~998px of stage.
 * The four-panel arrangement would leave 478px, which spec §8.1 calls "not a
 * centre stage — a thumbnail". The drawer is an overlay, so the stage never
 * actually shrinks; the sphere is offset inside the scene instead.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { AGENT_STATES, type AgentState } from '@zoey/protocol';

import type { BootstrapInfo, SphereTier } from '../shared/ipc-contract.ts';
import { parseDevScript, runDevScript } from './dev-drive.ts';
import { tokenPx } from './design-tokens.ts';
import { applyTheme, currentTheme, isThemeId, themeForKey, type ThemeId } from './theme.ts';
import {
  approvalArrived,
  approvalCleared,
  approvalsSweepExpired,
} from './state/approval-store.ts';
import { StateDwell } from './state/state-dwell.ts';
import { ApprovalStack } from './layout/ApprovalCard.tsx';
import { Drawer } from './layout/Drawer.tsx';
import { DevOverlay } from './layout/DevOverlay.tsx';
import { LastLine } from './layout/LastLine.tsx';
import { NotificationStack } from './layout/NotificationStack.tsx';
import { Rail } from './layout/Rail.tsx';
import { StatusBar } from './layout/StatusBar.tsx';
import { railById } from './rails/rails.tsx';
import { DomSphere } from './scene/DomSphere.tsx';
import { Sphere } from './scene/Sphere.tsx';
import { probeSphereTier } from './scene/gpu-tier.ts';
import type { ProbeReading, SphereEngine } from './scene/sphere-engine.ts';
import {
  agentStateStore,
  auditStore,
  AUDIT_MAX,
  connectionStore,
  healthStore,
  micStore,
  ptySessionsStore,
  pushHealthSample,
  pushNotification,
  railStore,
  tierStore,
  transcriptStore,
  TRANSCRIPT_MAX,
  useStore,
  type RailId,
} from './state/store.ts';

/**
 * One probe reading as a log line. Three decimals on the offsets: the pass
 * condition for the instrument is that a motionless sphere reads the SAME every
 * time, and a tolerance stated to 0.001 px is a claim that can fail.
 */
function describeProbe(r: ProbeReading): string {
  return (
    `buf=${r.bufW}x${r.bufH} css=${r.cssW}x${r.cssH} ` +
    `c=${r.cx.toFixed(3)},${r.cy.toFixed(3)} ` +
    `dx=${r.dx.toFixed(3)} dy=${r.dy.toFixed(3)} ` +
    `lit=${r.lit} sum=${r.sum} uPulse=${r.uPulse.toFixed(4)} ` +
    `state=${agentStateStore.get()} resize=${r.resizeReason}`
  );
}

export function App() {
  const tier = useStore(tierStore);
  const rail = useStore(railStore);
  const mic = useStore(micStore);

  const [bootstrap, setBootstrap] = useState<BootstrapInfo | null>(null);
  const [tierReason, setTierReason] = useState('probing…');
  const [rendererName, setRendererName] = useState('probing…');
  const [engine, setEngine] = useState<SphereEngine | null>(null);
  const readStats = engine ? engine.stats : null;
  const [showOverlay, setShowOverlay] = useState(false);

  /**
   * The one un-drawn state change, and when it arrived. Spec §4.
   *
   * A single slot, not a map keyed by state. The daemon repeats each state
   * several times per turn — one run saw `speaking` broadcast seven times — and
   * a map lets a later duplicate overwrite the timestamp of the arrival that
   * actually caused the redraw, or leave an entry that is never consumed and is
   * then paired with a redraw seconds later. Both produce a latency figure that
   * is arithmetic on two unrelated instants, which is the same error as pairing
   * a health frame with a different frame's render.
   *
   * A repeat of the state already showing is not a state CHANGE and is ignored;
   * a genuine change replaces whatever was pending, because the sphere will
   * never draw the superseded one.
   */
  const pendingState = useRef<{
    state: string;
    /** When the dwell released it to the store. */
    at: number;
    /** When the daemon's frame arrived. */
    arrivedAt: number;
    /** How long the dwell held it. */
    queuedMs: number;
  } | null>(null);
  const lastArrivedState = useRef<string | null>(null);

  /* ── bootstrap: GPU tier, then the connection feed ─────────────────────── */

  useEffect(() => {
    let alive = true;

    void window.zoey.bootstrap().then((info) => {
      if (!alive) return;
      setBootstrap(info);

      /**
       * Paint the theme before anything else in this callback.
       *
       * It rides the bootstrap rather than a push channel because a push
       * arrives at whatever listener exists, and on first paint there is none —
       * the theme would land a frame late and every launch would flash cyan
       * before turning ember. Main has already validated the id; `isThemeId`
       * here is the second half of that, because a main that sent something
       * unexpected must produce a NAMED fallback rather than an unset accent.
       */
      const wanted: ThemeId = isThemeId(info.theme) ? info.theme : 'cyan';
      const steps = applyTheme(wanted);
      window.zoey.reportMetrics(
        `THEME applied=${wanted} core=${steps.core} body=${steps.body} idle=${steps.idle} ` +
          `reason="${info.themeReason}"${isThemeId(info.theme) ? '' : ` FALLBACK from ${JSON.stringify(info.theme)}`}`,
      );

      // Validated against AGENT_STATES in main before it got here.
      if (info.forcedState) agentStateStore.set(info.forcedState as AgentState);
      if (info.devOverlay) setShowOverlay(true);

      const probe = probeSphereTier(info.gpu);
      tierStore.set(probe.tier);
      setTierReason(probe.reason);
      setRendererName(probe.renderer);
      console.log(`[orb] sphere tier=${probe.tier} — ${probe.reason} (${probe.renderer})`);
    });

    // Pull everything main has already seen, THEN follow the push channels.
    //
    // Push alone loses a race it cannot win: the daemon connects and returns
    // res.audit in milliseconds, while this bundle is still parsing. Main
    // logged "audit history → renderer: 100 entries" and SENTINEL still showed
    // NO DATA, because nothing was listening yet.
    void window.zoey.getSnapshot().then((snap) => {
      if (!alive) return;
      connectionStore.set(snap.connection);
      if (snap.health) {
        healthStore.set(snap.health);
        pushHealthSample(snap.health);
      }
      if (snap.audit.length > 0) auditStore.set([...snap.audit].reverse().slice(0, AUDIT_MAX));
      if (snap.ptySessions.length > 0) ptySessionsStore.set(snap.ptySessions);
      // Unconditional, unlike the two above: `claimed: false` is a real answer
      // and must overwrite the placeholder, not be skipped as "empty".
      micStore.set(snap.mic);
      // Approvals main was already holding. A red action that arrived while
      // this bundle was parsing must not be left with no card.
      for (const request of snap.approvals) approvalArrived(request);
    });
    const offConnection = window.zoey.onConnection((status) => {
      connectionStore.set(status);
      // A dropped link must not leave a frozen uptime on screen looking live.
      if (status.phase !== 'connected') healthStore.set(null);
    });
    const offHealth = window.zoey.onHealth((health) => {
      healthStore.set(health);
      pushHealthSample(health);
    });

    // SENTINEL's two real sources. History seeds the list; the live stream
    // prepends onto it, newest first, bounded so a long-running surface cannot
    // grow without limit.
    const offAuditHistory = window.zoey.onAuditHistory((entries) =>
      auditStore.set([...entries].reverse().slice(0, AUDIT_MAX)),
    );
    const offAuditAppended = window.zoey.onAuditAppended((entry) =>
      auditStore.set([entry, ...auditStore.get()].slice(0, AUDIT_MAX)),
    );
    const offPty = window.zoey.onPtySessions((sessions) => ptySessionsStore.set(sessions));
    const offMic = window.zoey.onMicState((state) => micStore.set(state));
    const offNote = window.zoey.onNotification((note) => pushNotification(note));
    const offApproval = window.zoey.onApprovalRequested((request) => approvalArrived(request));
    const offApprovalCleared = window.zoey.onApprovalCleared((cleared) =>
      approvalCleared(cleared.requestId, cleared.reason, cleared.decision),
    );
    const offTranscript = window.zoey.onTranscriptLine((line) =>
      transcriptStore.set([...transcriptStore.get(), line].slice(-TRANSCRIPT_MAX)),
    );

    // Main has already validated this against AGENT_STATES before sending.
    // Through the dwell, never straight to the store. See state-dwell.ts.
    const dwell = new StateDwell({
      report: (line) => window.zoey.reportMetrics(line),
      release: ({ state, arrivedAt, queuedMs }) => {
        // TWO stamps, deliberately. `arrivedAt` is when the daemon's frame
        // landed; `releasedAt` is when the dwell let it through. The engine
        // reports when it was DRAWN, and the difference between those two
        // intervals is the whole point: one is a rendering latency and the
        // other is a delay this surface chose. Collapsing them into a single
        // "state change → visible" number would quietly turn spec §4's budget
        // into a measurement of my own timer.
        pendingState.current = { state, at: performance.now(), arrivedAt, queuedMs };
        agentStateStore.set(state as AgentState);
      },
    });

    const offAgentState = window.zoey.onAgentState((state) => {
      const at = performance.now();
      const repeat = state === lastArrivedState.current;
      lastArrivedState.current = state;
      window.zoey.reportMetrics(
        `STATE-ARRIVED state=${state} t=${at.toFixed(1)} repeat=${repeat} depth=${dwell.depth}`,
      );
      dwell.submit(state);
    });

    return () => {
      alive = false;
      offConnection();
      offHealth();
      offAgentState();
      offAuditHistory();
      offAuditAppended();
      offPty();
      offTranscript();
      offMic();
      offNote();
      offApproval();
      offApprovalCleared();
      // A pending dwell timer outliving the listener would release a state
      // into a store nobody is reading and leave the sphere on it.
      dwell.dispose();
    };
  }, []);


  /* ── keyboard ──────────────────────────────────────────────────────────── */

  const isDev = bootstrap?.isDev ?? false;

  /**
   * A dead global chord is news, and it must not depend on winning a race.
   *
   * Main registers the shortcut before the renderer has mounted, so its pushed
   * notification arrives at a window with no listener yet — the same race that
   * left SENTINEL empty while main's log said it had forwarded 100 audit
   * entries. `chordRegistered` rides the snapshot, so deriving the message from
   * the state is race-free. Deduped by id against main's push, so a runtime
   * mode switch does not produce two of them.
   */
  useEffect(() => {
    if (mic.mode !== 'toggle' || !mic.chord || mic.chordRegistered) return;
    pushNotification({
      id: 'ptt-chord-failed',
      level: 'error',
      title: 'Push-to-talk shortcut unavailable',
      body: `${mic.chord} is already held by another application. Push-to-talk still works while the Orb has focus.`,
    });
  }, [mic.mode, mic.chord, mic.chordRegistered]);

  /**
   * Ctrl+Shift+<letter> — the theme switcher. NOT dev-gated: this is the
   * owner's display preference, not an instrument.
   *
   * A renderer keydown listener, deliberately not a `globalShortcut`. Taking
   * five OS-wide chords away from every other application on the machine so the
   * Orb can change colour would be indefensible, and the push-to-talk work
   * already established what a global grab costs — it consumes the key even for
   * the app the owner is typing into.
   *
   * `event.code` rather than `event.key`, so the letter is the PHYSICAL key and
   * Shift does not turn it into an uppercase character that has to be matched
   * separately.
   */
  useEffect(() => {
    function onThemeKey(event: KeyboardEvent) {
      if (!event.ctrlKey || !event.shiftKey || event.altKey) return;
      const next = themeForKey(event.code, event.key);
      if (!next || next === currentTheme()) return;
      event.preventDefault();
      const steps = applyTheme(next);
      // Display first, persistence second, and they are separate concerns: the
      // renderer owns what is on screen, main owns what survives a restart, and
      // main refuses to write on an instrumented launch.
      window.zoey.setTheme(next);
      window.zoey.reportMetrics(
        `THEME switched=${next} core=${steps.core} body=${steps.body} idle=${steps.idle}`,
      );
      // The sphere's colours are uniforms resolved once at construction, not
      // CSS. Without this the chrome changes and the sphere does not.
      engine?.retint();
    }
    window.addEventListener('keydown', onThemeKey);
    return () => window.removeEventListener('keydown', onThemeKey);
  }, [engine]);

  /**
   * Expiry sweep. CONTRACT §5.1 — the surface sends NOTHING when a window
   * lapses; `expired` is a value only the daemon may produce.
   *
   * One interval over the whole list rather than a timer per card: the daemon's
   * window is 30 minutes, so second-resolution is far finer than needed, and a
   * timer per card is a timer to leak.
   */
  useEffect(() => {
    const id = window.setInterval(() => {
      const expired = approvalsSweepExpired();
      for (const requestId of expired) {
        window.zoey.reportMetrics(
          `APPROVAL-EXPIRED ${requestId} — invalidated locally, nothing sent (CONTRACT §5.1)`,
        );
      }
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  /**
   * The dev driver. Runs the real click handlers, no OS involved.
   *
   * Deferred one frame past mount so the rails and any snapshot-fed content are
   * in the DOM before a selector is resolved — a driver that raced the first
   * paint would reintroduce exactly the timing guess the fixture just lost.
   */
  const devScript = bootstrap?.devScript ?? null;
  useEffect(() => {
    if (!isDev || !devScript) return;
    const steps = parseDevScript(devScript);
    window.zoey.reportMetrics(`DEV-DRIVE parsed ${steps.length} step(s)`);
    const id = window.setTimeout(() => {
      void runDevScript(
        steps,
        (line) => window.zoey.reportMetrics(line),
        // Validated against the closed set, same as the socket path. A typo in
        // a dev script must report REJECTED rather than quietly leaving the
        // sphere on the previous state and being read as "no visible change".
        (state) => {
          if (!(AGENT_STATES as readonly string[]).includes(state)) return false;
          agentStateStore.set(state as AgentState);
          return true;
        },
      );
    }, 0);
    return () => window.clearTimeout(id);
  }, [isDev, devScript]);

  /* ── push-to-talk, hold mode ───────────────────────────────────────────── */

  /**
   * Ctrl+Alt+Space, from the renderer, for HOLD only.
   *
   * In toggle mode main holds the same chord as a global shortcut, which
   * consumes the keydown before any window sees it — so this listener is dead
   * by construction there, and registering it anyway would double-fire the
   * moment the global registration failed. Gated on the mode instead.
   *
   * The release matcher is deliberately looser than the press matcher. A chord
   * is released one key at a time and in any order: let go of Ctrl first and
   * the Space keyup arrives with `ctrlKey: false`, so a release handler that
   * required the full chord would never fire and the microphone would stay
   * claimed. Any of the three lifting ends the hold.
   */
  const holdMode = mic.mode === 'hold';
  useEffect(() => {
    if (!holdMode) return;
    let held = false;
    if (isDev) window.zoey.reportMetrics('PTT-KEY hold-mode listener attached');

    const isSpace = (e: KeyboardEvent): boolean => e.code === 'Space' || e.key === ' ';

    function onDown(event: KeyboardEvent) {
      if (held || event.repeat) return;
      const match = event.ctrlKey && event.altKey && isSpace(event);
      // Dev-only, and it earned its place: the first hold-mode run produced no
      // edges at all and there was no way to tell whether the window lacked
      // focus, the chord was still globally grabbed, or the matcher was simply
      // wrong about what synthetic input looks like. Renderer console does not
      // reach the process log in a preview build, so it goes through the
      // metrics channel.
      if (isDev && (event.ctrlKey || event.altKey)) {
        window.zoey.reportMetrics(
          `PTT-KEY code=${event.code || '(none)'} key=${JSON.stringify(event.key)} ` +
            `ctrl=${event.ctrlKey} alt=${event.altKey} shift=${event.shiftKey} ` +
            `repeat=${event.repeat} focus=${document.hasFocus()} match=${match}`,
        );
      }
      if (!match) return;
      held = true;
      event.preventDefault();
      window.zoey.pushToTalkEdge('down');
    }

    function onUp(event: KeyboardEvent) {
      if (!held) return;
      if (!isSpace(event) && event.key !== 'Control' && event.key !== 'Alt') return;
      held = false;
      window.zoey.pushToTalkEdge('up');
    }

    window.addEventListener('keydown', onDown);
    window.addEventListener('keyup', onUp);
    return () => {
      window.removeEventListener('keydown', onDown);
      window.removeEventListener('keyup', onUp);
      // Unmounting mid-hold would otherwise strand the claim with no keyup
      // listener left to end it.
      if (held) window.zoey.pushToTalkEdge('up');
    };
  }, [holdMode, isDev]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        railStore.set(null);
        return;
      }

      // The dev state cycler. Phase 1 subscribes to no events, so this is the
      // only way to exercise all six states — and exercising all six is the
      // deliverable, not a convenience.
      if (!isDev || !event.altKey) return;

      // `code` first (layout-independent physical key), then `key` as a
      // fallback. The fallback is not redundant: `code` is derived from the
      // hardware scancode, and synthetic input — on-screen keyboards, remote
      // desktop, accessibility tools, and the keybd_event injection used to
      // verify this build — arrives with scancode 0 and therefore no usable
      // `code`. Matching only `code` makes the shortcut silently dead for all
      // of them.
      // Alt+0 toggles the frame-metrics overlay. Same family as the Alt+1…6
      // state cycler and the only digit it does not already use.
      if (/^Digit0$/.test(event.code) || event.key === '0') {
        setShowOverlay((v) => !v);
        event.preventDefault();
        return;
      }

      const digit =
        /^Digit([1-6])$/.exec(event.code)?.[1] ?? (/^[1-6]$/.test(event.key) ? event.key : null);
      if (!digit) return;

      const index = Number.parseInt(digit, 10) - 1;
      const next = AGENT_STATES[index];
      if (next) {
        agentStateStore.set(next);
        event.preventDefault();
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isDev]);

  /* ── dev metrics → main process log ────────────────────────────────────── */

  useEffect(() => {
    if (!isDev || !readStats) return;
    const id = window.setInterval(() => {
      const s = readStats();
      if (s.publishedAt === 0) return;
      window.zoey.reportMetrics(
        `tier=${s.tier} pts=${s.particles} focused=${s.focused} n=${s.samples} ` +
          `cost=${s.cost.p50.toFixed(2)}/${s.cost.p95.toFixed(2)} ` +
          `raf=${s.raf.p50.toFixed(1)}/${s.raf.p95.toFixed(1)} ` +
          `shown=${s.present.p50.toFixed(1)}/${s.present.p95.toFixed(1)} ` +
          `fps=${s.fps.toFixed(1)} state=${agentStateStore.get()} ` +
          `canvas=${s.canvas.cssW}x${s.canvas.cssH}css/${s.canvas.bufW}x${s.canvas.bufH}buf`,
      );
    }, 5000);
    return () => window.clearInterval(id);
  }, [isDev, readStats]);

  /* ── dev probes: read the drawing buffer, not the screen ───────────────── */

  const geometryMs = bootstrap?.probeGeometryMs ?? 0;
  const pulseMs = bootstrap?.probePulseMs ?? 0;

  /**
   * Geometry (§R.8 item 18f). One full-buffer read per tick.
   *
   * Every value the reader needs is on the line, including the buffer and CSS
   * sizes. A resize that did not take is then self-evident in the data rather
   * than something to be cross-checked against a window rectangle read from
   * outside — the previous run reported a 144×20 client and there was no way to
   * tell from the numbers alone that the leg was void.
   */
  useEffect(() => {
    if (!isDev || geometryMs <= 0 || !engine) return;
    const id = window.setInterval(() => {
      const r = engine.probeFrame('full');
      if (r) window.zoey.reportMetrics(`PROBE-GEO ${describeProbe(r)}`);
    }, geometryMs);
    return () => window.clearInterval(id);
  }, [isDev, geometryMs, engine]);

  /**
   * Pulse (§R.1). One centred full-height column per tick.
   *
   * `sum` is total luminance over the column, which rises and falls as the band
   * travels regardless of WHERE it currently is — the failure of the previous
   * attempt, which watched a ±10 px strip at the equator that the band leaves
   * almost immediately. `uPulse` rides along as the ground truth: the
   * brightness series says what is on screen, the uniform says what the engine
   * thinks it is drawing, and §R.1 needs both to agree.
   */
  useEffect(() => {
    if (!isDev || pulseMs <= 0 || !engine) return;
    const id = window.setInterval(() => {
      const r = engine.probeFrame('column');
      if (!r) return;
      window.zoey.reportMetrics(
        `PROBE-PULSE t=${performance.now().toFixed(0)} uPulse=${r.uPulse.toFixed(4)} ` +
          `sum=${r.sum} dpx=${Number.isFinite(r.pixelDelta) ? r.pixelDelta.toFixed(3) : 'na'} ` +
          `lit=${r.lit} col=${r.x0}..${r.x1} h=${r.bufH} ` +
          `held=${Math.round(r.heldMs)} focus=${r.focus.toFixed(3)} ` +
          `state=${agentStateStore.get()}`,
      );
    }, pulseMs);
    return () => window.clearInterval(id);
  }, [isDev, pulseMs, engine]);

  /**
   * Turbulence RATE (§R.1's intensification). One small limb patch per tick.
   *
   * Separate from the pulse probe because it answers a different question with
   * a different region and a much shorter interval. `pixelDelta` is the whole
   * point here: the pulse cares about total brightness, this cares about how
   * fast the picture is changing, and over the wide column the spin decorrelated
   * the field within a frame and pinned that number.
   */
  useEffect(() => {
    // One probe, two regions. `--probe-limb` reads the silhouette, where
    // rotation contributes least and radial turbulence most; `--probe-centre`
    // reads the disc centre, where rotation contributes most. Same 80x160 patch
    // and the same cost, aimed at opposite questions.
    const limbMs = bootstrap?.probeLimbMs ?? 0;
    const centreMs = bootstrap?.probeCentreMs ?? 0;
    const limbMode = limbMs > 0;
    const ms = limbMode ? limbMs : centreMs;
    if (!isDev || ms <= 0 || !engine) return;
    const id = window.setInterval(() => {
      const r = engine.probeFrame(limbMode ? 'limb' : 'centre');
      if (!r) return;
      window.zoey.reportMetrics(
        `PROBE-${limbMode ? 'LIMB' : 'CENTRE'} t=${performance.now().toFixed(0)} ` +
          `dpx=${Number.isFinite(r.pixelDelta) ? r.pixelDelta.toFixed(4) : 'na'} ` +
          `sum=${r.sum} lit=${r.lit} rect=${r.x0}..${r.x1} ` +
          `held=${Math.round(r.heldMs)} focus=${r.focus.toFixed(4)} ` +
          `spin=${r.spinRad.toFixed(4)} state=${agentStateStore.get()}`,
      );
    }, ms);
    return () => window.clearInterval(id);
  }, [isDev, bootstrap, engine]);

  /* ── the drawer, and what it does to the sphere ────────────────────────── */

  // Keep the last panel mounted while the drawer slides shut, so the content
  // does not vanish a beat before the panel does.
  const lastRail = useRef<RailId>('pulse');
  if (rail) lastRail.current = rail;

  // NEGATIVE, unlike the previous build. §R.7 puts the drawer immediately right
  // of the rail, so the stage that remains is to its RIGHT and the sphere has to
  // move right to stay centred in it. The old drawer was docked to the far right
  // and the sphere moved left.
  const drawerWidth = tokenPx('--transcript-w', 320);
  const offsetPx = rail ? -drawerWidth : 0;

  const onTierChange = useCallback((next: SphereTier, reason: string) => {
    tierStore.set(next);
    setTierReason(reason);
    console.warn(`[orb] sphere demoted to ${next}: ${reason}`);
  }, []);

  const onEngineReady = useCallback((next: SphereEngine) => {
    setEngine(next);
  }, []);

  /**
   * Spec §4: "sphere state change → visible, p95 80 ms, hard fail 200 ms".
   *
   * Only states that came FROM THE DAEMON are timed. The Alt+1…6 cycler and
   * `--force-state` also change the state and would produce a flattering number
   * measured from a keystroke this process synthesised — so they are simply
   * absent from the map and report nothing, rather than being averaged in.
   */
  const onStateRendered = useCallback((state: AgentState, at: number) => {
    const pending = pendingState.current;
    // Only pair a draw with the arrival that caused it. A draw of a state the
    // daemon never sent — the Alt+1…6 cycler, `--force-state` — has no pending
    // arrival and reports nothing, rather than being timed against a keystroke
    // this process synthesised itself.
    if (!pending || pending.state !== state) return;
    pendingState.current = null;
    // Three numbers, not one. `drawn` is what the renderer costs and is the
    // only one comparable to the pre-dwell figures; `queued` is the deliberate
    // wait; `total` is what the owner actually experiences. Reported apart so
    // nobody can read the sum as a rendering result.
    window.zoey.reportMetrics(
      `STATE-VISIBLE state=${state} ` +
        `queuedMs=${pending.queuedMs.toFixed(2)} ` +
        `drawnMs=${(at - pending.at).toFixed(2)} ` +
        `totalMs=${(at - pending.arrivedAt).toFixed(2)}`,
    );
  }, []);

  return (
    <div className="app">
      <StatusBar />

      <div className="app__body">
        <Rail />

        <main className="stage">
          {/* Nothing is drawn until bootstrap resolves and the tier is known.
              Rendering <Sphere> on the default 'med' first would create a WebGL
              context and allocate particle buffers, only to tear both down a
              frame later when the probe answers 'dom' — the exact machine where
              that answer is likeliest is the one least able to afford it. */}
          {!bootstrap ? null : tier === 'dom' ? (
            <DomSphere offsetPx={offsetPx} />
          ) : (
            <Sphere
              tier={tier}
              offsetPx={offsetPx}
              onTierChange={onTierChange}
              onEngineReady={onEngineReady}
              onStateRendered={onStateRendered}
            />
          )}

          {/* §R.2 — the HUD sits over the stage, never inside a drawer.
              All three render nothing until they have something true to show.

              The approval stack is FIRST and sits above the others: it is the
              only one of the three that is interrupting rather than ambient,
              and a notification toast must never overlap the buttons of a red
              action. */}
          <ApprovalStack />
          <NotificationStack />
          <LastLine />
        </main>

        <Drawer
          title={railById(lastRail.current).label}
          open={rail !== null}
          onClose={() => railStore.set(null)}
        >
          {railById(lastRail.current).render()}
        </Drawer>
      </div>

      {/* Dev-only AND off by default. `isDev` alone was the wrong gate: the
          owner runs `npm run dev`, so it was true for him, and the overlay sat
          over the lower-left of his sphere every day. --dev-overlay shows it at
          launch; Alt+0 toggles it. */}
      {isDev && showOverlay ? (
        <DevOverlay
          tier={tier}
          // The DOM rung has no engine; passing the disposed one's closure would
          // keep the overlay quoting frame times that stopped being measured.
          readStats={tier === 'dom' ? null : readStats}
          tierReason={tierReason}
          rendererName={rendererName}
        />
      ) : null}
    </div>
  );
}
