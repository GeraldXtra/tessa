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
  /** main → renderer, push. `evt.permission.request` — one approval card. */
  approvalRequested: 'zoey:approval-requested',
  /** main → renderer, push. A requestId that is no longer pending. */
  approvalCleared: 'zoey:approval-cleared',
  /**
   * main → renderer, push. The daemon REFUSED a decision.
   *
   * Separate from `approvalCleared` because the two mean opposite things about
   * whether the card should still be on screen. A refusal from `resolve_edit`
   * leaves the request pending daemon-side (core/brain/executor.py restores it
   * on the way out), so the card must come BACK with his edit intact. A refusal
   * from the pending lookup means the request is gone and the card must not.
   */
  approvalRefused: 'zoey:approval-refused',
  /**
   * renderer → main, send. `{ requestId, decision }` — the owner's answer.
   *
   * The ONE channel on this bridge that carries a caller-supplied string, and
   * it is fenced accordingly: `decision` is narrowed to the two values a
   * surface may send (CONTRACT §5.1 — `expired` is daemon-only), and
   * `requestId` is only ever echoed back against a request main already holds.
   * Main will not forward an id it never issued a card for.
   */
  approvalRespond: 'zoey:approval-respond',
  /**
   * renderer → main, send. One of five theme ids, for persistence only.
   *
   * The renderer already OWNS what is on screen — it sets the custom properties
   * itself. This channel exists solely so the choice survives a restart, which
   * needs the filesystem, which the renderer does not have. Main validates the
   * id against its own list and refuses anything else, so a compromised
   * renderer's worst outcome here is a file containing a word.
   */
  themeSet: 'zoey:theme-set',
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
  /**
   * How many times the brain has been called this daemon run, and BY WHAT.
   *
   * Session 1 added both to `evt.daemon.health` explicitly for this surface
   * (core/telemetry/health.py:120-147 — its own comment reads "PULSE shows
   * gemini - 14 calls"). They were arriving and this interface was dropping
   * them at the boundary, which is why nothing on screen has ever named the
   * engine answering.
   *
   * `brainEngine` is the single most consequential fact available: a silent
   * fallback from a cloud model to a local one changes accuracy, latency and
   * spend, and until now nothing would have shown it.
   *
   * Optional because an older daemon does not send them, and §3.2 requires
   * tolerating that rather than rendering a zero that looks like a measurement.
   */
  brainCalls?: number;
  brainEngine?: string;
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

/* ───────────────────────────────────────────────────── approval (§4.1/§5.1) */

/**
 * `evt.permission.request`, carried whole.
 *
 * CONTRACT §4.1: `{ requestId, tier, tool, args, provenance, expiresAt }`, all
 * six required — `provenance` explicitly so, per §6.2.
 *
 * `args` is `unknown`-valued on purpose. It is arbitrary tool input the owner
 * is being asked to authorise, it may contain anything a model produced, and
 * the card's whole job is to show it as it is rather than as the surface would
 * prefer it. Nothing here narrows or reshapes it.
 */
export interface PermissionRequest {
  requestId: string;
  tier: string;
  tool: string;
  args: Record<string, unknown>;
  provenance: string;
  /** ISO. The daemon's own 30-minute window (core/brain/approvals.py). */
  expiresAt: string;
  /** Wall clock of arrival, so the countdown survives a clock skew. */
  receivedAt: number;
  /**
   * DEV ONLY. Set by `--fixture-approval=`, never by the socket.
   *
   * A fabricated card on a security surface has to announce itself, or a
   * screenshot of one becomes evidence for a claim it cannot support. When this
   * is true the card renders a banner saying so, and main will not forward a
   * decision for it to the daemon — there is no daemon request behind it.
   */
  fixture?: true;
}

/** A surface may send these two and no others. CONTRACT §5.1. */
export type ApprovalDecision = 'approve' | 'deny';

/**
 * Why a card left the screen.
 *
 * `disconnected` is GONE, and its removal is the point. Session 1 ruled that a
 * pending request SURVIVES the deciding surface's disconnect — the daemon keeps
 * it and any surface may decide it — so clearing on a dropped socket would
 * destroy a card for an action that is still live and still waiting on him.
 *
 * What does kill a request is the DAEMON restarting: `ApprovalGate.pending` is
 * an in-memory dict rebuilt per process (core/brain/approvals.py:167), so a new
 * daemon has forgotten everything the old one held. Those two rules pull in
 * opposite directions, and `daemonRestarted` is how they are told apart — see
 * the instance check in main/index.ts.
 */
export type ApprovalClearReason = 'resolved' | 'expired' | 'daemonRestarted';

export interface ApprovalCleared {
  requestId: string;
  reason: ApprovalClearReason;
  /** Present when the daemon resolved it: approve | deny | expired. */
  decision?: string;
}

/**
 * The daemon refused a decision. CONTRACT §5.4 error codes.
 *
 * `requestStillPending` is derived in main from the code, by reading what
 * `core/brain/executor.py::execute_approved` actually does on each failure
 * path — it pops the request first and puts it back only for a rejected edit.
 * Getting this backwards either strands a live request with no card, or leaves
 * an approvable card for something the daemon has already discarded.
 */
