/**
 * The collapsed layout — spec §8.1's "design the collapsed layout first".
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │ status bar                                          28px │
 *   ├───────────────────────────────┬─────────────────────┬────┤
 *   │         sphere stage          │  drawer (overlay)   │rail│
 *   │      floats over the void     │        320          │ 48 │
 *   └───────────────────────────────┴─────────────────────┴────┘
 *
 * THE RAIL AND ITS DRAWER ARE ON THE RIGHT. They were on the left and opened
 * rightward, which put PULSE's drawer over the calendar — the one permanent
 * panel, docked bottom-left. Rail, drawer and approval card now share one
 * right-hand column and the calendar has the left side to itself.
 *
 * At 1366×768 with a drawer open that is 368px of chrome and ~998px of stage.
 * The four-panel arrangement would leave 478px, which spec §8.1 calls "not a
 * centre stage — a thumbnail". The drawer is an overlay, so the stage never
 * actually shrinks; the sphere is offset inside the scene instead.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { AGENT_STATES, type AgentState } from '@tessa/protocol';

import type { BootstrapInfo, SphereTier } from '../shared/ipc-contract.ts';
import { parseDevScript, runDevScript } from './dev-drive.ts';
import { tokenPx } from './design-tokens.ts';
import { applyAura, auraState, auraSweep, setForcedAuraLoad } from './aura.ts';
import { applyTheme, currentTheme, isThemeId, themeForKey, type ThemeId } from './theme.ts';
import {
  approvalArrived,
  approvalCleared,
  approvalRefused,
  approvalsStore,
  approvalsSweepExpired,
} from './state/approval-store.ts';
import { StateDwell } from './state/state-dwell.ts';
import { ApprovalStack } from './layout/ApprovalCard.tsx';
import { Calendar } from './layout/Calendar.tsx';
import { Clock } from './layout/Clock.tsx';
import { CompanionSwitcher } from './layout/CompanionSwitcher.tsx';
import { Today } from './layout/Today.tsx';
import { Drawer } from './layout/Drawer.tsx';
import { startTick } from './state/tick.ts';
import { DevOverlay } from './layout/DevOverlay.tsx';
import { LastLine } from './layout/LastLine.tsx';
import { NotificationStack } from './layout/NotificationStack.tsx';
import { Rail } from './layout/Rail.tsx';
import { StateChip } from './layout/StateChip.tsx';
import { StatusBar } from './layout/StatusBar.tsx';
import { railById } from './rails/rails.tsx';
import { DomSphere } from './scene/DomSphere.tsx';
import { Sphere } from './scene/Sphere.tsx';
import { probeSphereTier } from './scene/gpu-tier.ts';
import type { ProbeReading, SphereEngine } from './scene/sphere-engine.ts';
import {
  agentDetailStore,
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
  turnTimingStore,
  TRANSCRIPT_MAX,
  useStore,
  type RailId,
} from './state/store.ts';

/* ─────────────────────────────────────────────────────── the composition ──
 *
 * Direction A. The sphere is placed OFF-CENTRE by design and the right column
 * occupies the space that opens up. A circle centred in a rectangle with equal
 * emptiness on all four sides is the least dynamic arrangement available, and
 * that bullseye is most of what read as unfinished.
 *
 * These fractions are of the WINDOW, not of the stage, because the composition
 * is a property of what he sees rather than of an internal box.
 */

/** Status bar height. The column's own width lives in CSS (`--col-w`). */
const STATUS_H = 28;


/**
 * Widths of the two floating panels, and the window widths they need.
 *
 * CONTRACT §9 sizes them: `--panel-left-w` 240, `--panel-right-w` 280. §R.7
 * forbids a rounded card ON THE CENTRE STAGE and these are rounded cards, so
 * it is worth saying plainly rather than leaving it to look like a rule was
 * broken quietly: the prohibition is about the stage, which is the sphere's
 * ground and must stay void. These sit at the frame's edges, outside the
 * sphere's clear space, which is where §9's own layout rails put a left and a
 * right panel in the first place.
 *
 * Two panels need far more room than the one column did, so they collapse in a
 * stated order rather than all at once. See LAYOUT_STEPS in the report.
 */

/**
 * The largest fraction of the stage's SHORT side the sphere's diameter may
 * take, and the clearance it keeps from a panel.
 *
 * At its natural size the sphere's projected radius is 43% of the canvas
 * height — an 86%-of-height disc, which clipped top and bottom against the
 * inset border in the owner's screenshot. 0.74 is judged, not measured: it
 * leaves a margin wide enough that breath and turbulence, which push the shell
 * a few percent beyond its nominal radius, cannot reach the edge either.
 */
