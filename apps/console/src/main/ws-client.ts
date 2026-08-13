/**
 * apps/console/src/main/ws-client.ts — the Console's ONE socket to the daemon.
 *
 * CONTRACT §2.3: the WebSocket client must not live in a browser/renderer
 * context. A renderer cannot set an arbitrary `Origin` — which is the control
 * that actually stops a hostile webpage from driving the agent — and holding
 * the token here keeps it out of reach of anything rendered.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THE LOCKOUT HAZARD — reused wholesale from apps/orb/src/main/ws-client.ts
 *
 * `core/server.py` disables its listener after FIVE failed auth attempts in
 * 60 s, and only a daemon restart re-enables it. A conventional reconnect loop
 * against a stale token would lock the owner out of his own daemon in about
 * three seconds, from his own app, with no attacker involved.
 *
 * THIS SPRINT IT IS WORSE THAN THAT: the daemon is SHARED with a live Orb. A
 * Console that burned the failure budget would take the Orb's connection down
 * with it. Session 2 solved this first; re-deriving it would only be a chance
 * to get it wrong.
 *
 * The invariant, unchanged from theirs:
 *
 *     cmd.hello is never sent twice with the same (port, token) pair
 *     once that pair has been rejected with 4401.
 *
 * So the budget is spent at most ONCE per distinct token. The token rotates on
 * every daemon launch, so a restart clears the block automatically.
 */

import { createHash } from 'node:crypto'
import type { IncomingMessage } from 'node:http'

import { WebSocket, type RawData } from 'ws'

import {
  CLOSE_CODES,
  HANDSHAKE_DEADLINE_MS,
  MAX_FRAME_BYTES,
  PROTOCOL_VERSION,
  isEnvelope,
  makeEnvelope,
  type AllowedOrigin,
  type Envelope,
  type ResHello,
  type Surface,
} from '@zoey/protocol'

import { readRuntimeFile, type RuntimeInfo } from './token.ts'

/**
 * Topics the Console actually needs.
 *
 * Verified against the daemon's matcher rather than assumed: `topic_matches`
 * in core/server.py strips the first segment of the type before comparing, so
 * `pty.*` matches `evt.pty.revoke` and `daemon.*` matches `evt.daemon.health`.
 *
 *   pty.*        — evt.pty.revoke (must be honoured) and evt.pty.sessions
 *   permission.* — resolution of a pending spawn request
 *   daemon.*     — health, and shutdown so the UI can show a reconnect state
 *
 * Deliberately NOT subscribed: transcript, job, companion. The Console has
 * nowhere to render them in Phase 1, and decoding JSON only to drop it costs
 * frames on a 2-core machine.
 */
const TOPICS = ['pty.*', 'permission.*', 'daemon.*'] as const

const ORIGIN: AllowedOrigin = 'zoey://console'
const SURFACE: Surface = 'console'

/** How often to re-read runtime.json while there is no daemon. Opens no socket. */
const FILE_POLL_MS = 2_000
const BACKOFF_MIN_MS = 500
const BACKOFF_MAX_MS = 8_000
/** The daemon pings every 20 s; three missed intervals means a dead link. */
const SILENCE_TIMEOUT_MS = 60_000
/** A request/response round trip. Generous — the daemon is local. */
const REQUEST_TIMEOUT_MS = 5_000

/**
 * Scrub anything token-shaped before it reaches a log. The token is never
 * deliberately logged, so this should never fire — it exists because "should
 * never" and "cannot" are different words, and a token logged once is leaked.
 */
export function scrub(text: string): string {
  return text.replace(/\b[0-9a-f]{64}\b/gi, '<redacted-token>')
}

function credentialDigest(port: number, token: string): string {
  return createHash('sha256').update(`${port}:${token}`).digest('hex').slice(0, 16)
}

export type ConnPhase =
  | 'offline'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'authRejected'
  | 'protocolMismatch'

export interface ConnStatus {
  phase: ConnPhase
  detail?: string
  daemonVersion?: string
  sessionId?: string
}

export interface DaemonClientOptions {
  surfaceVersion: string
  log: (message: string) => void
  onStatus?: (status: ConnStatus) => void
  /** Daemon ordered a session killed. The Console MUST comply and report back. */
  onRevoke?: (sessionId: string, reason: string) => void
  onHealth?: (payload: Record<string, unknown>) => void
}

