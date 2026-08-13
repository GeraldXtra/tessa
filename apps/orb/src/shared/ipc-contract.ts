/**
 * The complete surface of what crosses the contextBridge.
 *
 * Read this file as a security document, not a convenience layer.
 *
 * CONTRACT §2.3 puts the WebSocket client in the main process because a
 * renderer cannot set an arbitrary `Origin`, and because a token held in a web
 * context is one XSS away from any rendered content. That guarantee is only
 * worth something if the bridge does not hand the renderer an equivalent
 * capability by the back door. So:
 *
 *   • No channel carries the token. Not redacted, not hashed — absent.
 *   • No channel accepts a caller-supplied message type or payload. There is no
 *     "send this frame" primitive, so the renderer cannot drive the daemon even
 *     indirectly.
 *   • Everything main → renderer is connection STATUS: a small closed union and
 *     a few strings safe to print on screen.
 *
 * Adding a channel here is the moment to ask whether it re-opens the hole.
 */

/* ────────────────────────────────────────────────────────── channel names */

export const IPC = {
  /** renderer → main, invoke. One round trip at startup. */
  bootstrap: 'zoey:bootstrap',
  /** renderer → main, invoke. Current status, for first paint. */
  getConnection: 'zoey:get-connection',
  /**
   * renderer → main, invoke. Everything already received, for first paint.
   *
   * Push channels only deliver what happens NEXT. The daemon connection
   * completes and `res.audit` lands in well under the time the 725 kB renderer
   * bundle takes to parse and mount, so a push-only design loses the entire
   * audit history to a race and SENTINEL shows NO DATA while main's log says it
   * forwarded 100 entries. The renderer pulls this once on mount.
   */
  getSnapshot: 'zoey:get-snapshot',
  /** main → renderer, push. Status changed. */
  connectionChanged: 'zoey:connection-changed',
  /** main → renderer, push. evt.daemon.health, every 5 s while subscribed. */
  healthChanged: 'zoey:health-changed',
  /** main → renderer, push. evt.agent.state — validated against the closed set. */
  agentStateChanged: 'zoey:agent-state-changed',
  /** main → renderer, push. One audit entry, from evt.audit.appended. */
  auditAppended: 'zoey:audit-appended',
  /** main → renderer, push. res.audit history, in reply to cmd.audit.query. */
  auditHistory: 'zoey:audit-history',
  /** main → renderer, push. evt.pty.sessions roster (CONTRACT §4.2). */
  ptySessions: 'zoey:pty-sessions',
  /** main → renderer, push. A completed transcript line. */
  transcriptLine: 'zoey:transcript-line',
  /**
   * main → renderer, push. A display was added, removed, or changed mode.
   *
   * The sphere's pacer divides the refresh rate to reach 30 fps, and it derives
   * the divider from the MEASURED rAF interval. Moving to a monitor with a
   * different refresh silently changes the right answer — a 144 Hz panel wants
   * a divider of 5, not 2 — and nothing would report an error, just a wrong
   * frame rate. This forces an immediate re-measure instead of waiting for the
   * estimate to drift into place over the next sampling window.
   */
  displayChanged: 'zoey:display-changed',
  /** main → renderer, push. One notification for the §R.2 stack. */
  notify: 'zoey:notify',
  /**
   * main → renderer, push. The microphone claim, as the DAEMON confirmed it.
   *
   * Never local optimism. If `cmd.voice.pushToTalk` is refused — an older
   * daemon answering `err.protocol.unknownType`, a surface check, a dropped
   * socket — this stays false and the indicator stays dark. A surface that
   * lights "MIC LIVE" on its own intent is the fabricated-UI failure pointed at
   * the one thing where being wrong is a privacy breach rather than a cosmetic
   * one.
   */
  micState: 'zoey:mic-state',
  /**
   * renderer → main, send. One push-to-talk edge: 'down' or 'up'.
   *
   * Deliberately an EDGE, not an action. The renderer does not get to say
   * "start recording" — it reports that a key moved, and main decides what that
   * means under the current mode. Same reason nothing here takes a message type
   * or a payload.
   */
  pttEdge: 'zoey:ptt-edge',
  /** renderer → main, send. 'toggle' | 'hold'. */
  pttSetMode: 'zoey:ptt-set-mode',
  /** renderer → main, send. Owner pressed RETRY after a terminal failure. */
  retryConnection: 'zoey:retry-connection',
  /**
   * renderer → main, send. DEV ONLY — one line of sphere metrics every 5 s,
   * logged by main.
   *
   * It exists so frame numbers can be read out of a log file instead of
   * squinted at in a screenshot. The previous round of performance work was
   * argued from screenshotted values that turned out to be stale, and a metric
   * you cannot script is a metric you cannot check.
   */
  devMetrics: 'zoey:dev-metrics',
  /** renderer → main, send. Frameless window needs its own controls. */
  windowMinimize: 'zoey:window-minimize',
  windowClose: 'zoey:window-close',
} as const;

