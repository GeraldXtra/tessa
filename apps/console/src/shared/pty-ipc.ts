/**
 * apps/console/src/shared/pty-ipc.ts
 *
 * THE HOST BOUNDARY. Shared verbatim by main, the PTY host, and the renderer.
 *
 * This file exists because of a specific risk. On Windows, @lydell/node-pty
 * constructs a `worker_threads` Worker for its conout connection — for every
 * PTY, unconditionally. Electron's utilityProcess has historically thrown on
 * `new Worker()` because of its V8 platform (electron#20550, #18540, #26816);
 * the fix in #20416 covered only ELECTRON_RUN_AS_NODE. VS Code does run its
 * pty host in a utilityProcess, but ships a patched Electron — evidence, not
 * proof, for stock Electron 43.
 *
 * So the host is deliberately SWAPPABLE across three rungs:
 *
 *   1. utilityProcess            — preferred: a crash kills one tab's backend
 *   2. main process              — worker_threads is known to work there
 *   3. child_process.fork with ELECTRON_RUN_AS_NODE=1  — what VS Code used for years
 *
 * Every rung speaks EXACTLY the protocol below over a MessagePort. Because the
 * boundary never changes, dropping down a rung is a configuration change in
 * main, not a rewrite of the terminal. Nothing downstream of this file — not
 * the renderer, not xterm in Step 3 — can tell which rung is in use.
 *
 * Bytes are base64 on this channel. structuredClone could carry a Uint8Array,
 * but base64 keeps the frames printable in logs and identical across all three
 * rungs including the JSON-only child_process case.
 */

/** Renderer → host. */
export type PtyToHost =
  | { t: 'write'; b64: string }
  | { t: 'resize'; cols: number; rows: number }
  | { t: 'kill' }

/** Host → renderer. */
export type PtyFromHost =
  | { t: 'ready'; pid: number }
  | { t: 'data'; b64: string }
  | { t: 'exit'; code: number; signal?: number }
  | { t: 'error'; message: string }

/** Host → main, on the control channel (not the MessagePort). */
export type HostToMain =
  | { t: 'probe'; workerOk: boolean; error?: string; ms: number }
  | { t: 'spawned'; pid: number }
  | { t: 'spawn-failed'; message: string }
  | { t: 'log'; message: string }

/** Main → host, on the control channel. */
export type MainToHost =
  | {
      t: 'spawn'
      shell: string
      args: string[]
      cwd: string
      cols: number
      rows: number
    }
  /**
   * Reap the PTY and exit.
   *
   * Required on Windows: killing the host process does NOT reap the PTY's
   * children — cmd.exe and its conhost.exe survive as orphans. Verified in
   * Step 2, where a force-kill left 2 cmd.exe and 4 conhost.exe behind.
   */
  | { t: 'shutdown' }

/** Which rung is hosting the PTY. Reported so the UI can be honest about it. */
export const PTY_HOST_KINDS = ['utilityProcess', 'main', 'forkedNode'] as const
export type PtyHostKind = (typeof PTY_HOST_KINDS)[number]

/** IPC channel on which main hands the renderer its MessagePort. */
export const PTY_PORT_CHANNEL = 'zoey:pty-port'