export interface ApprovalRefusal {
  requestId: string;
  code: string;
  message: string;
  requestStillPending: boolean;
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
   * DEV ONLY, 0 = off. `--probe-limb=<ms>`. Reads an 80x160 patch on the
   * sphere's limb, which is cheap enough to sustain a 50 ms cadence and is the
   * one place the spin does not swamp the turbulence. See PROBE_LIMB_W.
   */
  probeLimbMs: number;
  /** DEV ONLY, 0 = off. `--probe-centre=<ms>`. Same patch at the disc centre. */
  probeCentreMs: number;
  /**
   * DEV ONLY. `--dev-overlay` shows the frame-metrics overlay at launch.
   *
   * Default OFF even in a dev build, which is the change: it used to render
   * whenever `isDev` was true, and the owner runs `npm run dev`, so it sat over
   * the lower-left of his sphere every day. Alt+0 toggles it at runtime.
   */
  devOverlay: boolean;
  /**
   * DEV ONLY. `--force-state=<agent state>`, already validated against the
   * closed set in main. Null means the live/cycler state wins as usual.
   */
  forcedState: string | null;
  /**
   * DEV ONLY. `--dev-drive=click:<sel>;wait:<ms>;…` — a tiny script of UI
   * actions the renderer runs on mount, so a drawer can be opened and a toggle
   * clicked without the Orb ever needing the foreground. See dev-drive.ts.
   */
  devScript: string | null;
  /**
   * The theme to paint on first frame — restored from disk, or `cyan` when
   * there is nothing valid to restore.
   *
   * It rides the bootstrap rather than a push channel for the same reason the
   * audit history rides the snapshot: a push arrives at whatever listener
   * exists, and on first paint there is none. A theme applied one frame late is
   * a visible flash of the wrong palette on every launch.
   */
  theme: string;
  /** Why that theme. Logged, so a silent fallback to cyan cannot look chosen. */
  themeReason: string;
  /**
   * DEV ONLY. `--force-depth=<0..1>` — §R.1 depth shading's falloff, or null
   * for the engine default.
   *
   * 1.0 disables the depth term and reproduces the shell exactly as it was
   * before it existed, so a before/after comparison is one flag on one binary
   * at one window size rather than two builds.
   */
  forcedDepth: number | null;
  /**
   * DEV ONLY. `--force-sphere=<rimGain>,<rimSize>,<bodyBright>,<bodySize>`.
   *
   * The four numbers that decide whether the shell reads as a surface or as a
   * translucent cloud. The rim was measured against the reference's own direct
   * capture rather than judged by eye, and a cold build takes ~100 s — this
   * flag is what makes a twelve-point sweep affordable. `bodyBright` and
   * `bodySize` are multipliers on the per-state values, so a sweep cannot
   * disturb the ordering between the six states.
   */
  forcedSphere: {
    gain: number;
    size: number;
    bodyBright: number;
    bodySize: number;
  } | null;
  /**
   * DEV ONLY. `--force-aura=<0..1>` pins the resource aura's load.
   *
   * The aura is driven by the daemon's own cpuPct, which idles at 0-2.8%.
   * Making that climb means making her work, which needs a voice turn. This
   * exists so the instrument's visible range can be rendered and measured
   * rather than argued about.
   */
  forcedAura: number | 'cycle' | null;
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
  /**
   * Approvals main is still holding.
   *
   * Same race as the audit history, with a worse consequence: a red-tier
   * request that arrived while the renderer was still parsing its bundle would
   * leave main holding a pending action with no card on screen for it. The
   * owner would see nothing, the daemon would wait 30 minutes, and both sides
   * would look correct.
   */
  approvals: PermissionRequest[];
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
  /** Returns an unsubscribe function. A new approval card. */
  onApprovalRequested(listener: (request: PermissionRequest) => void): () => void;
  /** Returns an unsubscribe function. A card that must leave the screen. */
  onApprovalCleared(listener: (cleared: ApprovalCleared) => void): () => void;
  /** Returns an unsubscribe function. The daemon refused a decision. */
  onApprovalRefused(listener: (refusal: ApprovalRefusal) => void): () => void;
  /**
   * The owner's answer. Main validates the id against what it holds.
   *
   * `editedArgs` is CONTRACT §5.1's new optional field. Send it ONLY when he
   * actually changed something: the daemon computes `was_edited` by comparing
   * the merged args to the original, and an unchanged copy would ride through
   * as a no-op while still costing the 16 KB budget and an extra audit line.
   */
  respondToApproval(
    requestId: string,
    decision: ApprovalDecision,
    editedArgs?: Record<string, unknown>,
  ): void;
  /** Persist the theme choice. Display has already changed; this only saves it. */
  setTheme(theme: string): void;
  /** Report a push-to-talk key edge. Main decides what it means. */
  pushToTalkEdge(edge: 'down' | 'up'): void;
  setPushToTalkMode(mode: PttMode): void;
  retryConnection(): void;
  minimizeWindow(): void;
  closeWindow(): void;
  /** Dev only; a no-op string sink in production. */
  reportMetrics(line: string): void;
}
