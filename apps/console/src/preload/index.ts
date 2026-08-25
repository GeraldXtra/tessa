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

import { contextBridge, ipcRenderer, webUtils } from 'electron'
import { PTY_PORT_CHANNEL, type PtyHostKind } from '../shared/pty-ipc.ts'

export interface PtyStartOk {
  ok: true
  kind: PtyHostKind
  workerOk: boolean
  probeMs: number
  probeError?: string
  pid?: number
  sessionId?: string
  grantId?: string
  grantMs?: number
  expiresAt?: string
  /** Which shell actually started — not necessarily the one asked for. */
  shellId?: string
  shellLabel?: string
  /** True when the requested shell was missing and another was opened. */
  substituted?: boolean
  /** Plain-English reason for the substitution, shown in the terminal. */
  shellMessage?: string
  /** The directory this pane opened in, for the tab title. */
  cwd?: string
}
export interface PtyStartErr {
  ok: false
  error: string
}

/** A shell CHOICE, never a command line. See `startPty` below. */
export interface ShellOption {
  id: string
  label: string
  available: boolean
  how: string
}

const api = {
  /** Round-trip liveness check. Proves the bridge works under sandbox:true. */
  ping: (): Promise<string> => ipcRenderer.invoke('tessa:ping'),

  /**
   * Ask main for a terminal.
   *
   * `shellId` IS A CHOICE FROM A CLOSED SET, NOT A COMMAND LINE, and that
   * distinction is the whole reason it is allowed through this bridge at all.
   * The renderer may say "gitbash"; it may not say
   * `C:\anything\evil.exe --do-harm`. Main validates the id against
   * SHELL_IDS and resolves the executable, the argv and the cwd itself, so this
   * stays a menu selection rather than the unguarded execution channel the
   * red-team flagged. An unknown id falls back to the default rather than
   * reaching a spawn.
   */
  startPty: (
    dims: { cols: number; rows: number },
    shellId?: string,
  ): Promise<PtyStartOk | PtyStartErr> => ipcRenderer.invoke('tessa:pty-start', dims, shellId),

  /**
   * The on-disk path of a file the OS just dropped on the window.
   *
   * `File.path` was REMOVED in Electron 32; `webUtils.getPathForFile` is the
   * supported replacement, and it only exists in the preload — the renderer has
   * no `webUtils`. This widens nothing: it takes a File the renderer was
   * already handed by the drop event and returns a string. The renderer still
   * cannot ask for an arbitrary path, only for the path of a file the user
   * physically dragged onto the terminal.
   */
  pathForFile: (file: File): string => webUtils.getPathForFile(file),

  /**
   * One level of a directory, metadata only.
   *
   * The renderer can name a path but cannot read one: this returns names,
   * kinds, sizes and a reparse flag, and there is no call anywhere that returns
   * file CONTENT. A sandboxed preload could not read the disk even if it tried.
   */
  listDir: (
    dir: string,
  ): Promise<{
    path: string
    entries: { name: string; path: string; dir: boolean; link: boolean; size: number }[]
    total: number
    truncated: boolean
    error?: string
  }> => ipcRenderer.invoke('tessa:fs-list', dir),

  /**
   * Send a typed turn to Tessa.
   *
   * TEXT ONLY. The renderer cannot name a tool, a path or an argument — the
   * daemon decides what runs, exactly as it does for voice. That is what keeps
   * a typed turn inside her permission model instead of beside it.
   */
  agentSend: (text: string): Promise<{ ok: boolean; error?: string; intent?: string; awaitingApproval?: boolean }> =>
    ipcRenderer.invoke('tessa:agent-send', text),

  /** The shared thread: her words and his, typed or spoken. Returns unsubscribe. */
  onTranscript: (
    fn: (m: { messageId: string; role: string; text: string; via: string }) => void,
  ): (() => void) => {
    const h = (_e: unknown, m: { messageId: string; role: string; text: string; via: string }): void => fn(m)
    ipcRenderer.on('tessa:transcript', h)
    return () => ipcRenderer.removeListener('tessa:transcript', h)
  },

  /** idle | listening | thinking | working | speaking. Returns unsubscribe. */
  onAgentState: (fn: (state: string) => void): (() => void) => {
    const h = (_e: unknown, s: string): void => fn(s)
    ipcRenderer.on('tessa:agent-state', h)
    return () => ipcRenderer.removeListener('tessa:agent-state', h)
  },

  /** The Orb's companion theme. The Console never writes it. */
  getTheme: (): Promise<{ theme: string; raw: string; source: string; problem?: string }> =>
    ipcRenderer.invoke('tessa:theme'),

  /** Which shells exist on this machine, for the picker. */
  getShells: (): Promise<{ shells: ShellOption[]; defaultShell: string }> =>
    ipcRenderer.invoke('tessa:shells'),

  /**
   * The keymap and terminal preferences.
   *
   * THE BINDINGS FIRE IN THE RENDERER AND THE FILE IS READ IN MAIN, so this hop
   * has to exist. It is a pull rather than a push so the renderer can re-fetch
   * after he edits the file, without main having to know who is listening.
   */
  getSettings: (): Promise<{
    keymap: Record<string, string>
    rightClickPastes: boolean
    copyOnSelect: boolean
    scrollback: number
    fontSize: number
    path: string
    problems: string[]
  }> => ipcRenderer.invoke('tessa:settings'),

  /** Re-read console-settings.json from disk. Returns the same shape. */
  reloadSettings: (): Promise<{
    keymap: Record<string, string>
    rightClickPastes: boolean
    copyOnSelect: boolean
    scrollback: number
    fontSize: number
    path: string
    problems: string[]
  }> => ipcRenderer.invoke('tessa:settings-reload'),

  /**
   * Menu clicks, forwarded to the terminal. Returns an unsubscribe.
   *
   * The menu items carry no `accelerator` — registering one would put the chord
   * back at the browser level, which is the interception this whole change
   * removes — so a CLICK needs its own route to the same actions.
   */
  onMenu: (cb: (cmd: string) => void): (() => void) => {
    const h = (_e: unknown, cmd: string): void => cb(String(cmd))
    ipcRenderer.on('tessa:menu', h)
    return () => {
      ipcRenderer.removeListener('tessa:menu', h)
    }
  },

  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  },
} as const

export type TessaApi = typeof api

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
ipcRenderer.on(PTY_PORT_CHANNEL, (event, payload: { sessionId?: string } | null) => {
  // The sessionId travels WITH the port so the right pane can claim it. The
  // message shape changed from a bare string to an object for exactly this;
  // the renderer's broker matches on `channel` and then on `sessionId`.
  window.postMessage(
    { channel: PTY_PORT_CHANNEL, sessionId: payload?.sessionId ?? '' },
    '*',
    event.ports,
  )
})

if (process.contextIsolated) {
  contextBridge.exposeInMainWorld('tessa', api)
} else {
  // Unreachable: contextIsolation is true in webPreferences. Failing loudly
  // beats silently degrading the security model.
  throw new Error('contextIsolation is disabled — refusing to expose the Tessa API')
}