/**
 * The bands the sphere may not enter, in px.
 *
 * TOP_ROW_H is the state chip and the clock; BOTTOM_CONTROLS_H is the arrows,
 * the wordmark, the indicator and the pill. Both are measured off the rendered
 * elements rather than guessed, and both are subtracted from the height the
 * sphere is fitted into — leaving them out is precisely how it came to be
 * clipped top and bottom.
 */
const TOP_ROW_H = 52;
/**
 * 104 -> 68, AND THE 36 px COMES FROM THE REFERENCE'S OWN COMPOSITION.
 *
 * 104 was not padding: measured off a capture, the bottom controls occupy
 * y=624..696 of a 720 px window, which is 72 px of real content plus a 24 px
 * margin, and the sphere's lower edge sat at 613 against a boundary of 616.
 * There were eight pixels of slack in the whole band. So the sphere could not
 * grow by leaving the controls alone, and reporting "37.6% is the honest
 * ceiling" was true only under an assumption nobody had checked.
 *
 * The assumption is that the controls sit BELOW the sphere. The reference does
 * not do that. In image11 the wordmark and the two arrows sit INSIDE the disc's
 * lower region — the sphere's bottom edge is at y=1012 and the wordmark's ink
 * is at y=945..965, about 7% of the diameter inboard — and that overlap is
 * exactly where its extra size comes from.
 *
 * 68 is solved for, not chosen: it is the value that puts the disc at 549 px of
 * 1366, which is the reference's measured 40.2%. The switcher then overlaps the
 * disc's lower edge by ~16 px, which is less overlap than the reference has.
 */
const BOTTOM_CONTROLS_H = 68;

/**
 * The largest fraction of the stage's short side the sphere's diameter may take.
 *
 * 0.74 -> 0.96, MEASURED not chosen, and it still does not reach the reference.
 *
 * The reference's sphere is 549 window-px of 1366, i.e. 40.2% of the width. On
 * this stage the binding constraint is the HEIGHT, not the width: the clear
 * vertical band is 692 - 52 (top row) - 104 (bottom controls) = 536 px, so the
 * largest disc that fits is 536 px = 39.2% — and that one touches both bands.
 * 0.96 gives 514 px = 37.6%, which clears them with the breath and turbulence
 * overhang included.
 *
 * AND THEN 40.2% TURNED OUT TO BE REACHABLE AFTER ALL, which corrects the
 * paragraph that used to stand here. It read: "40.2% is not reachable at
 * 1366x720... on a 720 px work area the same proportion would put the sphere
 * through the wordmark." The second half is true and is not an obstacle — the
 * reference PUTS the sphere through its own wordmark, which is where its size
 * comes from. See BOTTOM_CONTROLS_H, now 68 rather than 104. At that band the
 * same 0.96 gives 549 px = 40.2%, the reference's figure exactly, and the
 * fill fraction does not move.
 *
 * A CORRECTION TO THE BRIEF, which read the reference as "nearer two thirds".
 * It is not: two thirds of 1366 is 911 px, which does not fit a 692 px stage in
 * any arrangement.
 *
 * It still may not exceed the natural projected size, so this is a ceiling
 * raise rather than a licence to inflate.
 */
const SPHERE_FILL = 0.96;
const PANEL_CLEARANCE = 28;

/**
 * The sphere's natural projected radius, as a fraction of canvas height.
 *
 * Derived, not measured: the silhouette of a sphere of radius R at distance d
 * subtends asin(R/d), so its projected radius is
 * `tan(asin(R/CAMERA_Z)) * h / (2 tan(fov/2))`. With R=1, CAMERA_Z=3.2 and
 * fov=42 that is 0.4285 * h. A least-squares circle fitted to a capture's own
 * silhouette measured 297.7 px at h=692, i.e. 0.4302 — 0.4% from the algebra,
 * which is the check that this constant is the real one and not a guess.
 */
