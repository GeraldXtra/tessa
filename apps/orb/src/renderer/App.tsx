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

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { AGENT_STATES, type AgentState } from '@tessa/protocol';

import type { BootstrapInfo } from '../shared/ipc-contract.ts';
import { parseDevScript, runDevScript } from './dev-drive.ts';
import { installDevKeys } from './dev-keys.ts';
import { tokenPx } from './design-tokens.ts';
import { applyTheme, currentTheme, isThemeId, themeForKey, type ThemeId } from './theme.ts';
import {
  approvalArrived,
  approvalCleared,
  approvalRefused,
  approvalsStore,
  approvalsSweepExpired,
} from './state/approval-store.ts';
import { StateDwell } from './state/state-dwell.ts';
import {
  composeNoteLine,
  composeNoteState,
  composePatch,
  requestComposeFocus,
} from './state/compose-store.ts';
import { ApprovalStack } from './layout/ApprovalCard.tsx';
import { Calendar } from './layout/Calendar.tsx';
import { Caption } from './layout/Caption.tsx';
import { Clock } from './layout/Clock.tsx';
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
import { Sphere } from './scene/Sphere.tsx';
import type { MarkReport, PlasmaEngine, RenderPath } from './scene/plasma-engine.ts';
import { STAGE_BANDS, plasmaLayout, type Box } from './scene/plasma-layout.ts';
import { captionText, captionWords, jobsStore } from './state/plasma-inputs.ts';
import {
  agentDetailStore,
  agentStateStore,
  auditStore,
  AUDIT_MAX,
  connectionStore,
  devStore,
  healthStore,
  micStore,
  ptySessionsStore,
  pushHealthSample,
  pushNotification,
  railStore,
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
const CARD_MAX_W = 460;

function sameBox(a: Box | null, b: Box | null): boolean {
  if (a === null || b === null) return a === b;
  return a.left === b.left && a.top === b.top && a.right === b.right && a.bottom === b.bottom;
}

export function App() {
  const rail = useStore(railStore);
  const mic = useStore(micStore);

  const [bootstrap, setBootstrap] = useState<BootstrapInfo | null>(null);
  const [engine, setEngine] = useState<PlasmaEngine | null>(null);
  const readStats = engine ? engine.stats : null;
  const [showOverlay, setShowOverlay] = useState(false);
  const [calBox, setCalBox] = useState<Box | null>(null);
  const calRef = useRef<HTMLElement>(null);

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
    ts: string;
    mainAt: number;
  } | null>(null);
  const arrivals = useRef(new Map<string, { ts: string; mainAt: number }>());
  const lastArrivedState = useRef<string | null>(null);

  /* ── bootstrap: GPU tier, then the connection feed ─────────────────────── */

  useEffect(() => {
    let alive = true;

    void window.tessa.bootstrap().then((info) => {
      if (!alive) return;
      setBootstrap(info);
      // Per-keystroke instrumentation in the compose box reads this rather
      // than paying for an IPC message it cannot know is a no-op.
      devStore.set(info.isDev);

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
      window.tessa.reportMetrics(
        `THEME applied=${wanted} mid=${steps.mid} hot=${steps.hot} deep=${steps.deep} bg=${steps.bg} ` +
          `reason="${info.themeReason}"${isThemeId(info.theme) ? '' : ` FALLBACK from ${JSON.stringify(info.theme)}`}`,
      );

      // Validated against AGENT_STATES in main before it got here.
      if (info.forcedState) agentStateStore.set(info.forcedState as AgentState);
      if (info.devOverlay) setShowOverlay(true);
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
      }
    });
    const offHealth = window.tessa.onHealth((health) => {
      healthStore.set(health);
      pushHealthSample(health);
    });
    const offJobs = window.tessa.onJobs((jobs) => jobsStore.set(jobs));
    const offPartial = window.tessa.onTranscriptPartial((partial) => {
      if (partial.role === 'assistant') captionText(partial.messageId, partial.text, partial.done);
    });
    const offWords = window.tessa.onVoiceWords((words) => captionWords(words));

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
    const offTranscript = window.tessa.onTranscriptLine((line) => {
      transcriptStore.set([...transcriptStore.get(), line].slice(-TRANSCRIPT_MAX));
      // Her answer closes the typed turn in flight. Bookkeeping for Escape,
      // not a rendering — the line itself is drawn by TRACE from the store.
      composeNoteLine(line);
      if (line.role === 'assistant') captionText(line.messageId, line.text, true);
    });

    // What the daemon said to a cancel. `res.ok` ends the turn; anything
    // else leaves it running and puts the daemon's own words in the hint.
    const offCancelReply = window.tessa.onAgentCancelReply((reply) => {
      composePatch(reply.ok ? { inflight: null, error: null } : { error: reply.message });
      window.tessa.reportMetrics(
        `CANCEL-RESULT messageId=${reply.messageId} ok=${reply.ok} type=${reply.type}`,
      );
    });

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
        const wire = arrivals.current.get(state) ?? { ts: '', mainAt: 0 };
        pendingState.current = { state, at: performance.now(), arrivedAt, queuedMs, ts: wire.ts, mainAt: wire.mainAt };
        agentStateStore.set(state as AgentState);
      },
    });

    const offAgentState = window.tessa.onAgentState(({ state, detail, ts, arrivedAt }) => {
      const at = performance.now();
      arrivals.current.set(state, { ts, mainAt: arrivedAt });
      const repeat = state === lastArrivedState.current;
      lastArrivedState.current = state;
      window.tessa.reportMetrics(
        `STATE-ARRIVED state=${state} t=${at.toFixed(1)} repeat=${repeat} depth=${dwell.depth}`,
      );
      // The raw arrival, before the dwell: idle-after-busy closes a typed
      // turn in flight. Bookkeeping, not a drawing.
      composeNoteState(state);
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
      offJobs();
      offPartial();
      offWords();
      offAgentState();
      offTiming();
      offAuditHistory();
      offAuditAppended();
      offPty();
      offTranscript();
      offCancelReply();
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
  const devKeys = bootstrap?.devKeys ?? false;

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
      window.tessa.reportMetrics(`THEME switched=${next} mid=${steps.mid} hot=${steps.hot} bg=${steps.bg}`);
      // The sphere's colours are uniforms resolved once at construction, not
      // CSS. Without this the chrome changes and the sphere does not.
      engine?.retint();
      engine?.mark(`Theme ${next}`, event.timeStamp, 0);
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

      /**
       * Ctrl+Shift+Space — open TRACE with focus in the compose box, from
       * anywhere in the Orb including the canvas. NOT dev-gated.
       *
       * Chosen, not inherited: the Console's chat pane has no chord at all
       * (a toolbar button and a pane-menu command), so there was no muscle
       * memory to match. This one is the push-to-talk chord minus Alt —
       * Ctrl+Alt+Shift+Space is TALK, Ctrl+Shift+Space is TYPE, same key —
       * and it is free of every other binding here (themes are Ctrl+Shift+
       * letter; the hold-mode PTT needs Alt), of Electron (no menu, so no
       * default accelerators), and of Windows. A renderer keydown, not a
       * global grab: it takes nothing from any other application.
       *
       * `code` then `key`, for the same reason every other chord here does:
       * synthetic input arrives with no usable `code`.
       *
       * Refused while an approval is pending, for the reason the rail refuses
       * (Rail.tsx): a red-tier request must not be dismissable by opening a
       * panel, and the card owns the right-hand column until it is answered.
       */
      const isSpace = event.code === 'Space' || event.key === ' ';
      if (event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey && isSpace) {
        event.preventDefault();
        if (approvalsStore.get().length > 0) {
          window.tessa.reportMetrics('CHORD ctrl+shift+space refused — approval pending');
          return;
        }
        railStore.set('trace');
        requestComposeFocus();
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
      if (!digit || !devKeys) return;

      const index = Number.parseInt(digit, 10) - 1;
      const next = AGENT_STATES[index];
      if (next) {
        agentStateStore.set(next);
        event.preventDefault();
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isDev, devKeys]);

  /* ── dev metrics → main process log ────────────────────────────────────── */

  useEffect(() => {
    if (!isDev || !readStats || !engine) return;
    const id = window.setInterval(() => {
      const s = readStats();
      const hist = engine.takeIntervals();
      const bins: string[] = [];
      hist.forEach((count, ms) => {
        if (count > 0) bins.push(`${ms}:${count}`);
      });
      window.tessa.reportMetrics(`PLASMA-IV ${bins.join(',')}`);
      window.tessa.reportMetrics(
        `PLASMA path=${s.path} fps=${s.fps.toFixed(1)} target=${s.targetFps.toFixed(0)} ` +
          `iv=${s.intervalP50.toFixed(2)}/${s.intervalP95.toFixed(2)} dropped=${s.dropped}/${s.samples} ` +
          `scale=${s.scale.toFixed(2)} gpu=${s.gpuP50.toFixed(2)}/${s.gpuP90.toFixed(2)} ` +
          `cost=${s.costP50.toFixed(2)}/${s.costP95.toFixed(2)} frames=${s.framesDrawn} ` +
          `focused=${s.focused} refresh=${s.refreshMs.toFixed(2)} R=${s.radius.toFixed(1)} ` +
          `maxInput=${s.maxShaderInput.toFixed(2)} load=${s.load.toFixed(3)} cpu=${s.cpu.toFixed(3)} mem=${s.mem.toFixed(3)} heap=${((performance as unknown as { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize ?? 0)} ` +
          `state=${agentStateStore.get()} canvas=${s.canvas.cssW}x${s.canvas.cssH}css/${s.canvas.bufW}x${s.canvas.bufH}buf ` +
          `gpuName="${s.rendererShort}" reason="${s.reason}" vis=${document.visibilityState}`,
      );
    }, 5000);
    return () => window.clearInterval(id);
  }, [isDev, readStats, engine]);

  useEffect(() => {
    if (!devKeys || !engine) return;
    return installDevKeys({
      engine,
      report: (line) => window.tessa.reportMetrics(line),
      toggleOverlay: () => setShowOverlay((v) => !v),
    });
  }, [devKeys, engine]);

  const contextLoss = bootstrap?.contextLoss ?? null;
  useEffect(() => {
    if (!contextLoss || !engine) return;
    const id = window.setTimeout(() => {
      const ok = engine.loseContextForTest(contextLoss.restoreMs);
      window.tessa.reportMetrics(
        `CONTEXT-LOSS forced=${ok} restoreMs=${contextLoss.restoreMs} t=${performance.now().toFixed(1)}`,
      );
    }, contextLoss.atMs);
    return () => window.clearTimeout(id);
  }, [contextLoss, engine]);

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

  useLayoutEffect(() => {
    const el = calRef.current;
    const stage = el?.parentElement;
    if (!el || !stage) return;
    const measure = (): void => {
      const a = el.getBoundingClientRect();
      const s = stage.getBoundingClientRect();
      const next: Box = {
        left: Math.round(a.left - s.left),
        top: Math.round(a.top - s.top),
        right: Math.round(a.right - s.left),
        bottom: Math.round(a.bottom - s.top),
      };
      setCalBox((prev) => (sameBox(prev, next) ? prev : next));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    ro.observe(stage);
    return () => ro.disconnect();
  }, []);

  const cardLeft = cardPresent
    ? canvasW - tokenPx('--sp-5', 24) - Math.min(CARD_MAX_W, canvasW - tokenPx('--sp-6', 32))
    : null;
  const layout = plasmaLayout({
    width: canvasW,
    height: canvasH,
    calendar: calBox,
    drawerLeft: rail ? canvasW - drawerWidth : null,
    cardLeft,
  });
  const offsetPx = layout.offsetX;
  const offsetYPx = layout.offsetY;
  const fit = layout.fit;

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
    '--sphere-cx': `${layout.cx.toFixed(1)}px`,
    '--sphere-cy': `${layout.cy.toFixed(1)}px`,
    '--sphere-r': `${layout.r.toFixed(1)}px`,
    '--readout-top': `${layout.readoutTop.toFixed(1)}px`,
    '--readout-w': `${layout.readoutWidth.toFixed(1)}px`,
  } as React.CSSProperties;

  const onEngineReady = useCallback((next: PlasmaEngine) => {
    setEngine(next);
  }, []);

  const onPath = useCallback((path: RenderPath, reason: string) => {
    window.tessa.reportMetrics(`PLASMA-PATH ${path} — ${reason}`);
  }, []);

  const onMark = useCallback((mark: MarkReport) => {
    window.tessa.reportMetrics(
      `MARK "${mark.label}" first=${mark.firstMs.toFixed(2)}ms` +
        `${mark.budgetMs > 0 ? ` settle=${mark.settleMs === null ? 'none' : `${mark.settleMs.toFixed(2)}ms`} budget=${mark.budgetMs}` : ''}` +
        `${mark.dropped ? ' DROPPED' : ''}`,
    );
  }, []);

  useEffect(() => {
    // The layout's own numbers, reported so a disagreement between what this
    // computes and what the sphere renders is visible in a log rather than
    // inferred from a screenshot. It was inferred once and the inference was
    // wrong by 50 px.
    if (isDev) {
      window.tessa.reportMetrics(
        `LAYOUT canvas=${canvasW}x${canvasH} rail=${rail ?? 'none'} card=${cardPresent} ` +
          `cal=${calBox ? `${calBox.left},${calBox.top}..${calBox.right},${calBox.bottom}` : 'none'} ` +
          `naturalR=${layout.naturalR.toFixed(1)} R=${layout.r.toFixed(1)} fit=${layout.fit.toFixed(3)} ` +
          `cx=${layout.cx.toFixed(1)} cy=${layout.cy.toFixed(1)} ` +
          `ribbon=${layout.ribbonLeft.toFixed(0)}..${layout.ribbonRight.toFixed(0)}x${layout.ribbonTop.toFixed(0)}..${layout.ribbonBottom.toFixed(0)} ` +
          `readout=${layout.readoutTop.toFixed(0)}+${STAGE_BANDS.readout} w${layout.readoutWidth.toFixed(0)} ` +
          `calendarClash=${layout.calendarClash}`,
      );
    }
  }, [isDev, canvasW, canvasH, rail, cardPresent, calBox, layout]);

  /**
   * JOBS OPENS ITSELF WHEN THERE IS SOMETHING IN IT.
   *
   * His ruling: a panel appears when it becomes active, and stays until he
   * dismisses it — no timeout, no auto-close. The trigger is built; NOTHING
   * FIRES IT TODAY. Jobs waits on a Phase 5 queue that does not exist, so the
   * condition below is permanently false right now, and that is the honest
   * state rather than a stub that opens on nothing.
   *
   * CHAT no longer has an entry here: the typed input lives in TRACE and is
   * opened by hand (the rail, or Ctrl+Shift+Space). A reply to something he
   * typed arrives in a drawer he already has open, and a spoken turn lands
   * in TRACE exactly as it did before — neither is a reason to open a panel
   * he did not ask for.
   *
   * One-shot per transition, not per render: `openedFor` remembers what it has
   * already opened for, so dismissing a panel does not have it spring back on
   * the next tick. That is the difference between "opens when it becomes
   * active" and "cannot be closed while active".
   */
  const openedFor = useRef<{ jobs: boolean }>({ jobs: false });
  const jobsActive = false; // no producer: evt.job.* is never emitted
  useEffect(() => {
    if (jobsActive && !openedFor.current.jobs) {
      openedFor.current.jobs = true;
      railStore.set('jobs');
    }
    if (!jobsActive) openedFor.current.jobs = false;
  }, [jobsActive]);

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
    const wall = performance.timeOrigin + at;
    const sentAt = pending.ts ? Date.parse(pending.ts) : Number.NaN;
    window.tessa.reportMetrics(
      `STATE-VISIBLE state=${state} ` +
        `queuedMs=${pending.queuedMs.toFixed(2)} ` +
        `drawnMs=${(at - pending.at).toFixed(2)} ` +
        `totalMs=${(at - pending.arrivedAt).toFixed(2)} ` +
        `tsToFrameMs=${Number.isFinite(sentAt) ? (wall - sentAt).toFixed(1) : 'na'} ` +
        `mainToFrameMs=${pending.mainAt > 0 ? (wall - pending.mainAt).toFixed(1) : 'na'}`,
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
          {!bootstrap ? null : (
            <Sphere
              offsetPx={offsetPx}
              offsetYPx={offsetYPx}
              fit={fit}
              forceFallback={bootstrap.forceFallback}
              clock={bootstrap.clock}
              onEngineReady={onEngineReady}
              onStateRendered={onStateRendered}
              onMark={onMark}
              onPath={onPath}
            />
          )}

          {/* Top row, as the reference has it: the state centre, the clock
              right. The CALM pill beside the clock is NOT built — nothing in
              core/ maps to it. See the report. */}
          <StateChip />
          <Clock />

          {/* §R.2 — the HUD sits over the stage, never inside a drawer.
              The approval stack is FIRST and above the others: it interrupts
              where they are ambient, and a toast must never cover the buttons
              of a red action. */}
          <ApprovalStack />
          <NotificationStack />
          <div className="readout">
            <Caption />
            <LastLine />
          </div>

          {/* THE CALENDAR IS THE ONLY PERMANENT PANEL, bottom-left.
              His ruling: nothing else shows until it has something to say or he
              opens it. Everything that used to sit on the stage — the status
              card, the jobs list, the chat, the telemetry column — is behind a
              rail now, which is the mechanism that already existed for exactly
              this. See RAIL_IDS.

              It is the one panel always on screen because the month with today
              marked is true without a producer, and because a glanceable
              always-on surface at 2am should say the date. */}
          <aside className="cal-dock" ref={calRef}>
            <Calendar />
            <Today />
          </aside>
        </main>

        <Drawer
          title={railById(lastRail.current).label}
          open={rail !== null}
          onClose={() => railStore.set(null)}
          footer={railById(lastRail.current).footer?.()}
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
      {isDev && showOverlay ? <DevOverlay readStats={readStats} devKeys={devKeys} /> : null}
    </div>
  );
}
