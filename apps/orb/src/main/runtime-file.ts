/**
 * Discovery of the daemon: %LOCALAPPDATA%\Tessa\runtime.json.
 *
 * CONTRACT §1 and §2.3. Three rules from those sections are implemented here
 * rather than assumed:
 *
 *   1. "The port is discovered, never hard-coded." Nothing in the Orb knows a
 *      port number. If this file cannot be read, the Orb does not connect —
 *      it does not fall back to a guess.
 *   2. "A stale file whose pid is not a live process must be ignored, not
 *      trusted. Surfaces verify liveness before connecting." A dead daemon's
 *      port may already belong to an unrelated process; connecting to it and
 *      sending the token would hand a secret to a stranger.
 *   3. "Surfaces re-read runtime.json on every reconnect and never cache the
 *      token to disk." The token rotates on every daemon launch. Nothing here
 *      memoises; every call is a fresh read.
 *
 * The token never leaves this module except as the `token` field of a
 * RuntimeInfo handed straight to the hello frame. It is never logged, never put
 * in an error message, and never crosses the contextBridge.
 */

import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

import { PROTOCOL_VERSION, RUNTIME_FILE_RELATIVE } from '@tessa/protocol';

/** `secrets.token_hex(32)` — 32 bytes, 64 lowercase hex chars (CONTRACT §2.1). */
const TOKEN_RE = /^[0-9a-f]{64}$/;

export interface RuntimeInfo {
  port: number;
  /** Sensitive. Do not log, do not forward over IPC, do not persist. */
  token: string;
  pid: number;
  startedAt: string;
}

export type RuntimeReadFailure =
  /** No file. The daemon is not running, or shut down cleanly. */
  | 'absent'
  /** Present but unreadable or not JSON. */
  | 'malformed'
  /** Present and well-formed, but its pid is gone. Treat exactly as absent. */
  | 'stale'
  /** The daemon speaks a different protocol version. Connecting cannot succeed. */
  | 'versionMismatch';

export type RuntimeReadResult =
  | { ok: true; info: RuntimeInfo }
  | { ok: false; reason: RuntimeReadFailure; detail: string; daemonProtocolVersion?: number };

/** Mirrors core/security/runtime.py::runtime_path(), including its POSIX fallback. */
export function runtimeFilePath(): string {
  const base =
    process.env['LOCALAPPDATA'] ??
    (process.platform === 'win32'
      ? join(homedir(), 'AppData', 'Local')
      : join(homedir(), '.local', 'share'));
  return join(base, RUNTIME_FILE_RELATIVE);
}

/**
 * CONTRACT §1 liveness check.
 *
 * `process.kill(pid, 0)` sends no signal — on Windows Node treats signal 0 as an
 * existence test. Preferred over shelling out to `tasklist` (which core/ has to
 * do from Python): no child process, no ~30 ms spawn, and this runs on every
 * reconnect attempt.
 *
 * EPERM means the process exists but is not ours to signal — still alive.
 */
function pidIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'EPERM';
  }
}

function isPort(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 && value <= 65535;
}

/**
 * Read, validate, and liveness-check the runtime file.
 *
 * Every failure is a returned value, never a throw: "the daemon is not running"
 * is the Orb's normal resting state, not an exception.
 */
export function readRuntimeFile(): RuntimeReadResult {
  const path = runtimeFilePath();

  let raw: string;
  try {
    raw = readFileSync(path, 'utf8');
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === 'ENOENT') {
      return { ok: false, reason: 'absent', detail: 'daemon not running' };
    }
    // EACCES here would mean the ACL is wrong in a way that locks US out —
    // worth showing, since core/security/runtime.py refuses to start if the ACL
    // did not take.
    return { ok: false, reason: 'malformed', detail: `cannot read runtime file (${code})` };
  }

  let parsed: unknown;
  try {
    // Strip a UTF-8 BOM before parsing. core/security/runtime.py writes with
    // Python's `encoding="utf-8"`, which emits none — but anything else that
    // touches this file might. Notepad and PowerShell's `Set-Content -Encoding
    // utf8` both prepend one, and JSON.parse rejects the result outright. The
    // failure reads as "the daemon wrote garbage" when the file is fine.
    parsed = JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw);
  } catch {
    return { ok: false, reason: 'malformed', detail: 'runtime file is not valid JSON' };
  }

  if (typeof parsed !== 'object' || parsed === null) {
    return { ok: false, reason: 'malformed', detail: 'runtime file is not an object' };
  }
  const data = parsed as Record<string, unknown>;

  const version = data['protocolVersion'];
  if (version !== PROTOCOL_VERSION) {
    return {
      ok: false,
      reason: 'versionMismatch',
      detail: `daemon speaks protocol ${String(version)}, this build speaks ${PROTOCOL_VERSION}`,
      ...(typeof version === 'number' ? { daemonProtocolVersion: version } : {}),
    };
  }

  const port = data['port'];
  if (!isPort(port)) {
    return { ok: false, reason: 'malformed', detail: 'runtime file has no usable port' };
  }

  const token = data['token'];
  // Shape-check the token BEFORE using it. The daemon disables its listener
  // after five failed auth attempts in 60 s (core/server.py), and a truncated
  // or placeholder token would spend one of those five for nothing.
  if (typeof token !== 'string' || !TOKEN_RE.test(token)) {
    return { ok: false, reason: 'malformed', detail: 'runtime file has no usable token' };
  }

  const pid = data['pid'];
  if (typeof pid !== 'number' || !Number.isInteger(pid) || pid <= 0) {
    return { ok: false, reason: 'malformed', detail: 'runtime file has no usable pid' };
  }
  if (!pidIsAlive(pid)) {
    // CONTRACT §1: ignore, do not trust. Reported as 'stale' rather than
    // 'absent' only so the log distinguishes "never started" from "died without
    // cleaning up"; both are treated identically by the caller.
    return { ok: false, reason: 'stale', detail: `daemon pid ${pid} is not running` };
  }

  const startedAt = data['startedAt'];

  return {
    ok: true,
    info: {
      port,
      token,
      pid,
      startedAt: typeof startedAt === 'string' ? startedAt : '',
    },
  };
}