const SPHERE_NATURAL_R = 0.4285;

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
   * Window size, tracked so the composition can collapse rather than overflow.
   * `resize` only; there is no polling and no rAF involvement.
   */
  const [viewport, setViewport] = useState(() => ({
    w: window.innerWidth,
    h: window.innerHeight,
  }));
  useEffect(() => {
    const onResize = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // The 1 Hz clock the whole telemetry layer reads. See state/tick.ts — this is
  // the mechanism behind "an instrument reads as advanced because it is live".
  useEffect(() => startTick(), []);

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

    void window.tessa.bootstrap().then((info) => {
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
      // Before the first heartbeat can arrive, so a forced run never renders
      // one real frame at the real load first.
      setForcedAuraLoad(info.forcedAura);

      const wanted: ThemeId = isThemeId(info.theme) ? info.theme : 'cyan';
      const steps = applyTheme(wanted);
      window.tessa.reportMetrics(
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
    void window.tessa.getSnapshot().then((snap) => {
      if (!alive) return;
      connectionStore.set(snap.connection);
      if (snap.health) {
        healthStore.set(snap.health);
        pushHealthSample(snap.health);
        applyAura(snap.health);
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
    const offConnection = window.tessa.onConnection((status) => {
      connectionStore.set(status);
      // A dropped link must not leave a frozen uptime on screen looking live.
      // The aura goes out with it, for the same reason and by the same rule the
      // equatorial pulse stops: an instrument holding its last value is a lie
      // in the shape of a reading.
      if (status.phase !== 'connected') {
        healthStore.set(null);
        applyAura(null);
      }
    });
    const offHealth = window.tessa.onHealth((health) => {
      healthStore.set(health);
      pushHealthSample(health);
      applyAura(health);
    });

    // SENTINEL's two real sources. History seeds the list; the live stream
    // prepends onto it, newest first, bounded so a long-running surface cannot
    // grow without limit.
    const offAuditHistory = window.tessa.onAuditHistory((entries) =>
      auditStore.set([...entries].reverse().slice(0, AUDIT_MAX)),
    );
    const offAuditAppended = window.tessa.onAuditAppended((entry) =>
      auditStore.set([entry, ...auditStore.get()].slice(0, AUDIT_MAX)),
    );
    const offPty = window.tessa.onPtySessions((sessions) => ptySessionsStore.set(sessions));
    const offMic = window.tessa.onMicState((state) => micStore.set(state));
    const offNote = window.tessa.onNotification((note) => pushNotification(note));
    const offApproval = window.tessa.onApprovalRequested((request) => approvalArrived(request));
    const offApprovalCleared = window.tessa.onApprovalCleared((cleared) =>
      approvalCleared(cleared.requestId, cleared.reason, cleared.decision),
    );
    const offApprovalRefused = window.tessa.onApprovalRefused((refusal) => {
      approvalRefused(
        refusal.requestId,
        refusal.code,
        refusal.message,
        refusal.requestStillPending,
      );
      window.tessa.reportMetrics(
        `APPROVAL-REFUSED ${refusal.requestId} code=${refusal.code} ` +
          `stillPending=${refusal.requestStillPending}`,
      );
    });
    const offTranscript = window.tessa.onTranscriptLine((line) =>
      transcriptStore.set([...transcriptStore.get(), line].slice(-TRANSCRIPT_MAX)),
    );

    // Main has already validated this against AGENT_STATES before sending.
    // Through the dwell, never straight to the store. See state-dwell.ts.
    const dwell = new StateDwell({
      report: (line) => window.tessa.reportMetrics(line),
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

    const offAgentState = window.tessa.onAgentState(({ state, detail }) => {
      const at = performance.now();
      const repeat = state === lastArrivedState.current;
      lastArrivedState.current = state;
      window.tessa.reportMetrics(
        `STATE-ARRIVED state=${state} t=${at.toFixed(1)} repeat=${repeat} depth=${dwell.depth}`,
      );
      // The detail is set BEFORE the state. The chip renders both from one
      // paint, and setting the state first would show the new state beside the
      // old target for a frame — which is a wrong statement about what she is
      // touching, briefly, which is still wrong.
      agentDetailStore.set(detail);
      dwell.submit(state);
    });

    // Item 9 — the latency trace. Nothing emits `evt.turn.timing` yet, so this
    // is live wiring behind a dark renderer rather than a stub: the moment
    // Session 1 ships its half, the trace appears with no change here.
    const offTiming = window.tessa.onTurnTiming((timing) => {
      turnTimingStore.set(timing);
    });

    return () => {
      alive = false;
      offConnection();
      offHealth();
      offAgentState();
      offTiming();
      offAuditHistory();
      offAuditAppended();
      offPty();
      offTranscript();
      offMic();
      offNote();
      offApproval();
      offApprovalCleared();
      offApprovalRefused();
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
      window.tessa.setTheme(next);
      window.tessa.reportMetrics(
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
        window.tessa.reportMetrics(
          `APPROVAL-EXPIRED ${requestId} — invalidated locally, nothing sent (CONTRACT §5.1)`,
        );
      }
      // One timer, not two. The aura goes flat if the beats stop while the
      // socket stays up — a held value would be the frozen-instrument lie.
      if (auraSweep()) {
        window.tessa.reportMetrics('AURA-STALE no heartbeat in 15s — aura flattened');
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
    window.tessa.reportMetrics(`DEV-DRIVE parsed ${steps.length} step(s)`);
    const id = window.setTimeout(() => {
      void runDevScript(
        steps,
        (line) => window.tessa.reportMetrics(line),
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
    if (isDev) window.tessa.reportMetrics('PTT-KEY hold-mode listener attached');

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
        window.tessa.reportMetrics(
          `PTT-KEY code=${event.code || '(none)'} key=${JSON.stringify(event.key)} ` +
            `ctrl=${event.ctrlKey} alt=${event.altKey} shift=${event.shiftKey} ` +
            `repeat=${event.repeat} focus=${document.hasFocus()} match=${match}`,
        );
      }
      if (!match) return;
      held = true;
      event.preventDefault();
      window.tessa.pushToTalkEdge('down');
    }

    function onUp(event: KeyboardEvent) {
      if (!held) return;
      if (!isSpace(event) && event.key !== 'Control' && event.key !== 'Alt') return;
      held = false;
      window.tessa.pushToTalkEdge('up');
    }

    window.addEventListener('keydown', onDown);
    window.addEventListener('keyup', onUp);
    return () => {
      window.removeEventListener('keydown', onDown);
      window.removeEventListener('keyup', onUp);
      // Unmounting mid-hold would otherwise strand the claim with no keyup
      // listener left to end it.
      if (held) window.tessa.pushToTalkEdge('up');
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
      window.tessa.reportMetrics(
        `tier=${s.tier} pts=${s.particles} pgain=${s.paletteGain.toFixed(3)} ` +
          `focused=${s.focused} n=${s.samples} ` +
          `cost=${s.cost.p50.toFixed(2)}/${s.cost.p95.toFixed(2)} ` +
          `raf=${s.raf.p50.toFixed(1)}/${s.raf.p95.toFixed(1)} ` +
          `shown=${s.present.p50.toFixed(1)}/${s.present.p95.toFixed(1)} ` +
          `fps=${s.fps.toFixed(1)} state=${agentStateStore.get()} aura[${auraState()}] ` +
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
      if (r) window.tessa.reportMetrics(`PROBE-GEO ${describeProbe(r)}`);
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
      window.tessa.reportMetrics(
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
      window.tessa.reportMetrics(
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
  const lastRail = useRef<RailId>('trace');
  if (rail) lastRail.current = rail;

  /* ── where the sphere goes, and it is ONE computation ──────────────────────
   *
   * The drawer used to own this value outright (`rail ? -drawerWidth : 0`),
   * which was fine while the sphere lived at the stage centre and fatal the
   * moment the composition placed it elsewhere: the two systems would each
   * write the same number and the last one to run would win.
   *
   * So the target position is derived from the WHOLE layout at once — base
   * placement, drawer open or shut — and converted to the engine's offset
   * convention exactly once, here.
   */
  const railW = tokenPx('--rail-w', 48);
  const drawerWidth = tokenPx('--transcript-w', 320);

  const canvasW = Math.max(1, viewport.w - railW);
  const canvasH = Math.max(1, viewport.h - STATUS_H);
  const leftPanelW = tokenPx('--panel-left-w', 240);
  const rightPanelW = tokenPx('--panel-right-w', 280);

  /**
   * BOTH COLUMNS TOGETHER, OR NEITHER.
   *
   * The old build dropped the left panel first and kept the right, which let
   * the sphere slide sideways into the gap and put the calendar over it — his
   * second complaint. In the reference the two columns are a symmetric frame
   * with the sphere clear between them, so they are one decision now: there is
   * room for the pair, or the stage is bare and the sphere takes the middle.
   *
   * They yield to the drawer and to the approval card for the reasons they
   * always did — both are deliberate where a column is ambient, and the card
   * is opaque.
   */
  const cardPresent = useStore(approvalsStore).length > 0;

  /**
   * AN APPROVAL CARD CLOSES ANY OPEN DRAWER. His ruling: one thing on the right
   * at a time, and with the rail moved to the right edge the card, the drawer
   * and the rail are literally the same column.
   *
   * It closes on the card's ARRIVAL only. When the card is answered the drawer
   * STAYS CLOSED — he reopens it — so there is deliberately no restore here and
   * no memory of what was open. Restoring would put a panel back on screen at
   * the exact moment he has just made a decision and is looking at the result.
   */
  const hadCard = useRef(false);
  useEffect(() => {
    if (cardPresent && !hadCard.current) railStore.set(null);
    hadCard.current = cardPresent;
  }, [cardPresent]);

  /**
   * THE STAGE IS BARE BY DEFAULT. His ruling, and it changes the whole view.
   *
   * The only permanent panel is the calendar, docked bottom-left. Everything
   * else lives behind a rail. So the sphere's clear space is the whole stage
   * minus three things, each of which it must actually avoid:
   *
   *   the calendar dock, bottom-left and always there;
   *   the drawer, when a rail is open;
   *   the approval card, which is opaque and must never cover the sphere —
   *     a red-tier approval is exactly when he most needs to read her state.
   */
  const calDockW = leftPanelW;
  const calDockH = 300;

  /**
   * THE RAIL IS ON THE RIGHT NOW, and every x in this function moved with it.
   *
   * His ruling, and it settles a collision rather than a preference: the rails
   * used to sit on the LEFT and open rightward, so PULSE's drawer rendered over
   * the calendar — the one permanent panel, docked bottom-left. Moving the rail
   * to the far right puts the rail, its drawer and the approval card into ONE
   * right-hand column and leaves the whole left side to the calendar.
   *
   * Three things had to move together and any one left behind is a bug:
   *   the stage now starts at x = 0 and ends at viewport.w - railW, so every
   *     `railW + …` that meant "the left inside edge" became a bare clearance
   *     and every right-hand bound gained a `- railW`;
   *   the drawer shift REVERSED — the sphere used to dodge right, away from a
   *     left drawer, and must now dodge LEFT;
   *   the canvas origin moved, so `--sphere-cx` and `offsetPx` no longer
   *     subtract railW.
   */

  const naturalR = canvasH * SPHERE_NATURAL_R;

  /**
   * THE SPHERE IS BUILT TO A MEASURED SIZE, not to a guess.
   *
   * Measured off reference/v2/image7 with a perspective correction the
   * measurement validates itself against: the reference's left panel comes out
   * at 240 window-px and its right panel at ~280, which are exactly this
   * build's `--panel-left-w` and `--panel-right-w`. Two independent landmarks
   * landing on known values is what makes the third trustworthy —
   *
   *   reference sphere   549 window-px of 1366  =  40.2%
   *   previous build     396 window-px          =  29.0%
   *
   * A CORRECTION TO THE BRIEF, which read the reference as "nearer two thirds".
   * It is not: two thirds of 1366 is 911 px, which would not fit the 692 px
   * stage at all. 40% is what the pixels say, and the pixels win.
   */
  /**
   * The approval card SHRINKS the sphere as well as shifting it.
   *
   * Shifting alone left 14 px between the two — measured — because the sphere
   * is now 30% larger than when that arithmetic was written and it simply ran
   * out of room to move into. A red-tier approval is the one moment he most
   * needs to read her state, so the sphere gives up size for it rather than
   * crowding the card.
   */
  const cardW = Math.min(460, viewport.w - 32);
  const clearW =
    canvasW - 2 * PANEL_CLEARANCE - (cardPresent ? cardW + PANEL_CLEARANCE : 0);
  const clearH = canvasH - TOP_ROW_H - BOTTOM_CONTROLS_H;
  const allowedR = Math.min(
    (clearH * SPHERE_FILL) / 2,
    (Math.max(140, clearW) * SPHERE_FILL) / 2,
    naturalR,
  );
  const fit = allowedR / naturalR;
  const sphereR = allowedR;

  /**
   * The vertical placement, computed before the horizontal one because the
   * dock test needs it and it does not depend on x.
   */
  const targetCyPre = Math.max(
    STATUS_H + TOP_ROW_H + sphereR,
    Math.min(
      STATUS_H + TOP_ROW_H + clearH / 2,
      viewport.h - BOTTOM_CONTROLS_H - sphereR,
    ),
  );

  /**
   * The sphere centres in what is actually free.
   *
   * With the columns gone it has the whole frame — which is the view he will
   * look at most and the one to get right. The calendar dock is bottom-left and
   * the sphere sits above and right of it rather than being pushed off centre
   * by it; only the card and the drawer move it horizontally.
   */
  /**
   * THE CALENDAR DOCK IS A LEFT-SIDE OCCUPANT WHENEVER THE SPHERE REACHES IT.
   *
   * At 1366x720 the sphere's lower edge stops above the dock's band and the two
   * never meet, so the sphere keeps the whole width. At 900x600 they do meet —
   * measured, `dockClash=true` — and the sphere sat over the calendar, which is
   * the exact fault he reported in the previous build ("the calendar is
   * blocking it") arriving from the other direction.
   *
   * Not circular: the test uses `targetCy` and `sphereR`, neither of which
   * depends on the horizontal placement being computed here.
   */
  const dockRight = PANEL_CLEARANCE + calDockW;
  const dockTop = viewport.h - PANEL_CLEARANCE - calDockH;
  // Where the sphere WOULD sit with the dock ignored…
  const bareLeft = PANEL_CLEARANCE;
  const bareRight =
    viewport.w - railW - (cardPresent ? cardW + PANEL_CLEARANCE : PANEL_CLEARANCE);
  const cxIfCentred = (bareLeft + bareRight) / 2;
  // …and whether that actually overlaps the dock's box, in BOTH axes. Testing
  // only the vertical band pushed the sphere right at 1366x720, where the two
  // are nowhere near each other.
  const dockReached =
    cxIfCentred - sphereR < dockRight && targetCyPre + sphereR > dockTop;
  const clearLeft = dockReached ? dockRight + PANEL_CLEARANCE : bareLeft;
  const clearRight = bareRight;
  /**
   * THE DRAWER SHIFT FLIPPED WITH THE RAIL — Math.max became Math.min.
   *
   * The old line pushed the sphere RIGHT until it cleared a LEFT-hand drawer.
   * The drawer is on the right now, so the same intent is the mirror: pull the
   * sphere LEFT until its right edge clears the drawer's left edge. Leaving the
   * max in place would have shoved the sphere straight into the panel it is
   * supposed to be avoiding, and it would have looked like the drawer was
   * "pushing" correctly right up until someone measured the overlap.
   */
  const wantCx = rail
    ? Math.min(
        (clearLeft + clearRight) / 2,
        viewport.w - railW - drawerWidth - sphereR - PANEL_CLEARANCE,
      )
    : (clearLeft + clearRight) / 2;
  const targetCx = Math.max(
    sphereR + PANEL_CLEARANCE,
    Math.min(wantCx, viewport.w - railW - sphereR - PANEL_CLEARANCE),
  );
  /**
   * Vertically the sphere sits in the band between the top row and the bottom
   * controls. The calendar dock does NOT push it up: the dock is bottom-LEFT
   * and the sphere is centred, so at the sizes this runs at they clear each
   * other in x. `calDockW`/`calDockH` exist so the no-clip proof can state that
   * rather than leave it to be noticed later.
   */
  const targetCy = targetCyPre;
  /** Does the disc reach into the calendar dock's box? Reported, not assumed. */
  const dockClash =
    targetCx - sphereR < PANEL_CLEARANCE + calDockW &&
    targetCy + sphereR > viewport.h - PANEL_CLEARANCE - calDockH;

  // Engine convention: positive x moves LEFT by x/2, positive y moves UP by y/2.
  // The canvas starts at x = 0 now that the rail is on the right, so there is
  // no railW to subtract.
  const offsetPx = -2 * (targetCx - canvasW / 2);
  const offsetYPx = -2 * (targetCy - STATUS_H - canvasH / 2);

  /**
   * Published to CSS so the aura, the floor and the wordmark track the sphere
   * without a second copy of this arithmetic.
   *
   * (This used to say "the contact ellipse". That is gone — deleted, not
   * dimmed — and the floor replaced it. One ground under the sphere, not two.)
   *
   * The aura is a radial centred on the sphere; if the sphere moves and the
   * glow does not, it becomes a light with nothing in it. The floor is anchored
   * to `--sphere-cy + --sphere-r`, so a refit moves the horizon with the object
   * standing on it rather than leaving a stripe behind.
   */
  const stageVars = {
    '--sphere-cx': `${targetCx.toFixed(1)}px`,
    '--sphere-cy': `${(targetCy - STATUS_H).toFixed(1)}px`,
    '--sphere-r': `${sphereR.toFixed(1)}px`,
  } as React.CSSProperties;

  const onTierChange = useCallback((next: SphereTier, reason: string) => {
    tierStore.set(next);
    setTierReason(reason);
    console.warn(`[orb] sphere demoted to ${next}: ${reason}`);
  }, []);

  const onEngineReady = useCallback((next: SphereEngine) => {
    setEngine(next);
  }, []);

  // The fit is a layout consequence, so it is pushed on every layout change
  // rather than passed as a prop — passing it would re-run Sphere's mount
  // effect and rebuild the WebGL context, which is the most expensive thing
  // this app can do (see the note on the depthFar prop).
  useEffect(() => {
    engine?.setFit(fit);
    // The layout's own numbers, reported so a disagreement between what this
    // computes and what the sphere renders is visible in a log rather than
    // inferred from a screenshot. It was inferred once and the inference was
    // wrong by 50 px.
    if (isDev) {
      window.tessa.reportMetrics(
        `LAYOUT canvas=${canvasW}x${canvasH} rail=${rail ?? 'none'} card=${cardPresent} ` +
          `left=${leftPanelW} right=${rightPanelW} clearW=${clearW.toFixed(0)} ` +
          `clearH=${clearH.toFixed(0)} naturalR=${naturalR.toFixed(1)} ` +
          `allowedR=${allowedR.toFixed(1)} fit=${fit.toFixed(3)} ` +
          `cx=${targetCx.toFixed(0)} cy=${targetCy.toFixed(0)} dockClash=${dockClash}`,
      );
    }
  }, [
    engine,
    fit,
    canvasW,
    canvasH,
    rail,
    cardPresent,
    leftPanelW,
    rightPanelW,
    clearW,
    clearH,
    naturalR,
    allowedR,
    targetCx,
    targetCy,
    dockClash,
  ]);

  /**
   * The two background companions, placed in the top corners BEHIND the panels.
   *
   * ─── what putting them behind the panels costs, measured before, not now ───
   * The approval-card round measured a transparent panel over a bright sphere
   * and found it unreadable; these panels carry text he must read. Three things
   * keep that from repeating, and none of them is luck:
   *
   *   the companions are DIM by construction (uBrightness 0.46 against the main
   *   sphere's 1.10) — see companions.ts;
   *   the panel fill measured off the reference is within four levels of the
   *   void — very nearly opaque black — and the panels use --panel, which
   *   composites to the same neighbourhood;
   *   they are placed so their CENTRES sit in the gap between the rail and the
   *   column, so it is their dim outer edge that goes under the panel, never
   *   their lit limb.
   *
   * Fractions of the CANVAS, which is what the engine expects. Recomputed on
   * every layout change so a resize or a drawer moves them with everything else.
   */
  /**
   * JOBS AND CHAT OPEN THEMSELVES WHEN THERE IS SOMETHING IN THEM.
   *
   * His ruling: a panel appears when it becomes active, and stays until he
   * dismisses it — no timeout, no auto-close. The trigger is built; NOTHING
   * FIRES IT TODAY. Jobs waits on a Phase 5 queue that does not exist, and
   * typed chat waits on Session 1 wiring the agent loop to a surface. Both
   * conditions below are permanently false right now, and that is the honest
   * state rather than a stub that opens on nothing.
   *
   * One-shot per transition, not per render: `openedFor` remembers what it has
   * already opened for, so dismissing a panel does not have it spring back on
   * the next tick. That is the difference between "opens when it becomes
   * active" and "cannot be closed while active".
   */
  const openedFor = useRef<{ jobs: boolean; chat: boolean }>({ jobs: false, chat: false });
  const jobsActive = false; // no producer: evt.job.* is never emitted
  const chatActive = false; // no producer: typed chat is not wired
  useEffect(() => {
    if (jobsActive && !openedFor.current.jobs) {
      openedFor.current.jobs = true;
      railStore.set('jobs');
    }
    if (!jobsActive) openedFor.current.jobs = false;
    if (chatActive && !openedFor.current.chat) {
      openedFor.current.chat = true;
      railStore.set('chat');
    }
    if (!chatActive) openedFor.current.chat = false;
  }, [jobsActive, chatActive]);

  useEffect(() => {
    if (!engine) return;
    /**
     * A QUARTER of the main sphere's diameter, measured off image7: the left
     * companion spans ~110 photo-px and the right ~135 against the main's 480,
     * i.e. 0.23 and 0.28. They read unmistakably as spheres there and as
     * slivers here, which was his complaint.
     *
     * Expressed against `sphereR` so they grow with it — a fixed world radius
     * would have them shrink relative to the main sphere every time it grew.
     *
     * 0.42 first, which MEASURED at 0.42-0.48 of the main diameter against the
     * reference's 0.23-0.28 — overshot by nearly two. 0.23 is the corrected
     * coefficient, and the measurement is why it is not a guess in either
     * direction.
     */
    /**
     * THE TWO ARE NOT THE SAME SIZE, and in the reference they never were.
     * Measured on image7, where both are fully in shot: the left companion is
     * 0.244 of the main disc and the right is 0.320. One shared coefficient was
     * an averaging error, not a simplification — two identical balls either
     * side of the sphere read as a symmetrical ornament, which is the opposite
     * of three companions with their own identities.
     */
    const fit = sphereR / Math.max(1, naturalR);
    const left = Math.max(0.14, Math.min(0.42, fit * 0.24));
    const right = Math.max(0.14, Math.min(0.42, fit * 0.30));
    engine.setCompanions([
      { side: 'left', fx: rail ? 0.34 : 0.17, fy: 0.21, scale: left },
      { side: 'right', fx: cardPresent ? 0.62 : 0.85, fy: 0.2, scale: right },
    ]);
  }, [engine, rail, cardPresent, sphereR, naturalR]);

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
    window.tessa.reportMetrics(
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
        <main className="stage" style={stageVars} data-bare={rail === null && !cardPresent}>
          {/* Nothing is drawn until bootstrap resolves and the tier is known.
              Rendering <Sphere> on the default 'med' first would create a WebGL
              context and allocate particle buffers, only to tear both down a
              frame later when the probe answers 'dom' — the exact machine where
              that answer is likeliest is the one least able to afford it.

              THE FLOOR AND THE EDGE DETAIL ARE GONE, and the time axis with
              them. None appears in any of the sixteen reference images, and the
              axis's "-3m -2m -1m" ruler plus its second rule across the bottom
              cut the composition in half — his words. The telemetry those
              served now lives in the PULSE rail. */}
          {!bootstrap ? null : tier === 'dom' ? (
            <DomSphere offsetPx={offsetPx} offsetYPx={offsetYPx} />
          ) : (
            <Sphere
              tier={tier}
              offsetPx={offsetPx}
              onTierChange={onTierChange}
              onEngineReady={onEngineReady}
              onStateRendered={onStateRendered}
              depthFar={bootstrap.forcedDepth}
              rim={bootstrap.forcedSphere}
              counts={bootstrap.forcedCount}
              faceSat={bootstrap.forcedFaceSat}
              paletteGain={bootstrap.forcedPaletteGain}
              offsetYPx={offsetYPx}
            />
          )}

          {/* Top row, as the reference has it: the state centre, the clock
              right. The CALM pill beside the clock is NOT built — nothing in
              core/ maps to it. See the report. */}
          <StateChip />
          <Clock />

          {/* Bottom centre: the arrows he kept pointing at, her name, the
              indicator, and the pill. See CompanionSwitcher for why the arrows
              are present-but-disabled rather than absent or fake. */}
          <CompanionSwitcher />

          {/* §R.2 — the HUD sits over the stage, never inside a drawer.
              The approval stack is FIRST and above the others: it interrupts
              where they are ambient, and a toast must never cover the buttons
              of a red action. */}
          <ApprovalStack />
          <NotificationStack />
          <LastLine />

          {/* THE CALENDAR IS THE ONLY PERMANENT PANEL, bottom-left.
              His ruling: nothing else shows until it has something to say or he
              opens it. Everything that used to sit on the stage — the status
              card, the jobs list, the chat, the telemetry column — is behind a
              rail now, which is the mechanism that already existed for exactly
              this. See RAIL_IDS.

              It is the one panel always on screen because the month with today
              marked is true without a producer, and because a glanceable
              always-on surface at 2am should say the date. */}
          <aside className="cal-dock">
            <Calendar />
            <Today />
          </aside>
        </main>

        <Drawer
          title={railById(lastRail.current).label}
          open={rail !== null}
          onClose={() => railStore.set(null)}
        >
          {railById(lastRail.current).render()}
        </Drawer>

        {/* LAST, so grid auto-placement puts it in the second column. The rail
            is a grid item; the drawer above it is absolutely positioned and so
            takes no track. */}
        <Rail blocked={cardPresent} />
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
