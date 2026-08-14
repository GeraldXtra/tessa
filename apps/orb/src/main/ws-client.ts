/**
 * The Orb's one and only WebSocket to the daemon. Main process, never renderer.
 *
 * CONTRACT §2.3: "The WebSocket client must not live in a browser/renderer
 * context. In Electron it lives in the main process; the renderer reaches it
 * over contextBridge IPC." The reason is §2.1's second control — a renderer
 * cannot set an arbitrary `Origin`, which is the check that actually stops a
 * hostile webpage from driving the agent. Holding the token here also keeps it
 * out of reach of anything rendered.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THE LOCKOUT HAZARD — why the retry logic is shaped the way it is
 *
 * core/server.py disables its listener after FIVE failed auth attempts in 60 s,
 * and only a daemon restart re-enables it. A conventional reconnect loop
 * (retry every second until it works) against a stale token would therefore
 * lock the owner out of his own daemon in about three seconds, from his own
 * app, with no attacker involved.
 *
 * The invariant this file maintains:
 *
 *     cmd.hello is never sent twice with the same (port, token) pair
 *     once that pair has been rejected with 4401.
 *
 * So the failure budget is spent at most ONCE per distinct token, no matter how
 * long the Orb runs or how many times it retries. Since the token rotates on
 * every daemon launch, a restart automatically clears the block — the Orb
 * notices via the file poll and reconnects on its own.
 *
 * Origin rejections are not counted by the daemon at all (core/server.py
 * record_failure), so they are not a lockout risk — but they are a bug if we
 * ever see one, and are surfaced loudly rather than retried.
 * ─────────────────────────────────────────────────────────────────────────────
 */

import { createHash } from 'node:crypto';
import type { IncomingMessage } from 'node:http';

import { WebSocket } from 'ws';
import type { RawData } from 'ws';

import {
  AGENT_STATES,
  CLOSE_CODES,
  HANDSHAKE_DEADLINE_MS,
  MAX_FRAME_BYTES,
  ORB_COMMANDS,
  PROTOCOL_VERSION,
  isEnvelope,
  makeEnvelope,
  ulid,
  type AgentState,
  type AllowedOrigin,
  type Envelope,
  type EvtAgentState,
  type EvtDaemonHealth,
  type ResHello,
  type Surface,
} from '@zoey/protocol';

import type {
  AuditEntry,
  ConnectionStatus,
  DaemonHealth,
  PtySession,
  TranscriptLine,
} from '../shared/ipc-contract.ts';
import { readRuntimeFile, type RuntimeInfo } from './runtime-file.ts';
import { TranscriptAssembler, type TranscriptDelta } from './transcript-assembler.ts';

/**
 * Topics subscribed after a successful hello.
 *
 * Prefix globs per CONTRACT §5.1. Verified against the daemon's matcher rather
 * than assumed: `core/server.py::topic_matches` strips the first segment of the
 * type before comparing, so `daemon.*` matches `evt.daemon.health` and
 * `agent.*` matches `evt.agent.state`.
 *
 * Only these two. The Orb has no use yet for job, transcript, permission or
 * audit traffic, and subscribing to events with nowhere to render them would
 * burn frames decoding JSON to drop it.
 */
const TOPICS = ['daemon.*', 'agent.*', 'audit.*', 'pty.*', 'transcript.*'] as const;

/** How much audit history SENTINEL asks for on connect. */
const AUDIT_HISTORY_LIMIT = 100;

/**
 * Role → provenance, for the TRACE gutter (§R.6).
 *
 * Deliberately conservative. CONTRACT §6.2 makes `human` the ONLY trusted
 * source, so anything not typed by the owner maps to something untrusted:
 * assistant text is `agent`, tool output is `program`. Getting this wrong in
 * the safe direction costs a duller colour; getting it wrong the other way
 * paints model-proposed text as though the owner wrote it.
 */
const ROLE_PROVENANCE: Record<string, string> = {
  user: 'human',
  assistant: 'agent',
  tool: 'program',
  system: 'system',
};

/**
 * Typed against the contract's allowlist, so a typo is a compile error rather
 * than a 403 at runtime. CONTRACT §2.1.
 */
