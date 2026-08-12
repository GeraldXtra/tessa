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
  /** main → renderer, push. Status changed. */
  connectionChanged: 'zoey:connection-changed',
  /** renderer → main, send. Owner pressed RETRY after a terminal failure. */
  retryConnection: 'zoey:retry-connection',
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
}

/* ───────────────────────────────────────────────────── the bridge, in types */

export interface ZoeyBridge {
  bootstrap(): Promise<BootstrapInfo>;
  getConnection(): Promise<ConnectionStatus>;
  /** Returns an unsubscribe function. */
  onConnection(listener: (status: ConnectionStatus) => void): () => void;
  retryConnection(): void;
  minimizeWindow(): void;
  closeWindow(): void;
}
