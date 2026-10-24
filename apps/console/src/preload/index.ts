import { contextBridge, ipcRenderer, webUtils } from 'electron'
import { PTY_PORT_CHANNEL } from '../shared/pty-ipc.ts'

export interface ShellInfo {
  id: string
  tag: string
  name: string
  version: string
  key: string
  available: boolean
  integrated: boolean
}

export interface SettingsView {
  keymap: Record<string, string>
  designKeymap: Record<string, string>
  fontSize: number
  scrollback: number
  outputColours: 'standard' | 'mono'
  prompt: 'tessa' | 'shell'
  cursorStyle: 'block' | 'bar' | 'underline'
  pasteGuard: boolean
  rightClickPastes: boolean
  copyOnSelect: boolean
  defaultShell: string
  path: string
  problems: string[]
}

export interface SavedTabView {
  shellId: string
  cwd: string
  name: string
}

export interface LinkView {
  linked: boolean
  phase: string
  detail: string
}

export interface ApprovalsView {
  count: number
  latest?: { requestId: string; tool: string; detail: string }
}

export interface ThreatView {
  count: number
  latest?: { alertId: string; title: string }
}

export interface InitView {
  shells: ShellInfo[]
  settings: SettingsView
  restore: { tabs: SavedTabView[]; recent: string[] }
  startDir: string | null
  home: string
  link: LinkView
  approvals: ApprovalsView
  threat: ThreatView
  maximized: boolean
}

export type PtyStartResult =
  | {
      ok: true
      sessionId: string
      grantId: string
      grantMs: number
      pid: number
      shellId: string
      integrated: boolean
      cwd: string
      cwdNote: string
    }
  | { ok: false; error: string; waiting?: boolean }

export interface GitView {
  cwd: string
  repo: boolean
  branch: string
  changed: number | null
  note: string
  ms: number
}

export interface LinkResultView {
  ok: boolean
  opened?: string
  message: string
}

function on<T>(channel: string, fn: (v: T) => void): () => void {
  const h = (_e: unknown, v: T): void => fn(v)
  ipcRenderer.on(channel, h)
  return () => ipcRenderer.removeListener(channel, h)
}

const api = {
  init: (): Promise<InitView> => ipcRenderer.invoke('tessa:init'),
  startPty: (
    dims: { cols: number; rows: number },
    req: { shellId: string; cwd: string; banner?: string },
  ): Promise<PtyStartResult> => ipcRenderer.invoke('tessa:pty-start', dims, req),
  closePty: (sessionId: string, why: string): Promise<boolean> => ipcRenderer.invoke('tessa:pty-close', sessionId, why),
  saveSettings: (patch: Record<string, unknown>): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('tessa:settings-save', patch),
  saveSession: (s: { tabs: SavedTabView[]; recent: string[] }): Promise<boolean> => ipcRenderer.invoke('tessa:session-save', s),
  checkDir: (p: string): Promise<string | null> => ipcRenderer.invoke('tessa:check-dir', p),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('tessa:pick-folder'),
  git: (cwd: string, withCount: boolean): Promise<GitView> => ipcRenderer.invoke('tessa:git', cwd, withCount),
  openLink: (req: { kind: 'url' | 'path'; target: string; base?: string; line?: number; col?: number }): Promise<LinkResultView> =>
    ipcRenderer.invoke('tessa:open-link', req),
  clipRead: (): Promise<string> => ipcRenderer.invoke('tessa:clip-read'),
  clipWrite: (text: string): Promise<boolean> => ipcRenderer.invoke('tessa:clip-write', text),
  windowCommand: (cmd: 'minimize' | 'maximize' | 'close'): Promise<boolean> => ipcRenderer.invoke('tessa:win', cmd),
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
  onLink: (fn: (v: LinkView) => void): (() => void) => on('tessa:link', fn),
  onApprovals: (fn: (v: ApprovalsView) => void): (() => void) => on('tessa:approvals', fn),
  onThreat: (fn: (v: ThreatView) => void): (() => void) => on('tessa:threat', fn),
  onSettings: (fn: (v: SettingsView) => void): (() => void) => on('tessa:settings', fn),
  onPtyEnded: (fn: (v: { sessionId: string; why: string; code?: number; revoked?: boolean }) => void): (() => void) =>
    on('tessa:pty-ended', fn),
  onMaximized: (fn: (v: boolean) => void): (() => void) => on('tessa:maximized', fn),
} as const

export type TessaApi = typeof api

ipcRenderer.on(PTY_PORT_CHANNEL, (event, payload: { sessionId?: string } | null) => {
  window.postMessage({ channel: PTY_PORT_CHANNEL, sessionId: payload?.sessionId ?? '' }, '*', event.ports)
})

if (process.contextIsolated) {
  contextBridge.exposeInMainWorld('tessa', api)
} else {
  throw new Error('contextIsolation is disabled — refusing to expose the Tessa API')
}
