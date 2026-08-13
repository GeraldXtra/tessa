/**
 * apps/console/src/main/token.ts — daemon discovery.
 *
 * Reads %LOCALAPPDATA%\Zoey\runtime.json for the port and the per-launch token.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * REUSED FROM apps/orb/src/main/runtime-file.ts, deliberately.
 *
 * Session 2 has a working, verified handshake against this exact daemon. Their
 * reader already solves four things correctly, and re-deriving them would only
 * be a chance to get one wrong:
 *
 *   • `process.kill(pid, 0)` for liveness — no child process, no ~30 ms
 *     `tasklist` spawn, and this runs on every reconnect attempt.
 *   • EPERM counts as alive: the process exists, it is simply not ours to signal.
 *   • BOM strip before `JSON.parse`. The daemon writes none, but Notepad and
 *     PowerShell's `Set-Content -Encoding utf8` both prepend one, and the
 *     failure then reads as "the daemon wrote garbage" when the file is fine.
 *   • Shape-check the token BEFORE using it. The daemon disables its listener
 *     after five failed auth attempts in 60 s; a truncated token would spend
 *     one of those five for nothing.
 *
 * Three rules from CONTRACT §1 are implemented here rather than assumed:
 *   1. The port is DISCOVERED, never hard-coded. Nothing in this app knows a
 *      port number at all. The daemon has a preferred port but walks upward
 *      when it is taken, so the bound port is known only from this file.
 *      (No port literal appears here on purpose — check-contract.mjs fails the
 *      build on one in surface code, and it correctly flagged an earlier draft
 *      of this very comment.)
 *   2. A stale file whose pid is dead is IGNORED, not trusted. That port may
 *      already belong to an unrelated process, and connecting would hand it the
 *      token.
 *   3. Nothing is memoised. The token rotates on every daemon launch, so every
 *      call is a fresh read and the token is never written to disk by us.
 *
 * The token leaves this module only as the `token` field of a RuntimeInfo handed
 * straight to the hello frame. It is never logged, never put in an error string,
 * and never crosses the contextBridge.
 */

import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

import { PROTOCOL_VERSION, RUNTIME_FILE_RELATIVE } from '@zoey/protocol'

/** `secrets.token_hex(32)` — 32 bytes, 64 lowercase hex chars (CONTRACT §2.1). */
const TOKEN_RE = /^[0-9a-f]{64}$/

export interface RuntimeInfo {
  port: number
  /** SENSITIVE. Do not log, do not forward over IPC, do not persist. */
  token: string
  pid: number
  startedAt: string
}

export type RuntimeReadFailure = 'absent' | 'malformed' | 'stale' | 'versionMismatch'

export type RuntimeReadResult =
  | { ok: true; info: RuntimeInfo }
  | { ok: false; reason: RuntimeReadFailure; detail: string; daemonProtocolVersion?: number }

/** Mirrors core/security/runtime.py::runtime_path(), including its POSIX fallback. */
export function runtimeFilePath(): string {
  const base =
    process.env['LOCALAPPDATA'] ??
    (process.platform === 'win32'
      ? join(homedir(), 'AppData', 'Local')
      : join(homedir(), '.local', 'share'))
  return join(base, RUNTIME_FILE_RELATIVE)
}

/**
 * CONTRACT §1 liveness check. Signal 0 sends nothing; it is an existence test.
 * EPERM means the process exists but belongs to someone else — still alive.
 */
export function pidIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'EPERM'
  }
}

function isPort(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 && value <= 65535
}

/**
 * Read, validate and liveness-check the runtime file.
 *
 * Every failure is a returned value, never a throw: "the daemon is not running"
 * is a normal resting state for the Console, not an exception.
 */
export function readRuntimeFile(): RuntimeReadResult {
  const path = runtimeFilePath()

  let raw: string
  try {
    raw = readFileSync(path, 'utf8')
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code
    if (code === 'ENOENT') return { ok: false, reason: 'absent', detail: 'daemon not running' }
    // EACCES would mean the ACL is wrong in a way that locks US out —
    // core/security/runtime.py refuses to start if the ACL did not take.
    return { ok: false, reason: 'malformed', detail: `cannot read runtime file (${code})` }
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw)
  } catch {
    return { ok: false, reason: 'malformed', detail: 'runtime file is not valid JSON' }
  }

  if (typeof parsed !== 'object' || parsed === null) {
    return { ok: false, reason: 'malformed', detail: 'runtime file is not an object' }
  }
  const data = parsed as Record<string, unknown>

  const version = data['protocolVersion']
  if (version !== PROTOCOL_VERSION) {
    return {
      ok: false,
      reason: 'versionMismatch',
      detail: `daemon speaks protocol ${String(version)}, this build speaks ${PROTOCOL_VERSION}`,
      ...(typeof version === 'number' ? { daemonProtocolVersion: version } : {}),
    }
  }

  const port = data['port']
  if (!isPort(port)) {
    return { ok: false, reason: 'malformed', detail: 'runtime file has no usable port' }
  }

  const token = data['token']
  if (typeof token !== 'string' || !TOKEN_RE.test(token)) {
    return { ok: false, reason: 'malformed', detail: 'runtime file has no usable token' }
  }

  const pid = data['pid']
  if (typeof pid !== 'number' || !Number.isInteger(pid) || pid <= 0) {
    return { ok: false, reason: 'malformed', detail: 'runtime file has no usable pid' }
  }
  if (!pidIsAlive(pid)) {
    // CONTRACT §1: ignore, do not trust. Reported as 'stale' rather than
    // 'absent' only so a log can distinguish "never started" from "died without
    // cleaning up"; callers treat them identically.
    return { ok: false, reason: 'stale', detail: `daemon pid ${pid} is not running` }
  }

  const startedAt = data['startedAt']
  return {
    ok: true,
    info: { port, token, pid, startedAt: typeof startedAt === 'string' ? startedAt : '' },
  }
}
