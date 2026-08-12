/**
 * apps/console/src/preload/index.ts
 *
 * The ONLY bridge between the renderer and the main process.
 *
 * This file runs with `sandbox: true`, which means it may require exactly
 * `electron`, `events`, `timers`, and `url` — nothing else. That is a feature,
 * not a limitation: it is structurally impossible for this file to read
 * runtime.json, open a socket, or touch the filesystem, so the auth token
 * cannot leak through it even by mistake.
 *
 * Rule for everything added here: expose FUNCTIONS, never objects the renderer
 * can mutate, and never anything taking a raw path or command that main does
 * not re-validate. The renderer is untrusted.
 */

import { contextBridge, ipcRenderer } from 'electron'
import { PTY_PORT_CHANNEL, type PtyHostKind } from '../shared/pty-ipc.ts'

export interface PtyStartOk {
  ok: true
  kind: PtyHostKind
  workerOk: boolean
  probeMs: number
  probeError?: string
  pid?: number
}
export interface PtyStartErr {
  ok: false
  error: string
}

const api = {
  /** Round-trip liveness check. Proves the bridge works under sandbox:true. */
  ping: (): Promise<string> => ipcRenderer.invoke('zoey:ping'),

  /**
   * Ask main for a terminal.
   *
   * Note what is NOT here: the renderer cannot name a shell, a cwd, or an
   * argv. It passes dimensions and nothing else. Main chooses the shell and —
   * from Step 4 — must obtain a daemon grant first. Letting the renderer supply
   * a command line would make this bridge an unguarded execution channel, which
   * is the exact hole the red-team flagged.
   */
  startPty: (dims: { cols: number; rows: number }): Promise<PtyStartOk | PtyStartErr> =>
    ipcRenderer.invoke('zoey:pty-start', dims),

  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  },
} as const

export type ZoeyApi = typeof api

/**
 * Hand the MessagePort to the renderer's main world.
 *
 * This deliberately does NOT go through contextBridge. A MessagePort cannot
 * cross the isolated-world boundary as a bridged value — it arrives as an inert
 * proxy whose `start` and `postMessage` are not functions, which is precisely
 * what happened the first time this was wired. The supported route is
 * `window.postMessage` with a transfer list, which moves the real port object.
 *
 * Security note: this widens nothing. The port is minted by main and carries
 * only the PTY protocol in src/shared/pty-ipc.ts. The renderer still cannot
 * name a shell, a cwd or an argv — it can only write bytes to a session main
 * already authorised.
 */
ipcRenderer.on(PTY_PORT_CHANNEL, (event) => {
  window.postMessage(PTY_PORT_CHANNEL, '*', event.ports)
})

if (process.contextIsolated) {
  contextBridge.exposeInMainWorld('zoey', api)
} else {
  // Unreachable: contextIsolation is true in webPreferences. Failing loudly
  // beats silently degrading the security model.
  throw new Error('contextIsolation is disabled — refusing to expose the Zoey API')
}
