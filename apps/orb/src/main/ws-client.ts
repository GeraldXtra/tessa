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
  CLOSE_CODES,
  HANDSHAKE_DEADLINE_MS,
  MAX_FRAME_BYTES,
  PROTOCOL_VERSION,
  isEnvelope,
  makeEnvelope,
  type AllowedOrigin,
  type ResHello,
  type Surface,
} from '@zoey/protocol';

import type { ConnectionStatus } from '../shared/ipc-contract.ts';
import { readRuntimeFile, type RuntimeInfo } from './runtime-file.ts';

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

/** Short, non-reversible handle for a credential, so a rejected token need not be kept. */
function credentialDigest(port: number, token: string): string {
  return createHash('sha256').update(`${port}:${token}`).digest('hex').slice(0, 16);
}

export interface DaemonConnectionOptions {
  surfaceVersion: string;
  onStatus: (status: ConnectionStatus) => void;
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
  private stopped = false;

  /** Digest of the (port, token) that was rejected with 4401. Never retried. */
  private rejectedCredential: string | null = null;

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

    if (parsed.type === 'res.hello' && parsed.corr === this.helloId) {
      this.onHelloAccepted(parsed.payload as unknown as ResHello);
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