/* ─────────────────────────────────────────────────────── connection status */

/**
 * Where the daemon connection is. Deliberately distinguishes the three
 * *terminal* failures from the retryable ones, because they need different
 * words on screen and — more importantly — different retry behaviour.
 * See `ws-client.ts` for why retrying `authRejected` is dangerous.
 */
export type ConnectionPhase =
  /** No usable runtime.json: absent, malformed, or its pid is dead. */
  | 'offline'
  /** Socket opening, or cmd.hello in flight. */
  | 'connecting'
  /** res.hello received. */
  | 'connected'
  /** Close 4401. Terminal until the owner retries or the daemon restarts. */
  | 'authRejected'
  /** Close 4409. Terminal, full stop — a retry cannot fix a version mismatch. */
  | 'protocolMismatch'
  /** Transport failure or close 4408. Retrying, with backoff. */
  | 'reconnecting';

export interface ConnectionStatus {
  phase: ConnectionPhase;
  /** From res.hello. Absent unless phase === 'connected'. */
  daemonVersion?: string;
  /** From res.hello. Shown truncated; useful when correlating with the audit log. */
  sessionId?: string;
  /** The daemon's PROTOCOL_VERSION, for the mismatch message. */
  daemonProtocolVersion?: number;
  /**
   * One short human-readable line for the status bar.
   * NEVER contains the token — see `redactForDisplay` in ws-client.ts.
   */
  detail?: string;
  /** Present while reconnecting, so the UI can count down honestly. */
  retryInMs?: number;
}

/* ───────────────────────────────────────────────────────────── daemon health */

/**
 * All six fields of `evt.daemon.health` (CONTRACT §4.1), carried whole.
 *
 * An earlier revision kept only `uptimeS`, on the grounds that the daemon
 * hardcoded the rest to zero/false and rendering `apiReachable: false` would
 * park a permanent false alarm on an always-on surface. That was true when it
 * was written and is no longer true: Session 1 landed the real values, and a
 * frame observed at 09:32:37Z carried
 *
 *   uptimeS 9211.6 · cpuPct 0.3 · memMB 20 · apiReachable true
 *   budgetSpent 0 · budgetCap 3000
 *
 * Dropping five real fields at the IPC boundary would mean the surface could
 * not show spend against the nightly cap even though the daemon is sending it.
 * Everything now crosses; what to DRAW with it is a rendering decision, made
 * where the rendering happens.
 */
export interface DaemonHealth {
  uptimeS: number;
  cpuPct: number;
  memMB: number;
  apiReachable: boolean;
  /** Spend and cap in naira. CONTRACT §4.1; the cap is a hard nightly stop. */
  budgetSpent: number;
  budgetCap: number;
  /** Wall clock of arrival, for the staleness check. */
  receivedAt: number;
}

/* ────────────────────────────────────────────────────── push-to-talk / mic */

/**
 * How the trigger behaves.
 *
 *   toggle  start on press, stop on the next press. The default, because
 *           Gerald has said plainly he will not hold a key.
 *   hold    start on keydown, stop on keyup. Cannot leave the mic claimed by
 *           forgetting, but see the note in ptt-controller: `hold` only works
 *           while the Orb has focus, because Electron's globalShortcut has no
 *           key-release callback at all.
 */
export type PttMode = 'toggle' | 'hold';

/**
 * The microphone claim as the surface is entitled to describe it.
 *
 * `claimed` is set ONLY from a `res.ok` the daemon sent back. Everything else
 * here is local fact about the trigger, not about the microphone.
 */
export interface MicState {
  claimed: boolean;
  mode: PttMode;
  /** Date.now() when the daemon confirmed the claim. Null when not claimed. */
  since: number | null;
  /** The global accelerator, whether or not it is currently held. */
  chord: string;
  /** False when the OS refused it — the chord belongs to something else. */
  chordRegistered: boolean;
  /** Last refusal from the daemon. Surfaced, never swallowed. */
  lastError: string | null;
}

/** §R.2 notification stack. `evt.notification`'s levels (CONTRACT §4.1). */
export interface OrbNotification {
  id: string;
  level: 'info' | 'warn' | 'error';
  title: string;
  body: string;
}

/* ─────────────────────────────────────────────────── SENTINEL / TRACE data */

/**
 * One audit-log entry as the daemon actually returns it.
 *
 * NOTE this is NOT the shape of `evt.audit.appended` in CONTRACT §4.1, which
 * names the id field `entryId`. `res.audit` streams the raw log rows from
 * core/security/audit.py, which carry `seq` plus `detail`, `prev` and `hash`.
 * Both forms are read defensively at the boundary — §3.2 requires tolerating
 * unknown fields, and it cuts both ways.
 */
export interface AuditEntry {
  id: string;
  ts: string;
  actor: string;
  tool: string;
  tier: string;
  summary: string;
  provenance: string | null;
}