/** Resolved response to a cmd.* — either the res.* or the err.* payload. */
export interface Reply {
  ok: boolean
  type: string
  payload: Record<string, unknown>
}

export class DaemonClient {
  private readonly opts: DaemonClientOptions
  private socket: WebSocket | null = null
  private timer: NodeJS.Timeout | null = null
  private handshakeTimer: NodeJS.Timeout | null = null
  private silenceTimer: NodeJS.Timeout | null = null

  private status: ConnStatus = { phase: 'offline' }
  private backoffMs = BACKOFF_MIN_MS
  private helloId: string | null = null
  private stopped = false
  private rejectedCredential: string | null = null

  /** In-flight cmd.* awaiting res./err. by correlation id. */
  private pending = new Map<string, { resolve: (r: Reply) => void; timer: NodeJS.Timeout }>()

  constructor(options: DaemonClientOptions) {
    this.opts = options
  }

  get current(): ConnStatus {
    return this.status
  }
  get isConnected(): boolean {
    return this.status.phase === 'connected' && this.socket?.readyState === WebSocket.OPEN
  }

  start(): void {
    this.stopped = false
    this.attempt()
  }

  dispose(): void {
    this.stopped = true
    this.clearTimer()
    this.clearHandshakeTimer()
    this.clearSilenceTimer()
    for (const [, p] of this.pending) clearTimeout(p.timer)
    this.pending.clear()
    if (this.socket) {
      this.socket.removeAllListeners()
      this.socket.close(1001, 'console closing')
      this.socket = null
    }
  }

  /**
   * Send a cmd.* and await its res./err.
   *
   * Rejects rather than hanging if the daemon is not connected — every caller
   * (notably the grant gate) must be able to FAIL, not wait indefinitely.
   */
  request(type: Parameters<typeof makeEnvelope>[0], payload: unknown): Promise<Reply> {
    return new Promise((resolve, reject) => {
      if (!this.isConnected || !this.socket) {
        reject(new Error('daemon not connected'))
        return
      }
      const frame = makeEnvelope(type, payload as never)
      // Lifecycle reports are the ONLY thing keeping the daemon's audit trail
      // true (CONTRACT §4.2 — it never sees terminal bytes), so the exact frame
      // that produced an audit entry is logged verbatim and can be diffed
      // against it. Low frequency, and it carries no secret: the token lives in
      // cmd.hello alone, which is logged as a digest and never as a frame.
      if (type === 'cmd.pty.report') this.opts.log(`-> ${JSON.stringify(frame)}`)
      const timer = setTimeout(() => {
        this.pending.delete(frame.id)
        reject(new Error(`no reply to ${type} within ${REQUEST_TIMEOUT_MS} ms`))
      }, REQUEST_TIMEOUT_MS)
      this.pending.set(frame.id, { resolve, timer })
      this.socket.send(JSON.stringify(frame))
    })
  }

  /* ───────────────────────────────────────────────────────────── connection */

  private attempt(): void {
    if (this.stopped || this.socket) return

    // CONTRACT §1: re-read on every attempt. The token rotates per daemon launch.
    const result = readRuntimeFile()
    if (!result.ok) {
      if (result.reason === 'versionMismatch') {
        this.emit({ phase: 'protocolMismatch', detail: result.detail })
      } else {
        this.emit({ phase: 'offline', detail: result.detail })
      }
      this.schedule(FILE_POLL_MS)
      return
    }

    const info = result.info
    const digest = credentialDigest(info.port, info.token)
    if (this.rejectedCredential === digest) {
      // Sending it again would burn another of the five failures for a
      // guaranteed-identical outcome — and take the Orb down with us.
      this.emit({
        phase: 'authRejected',
        detail: 'token refused by the daemon — waiting for a daemon restart',
      })
      this.schedule(FILE_POLL_MS)
      return
    }
    this.rejectedCredential = null
    this.open(info)
  }

