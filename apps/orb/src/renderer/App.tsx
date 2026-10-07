import { useCallback, useEffect, useRef, useState } from 'react';

import { AGENT_STATES, type AgentState } from '@tessa/protocol';

import type { BootstrapInfo } from '../shared/ipc-contract.ts';
import { parseDevScript, runDevScript } from './dev-drive.ts';
import { installDevKeys } from './dev-keys.ts';
import { applyTheme, currentTheme, isThemeId, themeForKey, type ThemeId } from './theme.ts';
import {
  approvalArrived,
  approvalCleared,
  approvalRefused,
  approvalsStore,
  approvalsSweepExpired,
} from './state/approval-store.ts';
import { installChurnProbe } from './ui/churn.ts';
import { Interface } from './ui/Interface.tsx';
import {
  auditLoadedStore,
  diskStore,
  lastHealthStore,
  memTotalStore,
  noteJobs,
  pushBeat,
  pushMachine,
  pushNote,
  stateSinceStore,
  threatStore,
} from './ui/stores.ts';
import { StateDwell } from './state/state-dwell.ts';
import { composeLinkDropped, composeNoteLine, composeNoteState, composePatch } from './state/compose-store.ts';
import { startTick } from './state/tick.ts';
import { DevOverlay } from './layout/DevOverlay.tsx';
import { Sphere } from './scene/Sphere.tsx';
import type { MarkReport, PlasmaEngine, RenderPath } from './scene/plasma-engine.ts';
import { captionText, captionWords, jobsStore } from './state/plasma-inputs.ts';
import {
  agentDetailStore,
  agentStateStore,
  auditStore,
  AUDIT_MAX,
  calendarStore,
  connectionStore,
  devStore,
  healthStore,
  micStore,
  ptySessionsStore,
  pushHealthSample,
  transcriptStore,
  turnTimingStore,
  TRANSCRIPT_MAX,
  useStore,
} from './state/store.ts';

const AUDIT_QUERY_LIMIT = 800;

function editableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