const ORIGIN: AllowedOrigin = 'zoey://orb';
const SURFACE: Surface = 'orb';

/** How often to re-read runtime.json while there is no daemon. No socket is opened. */
const FILE_POLL_MS = 2_000;

/** Transport-failure backoff. Jittered, capped. */
const BACKOFF_MIN_MS = 500;
const BACKOFF_MAX_MS = 8_000;

/**
 * The daemon pings every 20 s (core/server.py ping_interval). Three missed
 * intervals means the link is dead in a way TCP has not noticed yet — a real
 * possibility on a laptop that suspends.
 */
const SILENCE_TIMEOUT_MS = 60_000;

/**
 * Scrub anything token-shaped out of a string before it reaches a log.
 *
 * The token is never deliberately logged and never appears in the URL (CONTRACT
 * §2.1 forbids that), so this should never fire. It exists because "should
 * never" and "cannot" are different words, and a token written to a log once is
 * leaked permanently.
 */
function scrub(text: string): string {
  return text.replace(/\b[0-9a-f]{64}\b/gi, '<redacted-token>');
}

/**
 * Envelope for an Orb-only command the contract has RESERVED but not yet given
 * a payload shape.
 *
 * `makeEnvelope` is keyed on `PayloadMap`, and `cmd.voice.pushToTalk` has no
 * entry there — CONTRACT §5.3 lists it under "Reserved", which is precisely the
 * state of "the name is agreed, the payload is not". Gerald has approved
 * `{ action: "start" | "stop" }` and will apply the §5.3 diff; until he does,
 * `packages/protocol` is shared and locked and this surface does not get to
 * edit it.
 *
 * So the frame is built from the protocol's own primitives instead of casting
 * past its types. What that still buys:
 *
 *   • the TYPE NAME is compile-checked against the contract's own
 *     `ORB_COMMANDS` tuple, so a typo cannot ship;
 *   • the version and id generator are the protocol's, not a second copy;
 *   • the result is run through the protocol's own `isEnvelope` before it can
 *     be sent, so a malformed frame fails here rather than at the daemon.
 *
 * Only the payload is unchecked, which is exactly the part the contract has not
 * specified yet. When the diff lands, this collapses back to `makeEnvelope`.
 */
function reservedOrbEnvelope(
  type: (typeof ORB_COMMANDS)[number],
  payload: Record<string, unknown>,
): Envelope | null {
  const frame = {
    v: PROTOCOL_VERSION,
    id: ulid(),
    ts: new Date().toISOString().replace(/(\.\d{3})\d*Z$/, '$1Z'),
    type,
    corr: null,
    payload,
  };
  return isEnvelope(frame) ? frame : null;
}

/** Short, non-reversible handle for a credential, so a rejected token need not be kept. */
function credentialDigest(port: number, token: string): string {
  return createHash('sha256').update(`${port}:${token}`).digest('hex').slice(0, 16);
}

export interface DaemonConnectionOptions {
  surfaceVersion: string;
  onStatus: (status: ConnectionStatus) => void;
  onHealth: (health: DaemonHealth) => void;
  onAgentState: (state: AgentState) => void;
  onAuditHistory: (entries: AuditEntry[]) => void;
  onAuditAppended: (entry: AuditEntry) => void;
  onPtySessions: (sessions: PtySession[]) => void;
  onTranscriptLine: (line: TranscriptLine) => void;
  /**
   * The daemon ANSWERED a `cmd.voice.pushToTalk`. `active` is its own view of
   * whether the microphone is claimed, which is the only view worth rendering.
   */
  onVoiceAck: (action: 'start' | 'stop', active: boolean) => void;
  /**
   * The daemon REFUSED it, or the socket died before it could answer. The
   * distinction matters: a refusal means the claim did not happen, and the
   * indicator must not light.
   */
  onVoiceRefused: (action: 'start' | 'stop', detail: string) => void;
  log: (message: string) => void;
}

export class DaemonConnection {
  private readonly opts: DaemonConnectionOptions;

  private socket: WebSocket | null = null;
  private timer: NodeJS.Timeout | null = null;
  private handshakeTimer: NodeJS.Timeout | null = null;
  private silenceTimer: NodeJS.Timeout | null = null;