  private open(info: RuntimeInfo): void {
    this.emit({ phase: 'connecting', detail: `port ${info.port}` })
    const socket = new WebSocket(`ws://127.0.0.1:${info.port}/v1`, {
      origin: ORIGIN, // CONTRACT §2.1 — a renderer could not set this
      handshakeTimeout: HANDSHAKE_DEADLINE_MS,
      maxPayload: MAX_FRAME_BYTES,
      perMessageDeflate: false,
    })
    this.socket = socket

    socket.on('open', () => this.sendHello(info))
    socket.on('message', (d) => this.onMessage(d))
    socket.on('ping', () => this.armSilence())
    socket.on('unexpected-response', (_q, res) => this.onUnexpectedResponse(info, res))
    socket.on('error', (err) => this.opts.log(`socket error: ${scrub(err.message)}`))
    socket.on('close', (code, reason) => this.onClose(info, code, reason.toString()))
  }

  private sendHello(info: RuntimeInfo): void {
    const frame = makeEnvelope('cmd.hello', {
      token: info.token,
      surface: SURFACE,
      surfaceVersion: this.opts.surfaceVersion,
      protocolVersion: PROTOCOL_VERSION,
    })
    this.helloId = frame.id
    this.socket?.send(JSON.stringify(frame))

    // Mirror the daemon's 3 s deadline client-side, so a daemon that accepts the
    // socket and never answers does not leave the Console showing CONNECTING.
    this.clearHandshakeTimer()
    this.handshakeTimer = setTimeout(() => {
      this.opts.log('no res.hello within the handshake deadline')
      this.socket?.close(1000, 'client handshake deadline')
    }, HANDSHAKE_DEADLINE_MS)
    this.armSilence()
  }

  private onMessage(data: RawData): void {
    this.armSilence()

    let parsed: unknown
    try {
      parsed = JSON.parse(data.toString())
    } catch {
      this.opts.log('ignored a frame that was not valid JSON') // §3.2: never disconnect
      return
    }
    if (!isEnvelope(parsed)) {
      this.opts.log('ignored a frame that failed envelope validation')
      return
    }
    const env = parsed as Envelope

    if (env.type === 'res.hello' && env.corr === this.helloId) {
      this.onHelloAccepted(env.payload as unknown as ResHello)
      return
    }

    // Correlated reply to an in-flight request.
    if (env.corr && this.pending.has(env.corr)) {
      const entry = this.pending.get(env.corr)!
      this.pending.delete(env.corr)
      clearTimeout(entry.timer)
      entry.resolve({
        ok: env.type.startsWith('res.'),
        type: env.type,
        payload: env.payload as Record<string, unknown>,
      })
      return
    }

    if (env.type === 'evt.pty.revoke') {
      const p = env.payload as { sessionId?: unknown; reason?: unknown }
      if (typeof p.sessionId === 'string') {
        this.opts.log(`daemon revoked session ${p.sessionId.slice(0, 8)}: ${String(p.reason ?? '')}`)
        this.opts.onRevoke?.(p.sessionId, String(p.reason ?? 'revoked'))
      }
      return
    }

    if (env.type === 'evt.daemon.health') {
      this.opts.onHealth?.(env.payload as Record<string, unknown>)
      return
    }

    // CONTRACT §3.2 — unknown types are ignored, never an error.
  }

  private onHelloAccepted(payload: ResHello): void {
    this.clearHandshakeTimer()
    this.backoffMs = BACKOFF_MIN_MS
    this.rejectedCredential = null
    this.emit({
      phase: 'connected',
      daemonVersion: payload.daemonVersion,
      sessionId: payload.sessionId,
    })
    this.opts.log(
      `connected — daemon ${payload.daemonVersion}, protocol ${payload.protocolVersion}, ` +
        `session ${payload.sessionId.slice(0, 8)}, capabilities [${payload.capabilities.join(', ')}]`,
    )

    // Subscriptions are per-connection state in the daemon, so they must be
    // re-sent on every reconnect — and only after a successful hello, since a
    // cmd.* before that earns err.auth.required.
    const frame = makeEnvelope('cmd.subscribe', { topics: [...TOPICS] })
    this.socket?.send(JSON.stringify(frame))
  }