export function App() {
  const mic = useStore(micStore);

  const [bootstrap, setBootstrap] = useState<BootstrapInfo | null>(null);
  const [engine, setEngine] = useState<PlasmaEngine | null>(null);
  const readStats = engine ? engine.stats : null;
  const [showOverlay, setShowOverlay] = useState(false);

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
      memTotalStore.set(info.memTotalMB);

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
    let wasConnected = false;
    let everConnected = false;
    void window.tessa.getSnapshot().then((snap) => {
      if (!alive) return;
      if (snap.connection.phase === 'connected') {
        wasConnected = true;
        everConnected = true;
      }
      connectionStore.set(snap.connection);
      if (snap.health) {
        healthStore.set(snap.health);
        lastHealthStore.set(snap.health);
        pushHealthSample(snap.health);
      }
      if (snap.audit.length > 0) {
        auditStore.set([...snap.audit].reverse().slice(0, AUDIT_MAX));
        auditLoadedStore.set({ at: Date.now(), rows: snap.audit.length, limit: AUDIT_QUERY_LIMIT });
      }
      if (snap.calendar) calendarStore.set(snap.calendar);
      if (snap.disk) diskStore.set(snap.disk);
      if (snap.ptySessions.length > 0) ptySessionsStore.set(snap.ptySessions);
      // Unconditional, unlike the two above: `claimed: false` is a real answer
      // and must overwrite the placeholder, not be skipped as "empty".
      micStore.set(snap.mic);
      // Approvals main was already holding. A red action that arrived while
      // this bundle was parsing must not be left with no card.
      for (const request of snap.approvals) approvalArrived(request);
    });
    const offConnection = window.tessa.onConnection((status) => {
      const up = status.phase === 'connected';
      if (wasConnected && !up) {
        pushNote('LINK', `Daemon link lost (${status.phase}). Retrying.`);
        const unconfirmed = composeLinkDropped(Date.now());
        if (unconfirmed) {
          pushNote(
            'CHAT',
            unconfirmed.stage === 'ack'
              ? 'Your message was not confirmed before the link dropped. Nothing was queued. It is still in the box.'
              : 'Her answer to your message did not arrive before the link dropped. Nothing was queued or resent.',
          );
        }
      }
      if (!wasConnected && up && everConnected) pushNote('LINK', 'Daemon link back. Values are live again.');
      wasConnected = up;
      if (up) everConnected = true;
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
      lastHealthStore.set(health);
      pushHealthSample(health);
      pushBeat(Date.now());
    });
    const offJobs = window.tessa.onJobs((jobs) => {
      jobsStore.set(jobs);
      noteJobs(jobs);
    });
    const offCalendar = window.tessa.onCalendarToday((today) => calendarStore.set(today));
    const offDisk = window.tessa.onDisk((disk) => diskStore.set(disk));
    const offMachine = window.tessa.onMachineLoad((load) => pushMachine(load.cpu, load.mem));
    const offState = agentStateStore.subscribe(() => {
      const state = agentStateStore.get();
      if (stateSinceStore.get().state !== state) stateSinceStore.set({ state, at: Date.now() });
    });
    const offPartial = window.tessa.onTranscriptPartial((partial) => {
      if (partial.role === 'assistant') captionText(partial.messageId, partial.text, partial.done);
    });
    const offWords = window.tessa.onVoiceWords((words) => captionWords(words));

    // SENTINEL's two real sources. History seeds the list; the live stream
    // prepends onto it, newest first, bounded so a long-running surface cannot
    // grow without limit.
    const offAuditHistory = window.tessa.onAuditHistory((entries) => {
      auditStore.set([...entries].reverse().slice(0, AUDIT_MAX));
      auditLoadedStore.set({ at: Date.now(), rows: entries.length, limit: AUDIT_QUERY_LIMIT });
    });
    const offAuditAppended = window.tessa.onAuditAppended((entry) =>
      auditStore.set([entry, ...auditStore.get()].slice(0, AUDIT_MAX)),
    );
    const offPty = window.tessa.onPtySessions((sessions) => ptySessionsStore.set(sessions));
    const offMic = window.tessa.onMicState((state) => micStore.set(state));
    const offNote = window.tessa.onNotification((note) => {
      if (note.id === 'ptt-chord-failed') return;
      pushNote(note.level === 'info' ? 'ORB' : note.level.toUpperCase(), `${note.title}. ${note.body}`);
    });
    const offApproval = window.tessa.onApprovalRequested((request) => {
      const fresh = !approvalsStore.get().some((e) => e.request.requestId === request.requestId);
      approvalArrived(request);
      if (fresh) pushNote('APPROVAL', `${request.tool} is waiting on you.`, 'wait');
    });
    const offApprovalCleared = window.tessa.onApprovalCleared((cleared) => {
      const gone = approvalCleared(cleared.requestId);
      if (!gone) return;
      const what = gone.request.tool;
      const msg =
        cleared.reason === 'daemonRestarted'
          ? `${what}: the daemon restarted, the request is gone. Nothing ran.`
          : cleared.reason === 'expired'
            ? `${what}: the 30-minute window lapsed. Nothing ran.`
            : `${what}: ${(cleared.decision ?? 'resolved').toUpperCase()}${gone.request.fixture ? ' (fixture, not sent)' : ''}.`;
      pushNote('APPROVAL', msg);
    });
    const offApprovalRefused = window.tessa.onApprovalRefused((refusal) => {
      const entry = approvalsStore.get().find((e) => e.request.requestId === refusal.requestId);
      if (entry && !refusal.requestStillPending) {
        pushNote('APPROVAL', `${entry.request.tool}: the daemon refused (${refusal.code}): ${refusal.message}`);
      }
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
      offCalendar();
      offDisk();
      offMachine();
      offState();
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

  useEffect(() => {
    if (mic.mode !== 'toggle' || !mic.chord || mic.chordRegistered) return;
    pushNote('MIC', `Push-to-talk shortcut unavailable. ${mic.chord} is already held by another application. Push-to-talk still works while the Orb has focus.`);
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
      if (!event.ctrlKey || !event.shiftKey || event.altKey || event.metaKey) return;
      if (editableTarget(event.target)) return;
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
      const before = approvalsStore.get();
      const expired = approvalsSweepExpired();
      for (const requestId of expired) {
        window.tessa.reportMetrics(
          `APPROVAL-EXPIRED ${requestId} — invalidated locally, nothing sent (CONTRACT §5.1)`,
        );
        const tool = before.find((e) => e.request.requestId === requestId)?.request.tool ?? requestId;
        pushNote('APPROVAL', `${tool}: the approval window lapsed. Nothing was sent.`);
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
      if (editableTarget(event.target)) return;
      const match = event.ctrlKey && event.altKey && !event.metaKey && isSpace(event);
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
    if (!isDev) return;
    function onKeyDown(event: KeyboardEvent) {
      if (!event.altKey || event.ctrlKey || event.shiftKey || event.metaKey) return;
      if (editableTarget(event.target)) return;
      // Alt+0 toggles the frame-metrics overlay. Same family as the Alt+1…6
      // state cycler and the only digit it does not already use.
      if (/^Digit0$/.test(event.code) || event.key === '0') {
        setShowOverlay((v) => !v);
        event.preventDefault();
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isDev]);

  useEffect(() => {
    if (!bootstrap?.churn) return;
    return installChurnProbe((line) => window.tessa.reportMetrics(line));
  }, [bootstrap?.churn]);

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
      onThreat: () => {
        threatStore.set({ at: Date.now(), seen: false });
        pushNote('SENTINEL', 'Dev threat flare (T key). Not a real event.', 'threat');
      },
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
    <>
      {!bootstrap ? null : (
        <Interface
          bootstrap={bootstrap}
          engine={engine}
          sphere={
            <Sphere
              forceFallback={bootstrap.forceFallback}
              clock={bootstrap.clock}
              onEngineReady={onEngineReady}
              onStateRendered={onStateRendered}
              onMark={onMark}
              onPath={onPath}
            />
          }
        />
      )}


      {/* Dev-only AND off by default. `isDev` alone was the wrong gate: the
          owner runs `npm run dev`, so it was true for him, and the overlay sat
          over the lower-left of his sphere every day. --dev-overlay shows it at
          launch; Alt+0 toggles it. */}
      {isDev && showOverlay ? <DevOverlay readStats={readStats} devKeys={devKeys} /> : null}
    </>
  );
}
