/**
 * apps/console/src/main/daemon.ts — DEV-ONLY daemon auto-start.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * ⚠️  THIS IS A DEVELOPMENT STOPGAP. Phase 3 replaces it with a Windows service
 *     (spec §9) and this file is expected to be DELETED.
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * THE HAZARD THAT SHAPES EVERY LINE HERE
 *
 * The daemon is SHARED. Zoey Orb polls for it rather than spawning it, and the
 * Orb is live right now. If the Console owned the daemon's lifecycle, closing
 * the Console would kill the Orb's connection — a cross-surface coupling that
 * neither CONTRACT nor the spec sanctions.
 *
 * So the rule is: **the Console never stops a daemon it did not itself start.**
 *
 * That is enforced structurally, not by intention. `ownedPid` is set in exactly
 * one place — immediately after OUR `spawn()` returns — and `stop()` is a no-op
 * unless it is set. There is no code path that can populate it from
 * runtime.json, from a scan, or from an attach. If we attached to someone
 * else's daemon, `ownedPid` stays null and `stop()` does nothing at all.
 *
 * The token rotates on every daemon launch, so starting one is not free: any
 * surface already connected to a PREVIOUS daemon is dropped. That is why we
 * attach whenever a live daemon exists and only ever spawn when there is none.
 */

import { spawn, type ChildProcess } from 'node:child_process'
import { join } from 'node:path'

import { pidIsAlive, readRuntimeFile } from './token.ts'

/** Longest we wait for a freshly spawned daemon to publish runtime.json. */
const STARTUP_TIMEOUT_MS = 15_000
const POLL_INTERVAL_MS = 250

export type DaemonMode =
  /** A live daemon already existed. We are a guest; we must never stop it. */
  | { kind: 'attached'; pid: number; port: number }
  /** We spawned it. Only in this case may we stop it. */
  | { kind: 'started'; pid: number; port: number }
  | { kind: 'unavailable'; reason: string }

export interface DaemonSupervisorOptions {
  repoRoot: string
  isDev: boolean
  log: (message: string) => void
}

export class DaemonSupervisor {
  private readonly opts: DaemonSupervisorOptions
  private child: ChildProcess | null = null

  /**
   * The PID of a daemon THIS process spawned. Null in every other case.
   *
   * The single gate on `stop()`. Never assigned from runtime.json or a scan —
   * only from our own spawn.
   */
  private ownedPid: number | null = null

  constructor(options: DaemonSupervisorOptions) {
    this.opts = options
  }

  get owns(): boolean {
    return this.ownedPid !== null
  }

  /**
   * Attach to a live daemon, or start one in dev.
   *
   * Never spawns a second daemon: a live runtime.json short-circuits before any
   * spawn, so two Consoles cannot race into two daemons and two tokens.
   */
  async ensure(): Promise<DaemonMode> {
    const existing = readRuntimeFile()
    if (existing.ok) {
      this.opts.log(
        `attached to an existing daemon (pid ${existing.info.pid}, port ${existing.info.port}) — ` +
          `NOT ours, will never be stopped by the Console`,
      )
      return { kind: 'attached', pid: existing.info.pid, port: existing.info.port }
    }

    if (!this.opts.isDev) {
      // Never in a packaged build. Production gets a Windows service.
      return {
        kind: 'unavailable',
        reason: `Zoey Core is not running (${existing.reason}). Start it with: python core/server.py`,
      }
    }

    return this.spawnDaemon(existing.detail)
  }

  private async spawnDaemon(why: string): Promise<DaemonMode> {
    this.opts.log(`no live daemon (${why}) — starting one for development`)

    const child = spawn('python', [join(this.opts.repoRoot, 'core', 'server.py')], {
      cwd: this.opts.repoRoot,
      // Inherit stderr so a Python traceback is visible rather than swallowed;
      // ignore stdin because the daemon never reads it.
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      detached: false,
    })
    this.child = child

    child.stdout?.on('data', (b: Buffer) => {
      for (const line of b.toString().split('\n')) {
        if (line.trim()) this.opts.log(`[core] ${line.trim()}`)
      }
    })
    child.stderr?.on('data', (b: Buffer) => {
      const t = b.toString().trim()
      if (t) this.opts.log(`[core:err] ${t}`)
    })
    child.on('exit', (code) => {
      this.opts.log(`[core] daemon exited (code ${code})`)
      this.ownedPid = null
      this.child = null
    })

    // Wait for the daemon to publish runtime.json rather than assuming a delay.
    const deadline = Date.now() + STARTUP_TIMEOUT_MS
    while (Date.now() < deadline) {
      const r = readRuntimeFile()
      if (r.ok) {
        // THE ONLY assignment of ownedPid in this file.
        this.ownedPid = r.info.pid
        this.opts.log(`started daemon (pid ${r.info.pid}, port ${r.info.port}) — owned by this Console`)
        return { kind: 'started', pid: r.info.pid, port: r.info.port }
      }
      if (child.exitCode !== null) {
        return { kind: 'unavailable', reason: `daemon exited immediately (code ${child.exitCode})` }
      }
      await new Promise((r2) => setTimeout(r2, POLL_INTERVAL_MS))
    }

    return { kind: 'unavailable', reason: `daemon did not publish runtime.json within ${STARTUP_TIMEOUT_MS} ms` }
  }

  /**
   * Stop the daemon — ONLY if this process started it.
   *
   * The `ownedPid === null` guard is the whole point of the file. An attached
   * daemon belongs to the Orb (or to Gerald), and killing it would sever a live
   * surface's connection.
   */
  stop(): void {
    if (this.ownedPid === null) {
      this.opts.log('daemon left running — this Console did not start it')
      return
    }
    const pid = this.ownedPid
    // Re-check liveness: if it already exited, the pid may have been recycled by
    // an unrelated process and killing it would be actively harmful.
    if (!pidIsAlive(pid)) {
      this.opts.log(`daemon pid ${pid} already gone`)
      this.ownedPid = null
      this.child = null
      return
    }
    this.opts.log(`stopping the daemon this Console started (pid ${pid})`)
    try {
      this.child?.kill() // SIGTERM; the daemon removes runtime.json on clean exit
    } catch {
      /* already gone */
    }
    this.ownedPid = null
    this.child = null
  }
}