  /** The upgrade was refused at the HTTP layer, before any WebSocket existed. */
  private onUnexpectedResponse(info: RuntimeInfo, res: IncomingMessage): void {
    const code = res.statusCode ?? 0
    this.socket?.removeAllListeners()
    this.socket = null

    if (code === 403) {
      // We send the allowlisted Origin, so this is our bug, not policy.
      this.rejectedCredential = credentialDigest(info.port, info.token)
      this.emit({ phase: 'authRejected', detail: 'daemon rejected Origin: zoey://console' })
      this.opts.log('!! upgrade refused on Origin — check ALLOWED_ORIGINS in core/server.py')
    } else if (code === 429) {
      this.rejectedCredential = credentialDigest(info.port, info.token)
      this.emit({
        phase: 'authRejected',
        detail: 'daemon listener disabled after repeated auth failures — restart the daemon',
      })
      this.opts.log('!! daemon listener disabled (HTTP 429) — restart core/server.py')
    } else {
      this.emit({ phase: 'reconnecting', detail: `daemon refused the upgrade (HTTP ${code})` })
    }
    this.scheduleBackoff()
  }

  private onClose(info: RuntimeInfo, code: number, reason: string): void {
    this.clearHandshakeTimer()
    this.clearSilenceTimer()
    this.socket?.removeAllListeners()
    this.socket = null
    this.helloId = null
    if (this.stopped) return

    switch (code) {
      // 4401 — record the credential so it is never retried. A daemon restart
      // rotates the token, changing the digest, which unblocks us automatically.
      case CLOSE_CODES.Unauthorized:
        this.rejectedCredential = credentialDigest(info.port, info.token)
        this.emit({ phase: 'authRejected', detail: 'daemon refused the token' })
        this.opts.log('auth rejected (4401) — this credential will not be retried')
        this.schedule(FILE_POLL_MS)
        return

      // 4408 — we were too slow to say hello. Retryable, and NOT counted by the
      // daemon as an auth failure, so a plain backoff is safe here.
      case CLOSE_CODES.HandshakeTimeout:
        this.emit({ phase: 'reconnecting', detail: 'handshake deadline missed (4408)' })
        this.opts.log('handshake timeout (4408) — retrying')
        this.scheduleBackoff()
        return

      // 4409 — a retry cannot reconcile two protocol versions (CONTRACT §7.3).
      case CLOSE_CODES.ProtocolMismatch:
        this.emit({
          phase: 'protocolMismatch',
          detail: `daemon rejected protocol version ${PROTOCOL_VERSION}`,
        })
        this.opts.log('protocol mismatch (4409) — both surfaces must update together')
        this.schedule(FILE_POLL_MS)
        return

      // 4429 — the daemon is rate-limiting. Backing off fast would make it worse.
      case CLOSE_CODES.RateLimited:
        this.rejectedCredential = credentialDigest(info.port, info.token)
        this.emit({ phase: 'authRejected', detail: 'daemon rate-limited this connection (4429)' })
        this.opts.log('rate limited (4429) — waiting for a daemon restart')
        this.schedule(FILE_POLL_MS)
        return

      default: {
        const detail = reason ? scrub(reason) : `connection closed (${code})`
        this.emit({ phase: 'reconnecting', detail })
        this.scheduleBackoff()
      }
    }
  }

  /* ───────────────────────────────────────────────────────────────── timers */

  private armSilence(): void {
    this.clearSilenceTimer()
    this.silenceTimer = setTimeout(() => {
      this.opts.log('no traffic from the daemon in 60 s — treating the link as dead')
      this.socket?.terminate()
    }, SILENCE_TIMEOUT_MS)
  }

  private scheduleBackoff(): void {
    const jitter = 0.75 + Math.random() * 0.5
    const delay = Math.round(this.backoffMs * jitter)
    this.backoffMs = Math.min(this.backoffMs * 2, BACKOFF_MAX_MS)
    this.schedule(delay)
  }

  private schedule(delayMs: number): void {
    this.clearTimer()
    if (this.stopped) return
    this.timer = setTimeout(() => {
      this.timer = null
      this.attempt()
    }, delayMs)
  }

  private clearTimer(): void {
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
  }
  private clearHandshakeTimer(): void {
    if (this.handshakeTimer) {
      clearTimeout(this.handshakeTimer)
      this.handshakeTimer = null
    }
  }
  private clearSilenceTimer(): void {
    if (this.silenceTimer) {
      clearTimeout(this.silenceTimer)
      this.silenceTimer = null
    }
  }

  private emit(status: ConnStatus): void {
    this.status = status
    this.opts.onStatus?.(status)
  }
}