  private status: ConnectionStatus = { phase: 'offline' };
  private backoffMs = BACKOFF_MIN_MS;
  private helloId: string | null = null;
  private subscribeId: string | null = null;
  private auditQueryId: string | null = null;
  private stopped = false;
  private loggedFirstHealth = false;

  /**
   * Per-connection, and reset on disconnect. A half-streamed message cannot be
   * completed by the next connection, and holding its fragments would let stale
   * text prepend itself to a future message that reuses the id.
   */
  private readonly assembler = new TranscriptAssembler();

  /** Digest of the (port, token) that was rejected with 4401. Never retried. */
  private rejectedCredential: string | null = null;

  /**
   * In-flight `cmd.voice.pushToTalk` frames, by correlation id.
   *
   * Kept so a reply can be attributed to the action that caused it. Without
   * this, a `stop` acknowledged after a `start` was refused would be read as
   * confirmation of the start. Cleared on disconnect, where every outstanding
   * one is reported as refused — a command with no answer is not a command that
   * succeeded, and for this particular command the safe reading is "the
   * microphone is not claimed".
   */
  private readonly pendingVoice = new Map<string, 'start' | 'stop'>();

  constructor(options: DaemonConnectionOptions) {
    this.opts = options;
  }

  /* ───────────────────────────────────────────────────────── public surface */

  get current(): ConnectionStatus {
    return this.status;
  }

  start(): void {
    this.stopped = false;
    this.attempt();
  }

  /**
   * Owner pressed RETRY. Clears the 4401 block so the same credential may be
   * tried once more — a deliberate, human-initiated decision, which is exactly
   * the kind of action the failure budget exists to permit.
   */
  retryNow(): void {
    this.rejectedCredential = null;
    this.backoffMs = BACKOFF_MIN_MS;
    this.clearTimer();
    this.attempt();
  }