/** From `evt.pty.sessions`. The Orb subscribes read-only (CONTRACT §4.2). */
export interface PtySession {
  sessionId: string;
  profileId: string;
  cwd: string;
  title: string;
  startedAt: string;
  busy: boolean;
}

/**
 * A transcript line for TRACE.
 *
 * `provenance` is DERIVED from `role`, not sent: the transcript events carry a
 * Role, while the gutter is specified in provenance terms (§R.6). The mapping
 * is the conservative one — anything the model produced is `agent`, anything a
 * tool produced is `program`, and only what the owner typed is `human`.
 */
export interface TranscriptLine {
  messageId: string;
  role: string;
  provenance: string;
  text: string;
  ts: string;
}

/* ──────────────────────────────────────────────────────────── sphere tiers */

/**
 * Render rungs for the sphere, worst-case last.
 *
 * Four rungs rather than "WebGL or not" because the failure mode on this
 * machine is not a clean absence — an HD 620 on a legacy driver can hand back a
 * working WebGL2 context that is actually SwiftShader on the CPU, which would
 * quietly eat one of two physical cores.
 */
export type SphereTier = 'high' | 'med' | 'low' | 'dom';

export interface GpuHint {
  /** Raw `app.getGPUFeatureStatus()` value, e.g. 'enabled', 'disabled_software'. */
  webgl2: string;
  gpuCompositing: string;
  /** True when the status strings themselves say software rendering. */
  softwareSuspected: boolean;
  /** `--force-tier=<tier>` on the command line. Dev/verification aid. */
  forcedTier: SphereTier | null;
}

export interface BootstrapInfo {
  /** Sent to the daemon as `surfaceVersion`; shown in the status bar. */
  surfaceVersion: string;
  /** Gates the state cycler and the frame-time overlay. */
  isDev: boolean;
  gpu: GpuHint;
  /**
   * DEV ONLY, 0 = off. Sampling periods for the two buffer read-back probes,
   * from `--probe-geometry=<ms>` and `--probe-pulse=<ms>`.
   *
   * They are separate flags rather than one because they want opposite
   * cadences and cannot share a run: geometry reads the whole buffer and is
   * far too expensive to do at the rate the pulse needs, and doing it anyway
   * would perturb the frame timing of the animation being measured.
   */
  probeGeometryMs: number;
  probePulseMs: number;
  /**
   * DEV ONLY. `--force-state=<agent state>`, already validated against the
   * closed set in main. Null means the live/cycler state wins as usual.
   */
  forcedState: string | null;
}

/* ───────────────────────────────────────────────────── the bridge, in types */

/** Everything main has already received, replayed for a late-mounting renderer. */
export interface Snapshot {
  connection: ConnectionStatus;
  health: DaemonHealth | null;
  audit: AuditEntry[];
  ptySessions: PtySession[];
  /**
   * Included for the same reason the audit history is: a push channel only
   * delivers what happens NEXT, and the global chord can claim the microphone
   * before the renderer has finished mounting. A reload with the mic already
   * claimed must not paint a dark indicator.
   */
  mic: MicState;
}

export interface ZoeyBridge {
  bootstrap(): Promise<BootstrapInfo>;
  getConnection(): Promise<ConnectionStatus>;
  getSnapshot(): Promise<Snapshot>;
  /** Returns an unsubscribe function. */
  onConnection(listener: (status: ConnectionStatus) => void): () => void;
  /** Returns an unsubscribe function. */
  onHealth(listener: (health: DaemonHealth) => void): () => void;
  /** Returns an unsubscribe function. Daemon-authoritative agent state. */
  onAgentState(listener: (state: string) => void): () => void;
  /** Returns an unsubscribe function. Fires when the display layout changes. */
  onDisplayChanged(listener: () => void): () => void;
  /** Returns an unsubscribe function. One newly appended audit entry. */
  onAuditAppended(listener: (entry: AuditEntry) => void): () => void;
  /** Returns an unsubscribe function. The audit history reply. */
  onAuditHistory(listener: (entries: AuditEntry[]) => void): () => void;
  /** Returns an unsubscribe function. Full PTY session roster. */
  onPtySessions(listener: (sessions: PtySession[]) => void): () => void;
  /** Returns an unsubscribe function. One completed transcript line. */
  onTranscriptLine(listener: (line: TranscriptLine) => void): () => void;
  /** Returns an unsubscribe function. Daemon-confirmed microphone claim. */
  onMicState(listener: (state: MicState) => void): () => void;
  /** Returns an unsubscribe function. One notification for the §R.2 stack. */
  onNotification(listener: (note: OrbNotification) => void): () => void;
  /** Report a push-to-talk key edge. Main decides what it means. */
  pushToTalkEdge(edge: 'down' | 'up'): void;
  setPushToTalkMode(mode: PttMode): void;
  retryConnection(): void;
  minimizeWindow(): void;
  closeWindow(): void;
  /** Dev only; a no-op string sink in production. */
  reportMetrics(line: string): void;
}