  /**
   * CONTRACT §5.3 `cmd.voice.pushToTalk` — Orb-only, payload
   * `{ action: "start" | "stop" }`.
   *
   * Fire-and-correlate rather than fire-and-forget. The daemon's reply carries
   * `active`, and that is what the indicator renders; this method never asserts
   * anything about the microphone by itself.
   *
   * Returns false when there is no open socket, so the caller can say "not
   * connected" instead of showing a claim that was never sent.
   */
  sendPushToTalk(action: 'start' | 'stop'): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      this.opts.onVoiceRefused(action, 'not connected to the daemon');
      return false;
    }
    const frame = reservedOrbEnvelope('cmd.voice.pushToTalk', { action });
    if (!frame) {
      // Unreachable unless the protocol's own validator rejects a frame this
      // file built from the protocol's own primitives — which would mean the
      // two have drifted. Refuse rather than send something unvalidated.
      this.opts.onVoiceRefused(action, 'could not build a valid envelope');
      return false;
    }
    this.pendingVoice.set(frame.id, action);
    this.socket.send(JSON.stringify(frame));
    return true;
  }

  dispose(): void {
    this.stopped = true;
    this.clearTimer();
    this.clearHandshakeTimer();
    this.clearSilenceTimer();
    if (this.socket) {
      this.socket.removeAllListeners();
      this.socket.close(1001, 'orb closing');
      this.socket = null;
    }
  }

  /* ──────────────────────────────────────────────────────────── the attempt */

  private attempt(): void {
    if (this.stopped || this.socket) return;

    const result = readRuntimeFile();

    if (!result.ok) {
      if (result.reason === 'versionMismatch') {
        // Terminal for this pairing: a retry cannot reconcile two different
        // protocol versions (CONTRACT §7.3). We keep polling the FILE though —
        // it costs one stat, opens no socket, and means a daemon replacement is
        // noticed without the owner restarting the Orb.
        this.emit({
          phase: 'protocolMismatch',
          detail: result.detail,
          ...(result.daemonProtocolVersion !== undefined
            ? { daemonProtocolVersion: result.daemonProtocolVersion }
            : {}),
        });
      } else {
        this.emit({ phase: 'offline', detail: result.detail });
      }
      this.schedule(FILE_POLL_MS);
      return;
    }

    const info = result.info;
    const digest = credentialDigest(info.port, info.token);

    if (this.rejectedCredential === digest) {
      // Same credential the daemon already refused. Sending it again would burn
      // another of the five failures for a guaranteed-identical outcome.
      this.emit({
        phase: 'authRejected',
        detail: 'token refused by the daemon — waiting for a daemon restart',
      });
      this.schedule(FILE_POLL_MS);
      return;
    }

    // A different credential means a fresh daemon launch; the old block is moot.
    this.rejectedCredential = null;
    this.open(info);
  }

  private open(info: RuntimeInfo): void {
    this.emit({ phase: 'connecting', detail: `port ${info.port}` });

    const socket = new WebSocket(`ws://127.0.0.1:${info.port}/v1`, {
      origin: ORIGIN,
      handshakeTimeout: HANDSHAKE_DEADLINE_MS,
      maxPayload: MAX_FRAME_BYTES,
      perMessageDeflate: false,
    });
    this.socket = socket;

    socket.on('open', () => this.sendHello(info));
    socket.on('message', (data) => this.onMessage(data));
    socket.on('ping', () => this.armSilenceTimer());
    socket.on('unexpected-response', (_req, res) => this.onUnexpectedResponse(info, res));
    socket.on('error', (err) => this.opts.log(`socket error: ${scrub(err.message)}`));
    socket.on('close', (code, reason) => this.onClose(info, code, reason.toString()));
  }

  private sendHello(info: RuntimeInfo): void {
    const frame = makeEnvelope('cmd.hello', {
      token: info.token,
      surface: SURFACE,
      surfaceVersion: this.opts.surfaceVersion,
      protocolVersion: PROTOCOL_VERSION,
    });
    this.helloId = frame.id;
    this.socket?.send(JSON.stringify(frame));

    // The daemon closes us at 3 s (CONTRACT §2.1). Mirror it client-side so a
    // daemon that accepts the socket and then never answers does not leave the
    // Orb showing CONNECTING forever.
    this.clearHandshakeTimer();
    this.handshakeTimer = setTimeout(() => {
      this.opts.log('no res.hello within the handshake deadline');
      this.socket?.close(1000, 'client handshake deadline');
    }, HANDSHAKE_DEADLINE_MS);

    this.armSilenceTimer();
  }

  /* ─────────────────────────────────────────────────────────────── inbound */

  private onMessage(data: RawData): void {
    this.armSilenceTimer();

    let parsed: unknown;
    try {
      parsed = JSON.parse(data.toString());
    } catch {
      // CONTRACT §3.2 — never an error, never a disconnect.
      this.opts.log('ignored a frame that was not valid JSON');
      return;
    }

    if (!isEnvelope(parsed)) {
      this.opts.log('ignored a frame that failed envelope validation');
      return;
    }

    /**
     * ARRIVAL, logged before anything can reject it.
     *
     * A transcript run produced no rendered lines AND no mismatch warning, and
     * those two are very different facts — "arrived and was refused" versus
     * "never arrived" — that looked identical from the outside. The rejection
     * path was built to be loud precisely so that could not happen, and it
     * still could, because silence upstream of the rejection is also silence.
     *
     * So the frame is recorded the moment it parses, before the shape check,
     * before the reassembler, before IPC. Whatever happens after this, the
     * question "did the daemon send one" now has an answer in the log.
     */
    if (parsed.type.startsWith('evt.transcript.')) {
      const p = (parsed.payload ?? {}) as Record<string, unknown>;
      const msg = p['message'] as Record<string, unknown> | undefined;
      // Identity and shape only. The TEXT is never logged: it is whatever the
      // owner said out loud, and a debug line is not a place to put that.
      this.opts.log(
        `TRANSCRIPT-IN ${parsed.type} keys=[${Object.keys(p).join(',')}] ` +
          `messageId=${String(msg?.['messageId'] ?? '(none)')} ` +
          `role=${String(msg?.['role'] ?? '(none)')} ` +
          `chars=${typeof msg?.['text'] === 'string' ? (msg['text'] as string).length : 0}`,
      );
    }

    if (parsed.type === 'res.hello' && parsed.corr === this.helloId) {
      this.onHelloAccepted(parsed.payload as unknown as ResHello);
      return;
    }

    if (parsed.type === 'res.subscribe' && parsed.corr === this.subscribeId) {
      const topics = (parsed.payload as { topics?: unknown }).topics;
      this.opts.log(`subscribed: ${Array.isArray(topics) ? topics.join(', ') : '(none echoed)'}`);
      return;
    }

    // Push-to-talk replies, matched by correlation id. Both outcomes are
    // handled here rather than letting a refusal fall through to the silent
    // ignore at the bottom: an unanswered microphone claim must not look like
    // a successful one.
    if (parsed.corr && this.pendingVoice.has(parsed.corr)) {
      const action = this.pendingVoice.get(parsed.corr) as 'start' | 'stop';
      this.pendingVoice.delete(parsed.corr);

      if (parsed.type === 'res.ok') {
        const payload = parsed.payload as { active?: unknown; changed?: unknown };
        // `active` is the daemon's own view. Absent means an older daemon
        // answered a bare res.ok, and inferring the claim from the action we
        // sent would be exactly the local optimism this avoids — so treat a
        // reply with no `active` as unconfirmed.
        if (typeof payload.active === 'boolean') {
          this.opts.log(
            `voice.pushToTalk ${action} → mic ${payload.active ? 'CLAIMED' : 'released'}` +
              `${payload.changed === false ? ' (no change)' : ''}`,
          );
          this.opts.onVoiceAck(action, payload.active);
        } else {
          this.opts.onVoiceRefused(action, 'daemon replied without an active flag');
        }
      } else {
        const payload = parsed.payload as { message?: unknown };
        const detail = typeof payload.message === 'string' ? payload.message : parsed.type;
        this.opts.log(`voice.pushToTalk ${action} REFUSED: ${scrub(detail)}`);
        this.opts.onVoiceRefused(action, detail);
      }
      return;
    }

    if (parsed.type === 'evt.daemon.health') {
      // Log the first frame verbatim. Session 1 is filling these fields with
      // real values, so what actually arrives will change under us; a literal
      // record of one frame is worth more than an assumption about its shape.
      if (!this.loggedFirstHealth) {
        this.loggedFirstHealth = true;
        this.opts.log(`first evt.daemon.health verbatim: ${JSON.stringify(parsed)}`);
      }
      const health = parsed.payload as unknown as EvtDaemonHealth;
      if (typeof health.uptimeS === 'number') {
        // Each field defaulted independently rather than gated on one another:
        // the daemon is filling these in progressively, so a frame carrying
        // uptime but not yet budget is a real intermediate state, not a fault.
        const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
        this.opts.onHealth({
          uptimeS: health.uptimeS,
          cpuPct: num(health.cpuPct),
          memMB: num(health.memMB),
          apiReachable: health.apiReachable === true,
          budgetSpent: num(health.budgetSpent),
          budgetCap: num(health.budgetCap),
          receivedAt: Date.now(),
        });
      }
      return;
    }

    if (parsed.type === 'res.audit' && parsed.corr === this.auditQueryId) {
      const rows = (parsed.payload as { entries?: unknown }).entries;
      if (Array.isArray(rows)) {
        const entries = rows
          .filter((r): r is Record<string, unknown> => typeof r === 'object' && r !== null)
          .map((r) => this.toAuditEntry(r))
          .filter((e): e is AuditEntry => e !== null);
        this.opts.log(`audit history: ${entries.length} entries`);
        this.opts.onAuditHistory(entries);
      }
      return;
    }

    if (parsed.type === 'evt.audit.appended') {
      const entry = this.toAuditEntry(parsed.payload as Record<string, unknown>);
      if (entry) this.opts.onAuditAppended(entry);
      return;
    }

    if (parsed.type === 'evt.pty.sessions') {
      const list = (parsed.payload as { sessions?: unknown }).sessions;
      if (Array.isArray(list)) {
        this.opts.onPtySessions(list as PtySession[]);
      }
      return;
    }

    // Streaming text. Reassembled by seq per CONTRACT §3.3 — see
    // transcript-assembler.ts. Emits only when `done` closes the message, so
    // consumers never see a half-sentence or an out-of-order fragment.
    if (parsed.type === 'evt.transcript.delta') {
      const d = parsed.payload as unknown as TranscriptDelta;
      const finished = this.assembler.push(d);
      if (finished) {
        if (finished.gaps.length > 0) {
          this.opts.log(
            `transcript ${finished.messageId} completed with ${finished.gaps.length} missing ` +
              `fragment(s) at seq ${finished.gaps.join(',')} — emitting what arrived`,
          );
        }
        this.opts.onTranscriptLine({
          messageId: finished.messageId,
          role: finished.role,
          provenance: ROLE_PROVENANCE[finished.role] ?? 'system',
          text: finished.text,
          ts: parsed.ts,
        });
      }
      return;
    }

    if (parsed.type === 'evt.transcript.message') {
      const payload = parsed.payload as { message?: Record<string, unknown> };
      const m = payload.message;
      if (!m || typeof m['text'] !== 'string') {
        // Say so. CONTRACT §4.1 specifies this payload as
        // `{ companionId, message: { messageId, role, text, toolCalls?, ts } }`
        // and the daemon currently sends `{ role, text, final }` flat, with no
        // `message` wrapper and no messageId or ts.
        //
        // Not accepted anyway, and the silence was the real defect: dropping it
        // without a word produced an empty TRACE and an empty under-sphere line
        // with nothing anywhere to say why, which is indistinguishable from
        // "the daemon said nothing". Rendering the flat form instead would be
        // worse — it would hide a contract mismatch AND invent the messageId
        // the reassembler and the React key both need.
        this.opts.log(
          `!! evt.transcript.message does not match CONTRACT §4.1 — expected a ` +
            `nested \`message\` object, got keys [${Object.keys(
              parsed.payload as Record<string, unknown>,
            ).join(', ')}]. Dropping it.`,
        );
        return;
      }
      {
        const role = typeof m['role'] === 'string' ? m['role'] : 'system';
        this.opts.onTranscriptLine({
          messageId: String(m['messageId'] ?? ''),
          role,
          provenance: ROLE_PROVENANCE[role] ?? 'system',
          text: m['text'],
          ts: typeof m['ts'] === 'string' ? m['ts'] : new Date().toISOString(),
        });
      }
      return;
    }

    if (parsed.type === 'evt.agent.state') {
      const evt = parsed.payload as unknown as EvtAgentState;

      /**
       * The missing field this handler did NOT catch.
       *
       * `EvtAgentState` is `{ companionId, state, detail? }`, and for four
       * emit sites the daemon sent no `companionId` at all. This code never
       * noticed, because `as unknown as EvtAgentState` asserts the shape
       * instead of checking it and the runtime guard only ever looked at
       * `state`. The sphere rendered correctly off a malformed frame for days.
       *
       * §3.2 requires tolerating unknown FIELDS — a field the contract does not
       * define. It says nothing about a required field being absent, and
       * treating the two the same is how a wrong shape survives. Reported, and
       * deliberately NOT fatal: `companionId` is not something the sphere uses,
       * and blanking the state over a field this surface does not read would
       * turn someone else's schema slip into a dead sphere.
       */
      if (typeof evt.companionId !== 'string' || evt.companionId.length === 0) {
        this.opts.log(
          `!! evt.agent.state is missing the required \`companionId\` (CONTRACT §4.1) — ` +
            `keys [${Object.keys(parsed.payload as Record<string, unknown>).join(', ')}]. ` +
            `Rendering the state anyway; the sphere does not read companionId.`,
        );
      }

      // Validated against the closed set rather than trusted. The daemon is the
      // authority, but a value outside AgentState would mean the two sides have
      // drifted (CONTRACT §7.4) — dropping it keeps the sphere on a state it
      // can actually render instead of blanking.
      if (typeof evt.state === 'string' && (AGENT_STATES as readonly string[]).includes(evt.state)) {
        this.opts.onAgentState(evt.state);
      } else {
        this.opts.log(`ignored evt.agent.state with unknown state '${String(evt.state)}'`);
      }
      return;
    }

    if (this.status.phase === 'connecting' && parsed.type.startsWith('err.')) {
      const payload = parsed.payload as { message?: unknown };
      const detail = typeof payload.message === 'string' ? payload.message : parsed.type;
      this.opts.log(`handshake refused: ${scrub(detail)}`);
      this.socket?.close(1000, 'handshake refused');
      return;
    }

    // CONTRACT §3.2 — an unknown type, or an unknown field inside a known
    // payload, MUST be ignored silently. This is what lets the Console ship a
    // new event without breaking the Orb. Phase 1 subscribes to nothing, so
    // everything below is expected to be quiet.
    this.opts.log(`ignored ${parsed.type}`);
  }

  private onHelloAccepted(payload: ResHello): void {
    this.clearHandshakeTimer();
    this.backoffMs = BACKOFF_MIN_MS;
    this.rejectedCredential = null;

    this.emit({
      phase: 'connected',
      daemonVersion: payload.daemonVersion,
      sessionId: payload.sessionId,
      daemonProtocolVersion: payload.protocolVersion,
    });
    this.opts.log(
      `connected — daemon ${payload.daemonVersion}, session ${payload.sessionId.slice(0, 8)}`,
    );

    // Subscribe only after the handshake is accepted. A cmd.* before a
    // successful cmd.hello earns err.auth.required (CONTRACT §5.4), and the
    // subscription has to be re-sent on every reconnect because the daemon
    // holds it in per-connection state, not per-surface.
    const frame = makeEnvelope('cmd.subscribe', { topics: [...TOPICS] });
    this.subscribeId = frame.id;
    this.socket?.send(JSON.stringify(frame));

    // Subscription only delivers what happens NEXT. SENTINEL needs the log that
    // already exists, so ask for it once per connection.
    const query = makeEnvelope('cmd.audit.query', { limit: AUDIT_HISTORY_LIMIT });
    this.auditQueryId = query.id;
    this.socket?.send(JSON.stringify(query));
  }

  /**
   * Normalise one audit row.
   *
   * `res.audit` returns raw log rows keyed by `seq`; `evt.audit.appended` is
   * specified with `entryId`. Accept either rather than assuming, because one
   * of the two is going to change and a silently-empty SENTINEL is worse than
   * a loud mismatch.
   */
  private toAuditEntry(raw: Record<string, unknown>): AuditEntry | null {
    const id = raw['entryId'] ?? raw['seq'] ?? raw['id'];
    if (id === undefined || id === null) return null;
    return {
      id: String(id),
      ts: typeof raw['ts'] === 'string' ? raw['ts'] : '',
      actor: typeof raw['actor'] === 'string' ? raw['actor'] : 'unknown',
      tool: typeof raw['tool'] === 'string' ? raw['tool'] : '',
      tier: typeof raw['tier'] === 'string' ? raw['tier'] : 'none',
      summary: typeof raw['summary'] === 'string' ? raw['summary'] : '',
      provenance: typeof raw['provenance'] === 'string' ? raw['provenance'] : null,
    };
  }

  /**
   * The upgrade was refused at the HTTP layer, before any WebSocket existed.
   * core/server.py::process_request does this for three cases, and they mean
   * very different things.
   */
  private onUnexpectedResponse(info: RuntimeInfo, res: IncomingMessage): void {
    const code = res.statusCode ?? 0;
    this.socket?.removeAllListeners();
    this.socket = null;

    if (code === 403) {
      // Origin rejected. We send the allowlisted value, so this is our bug, not
      // a policy decision — retrying identically would only spam the audit log.
      this.rejectedCredential = credentialDigest(info.port, info.token);
      this.emit({
        phase: 'authRejected',
        detail: 'daemon rejected Origin: zoey://orb — this is a bug, not a lockout',
      });
      this.opts.log('!! upgrade refused on Origin — check ALLOWED_ORIGINS in core/server.py');
    } else if (code === 429) {
      // The daemon has disabled its own listener after repeated auth failures.
      // Nothing the Orb can do; it needs a restart. Say so plainly rather than
      // reporting a generic auth failure and sending the owner hunting.
      this.rejectedCredential = credentialDigest(info.port, info.token);
      this.emit({
        phase: 'authRejected',
        detail: 'daemon listener is disabled after repeated auth failures — restart the daemon',
      });
      this.opts.log('!! daemon listener disabled (HTTP 429) — restart core/server.py');
    } else {
      this.emit({ phase: 'reconnecting', detail: `daemon refused the upgrade (HTTP ${code})` });
    }

    this.scheduleBackoff();
  }

  private onClose(info: RuntimeInfo, code: number, reason: string): void {
    this.clearHandshakeTimer();
    this.clearSilenceTimer();
    this.socket?.removeAllListeners();
    this.socket = null;
    this.helloId = null;
    this.subscribeId = null;
    this.auditQueryId = null;
    this.assembler.reset();

    // Every unanswered voice command fails closed. A `start` whose reply was
    // lost with the socket did not claim anything the surface can vouch for,
    // and a `stop` that never arrived means the daemon still thinks the mic is
    // open — in both cases the honest local state is "not claimed", and the
    // controller re-sends a stop when the link returns.
    for (const [, action] of this.pendingVoice) {
      this.opts.onVoiceRefused(action, `connection closed (${code}) before the daemon answered`);
    }
    this.pendingVoice.clear();

    if (this.stopped) return;

    switch (code) {
      case CLOSE_CODES.Unauthorized: {
        // 4401. Record the credential so it is never sent again, then fall back
        // to polling. A daemon restart rotates the token, which changes the
        // digest, which unblocks us automatically.
        this.rejectedCredential = credentialDigest(info.port, info.token);
        this.emit({
          phase: 'authRejected',
          detail: 'daemon refused the token — waiting for a daemon restart',
        });
        this.opts.log('auth rejected (4401) — this credential will not be retried');
        this.schedule(FILE_POLL_MS);
        return;
      }

      case CLOSE_CODES.ProtocolMismatch: {
        this.emit({
          phase: 'protocolMismatch',
          detail: `daemon rejected protocol version ${PROTOCOL_VERSION}`,
        });
        this.opts.log('protocol mismatch (4409) — both surfaces must update together');
        this.schedule(FILE_POLL_MS);
        return;
      }

      case CLOSE_CODES.RateLimited: {
        this.rejectedCredential = credentialDigest(info.port, info.token);
        this.emit({ phase: 'authRejected', detail: 'daemon rate-limited this connection' });
        this.schedule(FILE_POLL_MS);
        return;
      }

      default: {
        // 4408, 1000, 1001, 1006, transport errors. All retryable — the daemon
        // restarting mid-session is the common case.
        const detail = reason ? scrub(reason) : `connection closed (${code})`;
        this.emit({ phase: 'reconnecting', detail, retryInMs: this.backoffMs });
        this.scheduleBackoff();
      }
    }
  }

  /* ─────────────────────────────────────────────────────────────── plumbing */

  private armSilenceTimer(): void {
    this.clearSilenceTimer();
    this.silenceTimer = setTimeout(() => {
      this.opts.log('no traffic from the daemon in 60 s — treating the link as dead');
      this.socket?.terminate();
    }, SILENCE_TIMEOUT_MS);
  }

  private scheduleBackoff(): void {
    const jitter = 0.75 + Math.random() * 0.5;
    const delay = Math.round(this.backoffMs * jitter);
    this.backoffMs = Math.min(this.backoffMs * 2, BACKOFF_MAX_MS);
    this.schedule(delay);
  }

  private schedule(delayMs: number): void {
    this.clearTimer();
    if (this.stopped) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.attempt();
    }, delayMs);
  }

  private clearTimer(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private clearHandshakeTimer(): void {
    if (this.handshakeTimer) {
      clearTimeout(this.handshakeTimer);
      this.handshakeTimer = null;
    }
  }

  private clearSilenceTimer(): void {
    if (this.silenceTimer) {
      clearTimeout(this.silenceTimer);
      this.silenceTimer = null;
    }
  }

  private emit(status: ConnectionStatus): void {
    this.status = status;
    this.opts.onStatus(status);
  }
}
