/**
 * The contextBridge. This is the entire attack surface between the renderer and
 * the process that holds the daemon token.
 *
 * Everything exposed here is either a read of connection *status* or a
 * parameterless verb. There is deliberately no channel that takes a message
 * type, a payload, or a path from the renderer — CONTRACT §2.3 moves the socket
 * into main precisely so the renderer cannot drive the daemon, and a generic
 * `send(type, payload)` bridge would hand that capability straight back.
 *
 * Note also what the listener wrapper does: it drops the IpcRendererEvent and
 * passes only the status object. That event carries a `sender` reference, and
 * handing it to renderer code would leak an IPC capability into the sandbox.
 */

import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';

import {
  IPC,
  type AuditEntry,
  type BootstrapInfo,
  type ConnectionStatus,
  type DaemonHealth,
  type MicState,
  type OrbNotification,
  type PtySession,
  type PttMode,
  type Snapshot,
  type TranscriptLine,
  type ZoeyBridge,
} from '../shared/ipc-contract.ts';

/**
 * Every push channel is the same shape: register, strip the IpcRendererEvent,
 * hand back an unsubscribe. Factored because there are now eight of them and
 * the one thing that must never vary is dropping the event object — it carries
 * a `sender`, and leaking that into the sandbox would hand renderer code an IPC
 * capability the contextBridge exists to withhold.
 */
function subscribe<T>(channel: string) {
  return (listener: (value: T) => void): (() => void) => {
    const handler = (_event: IpcRendererEvent, value: T): void => listener(value);
    ipcRenderer.on(channel, handler);
    return () => {
      ipcRenderer.off(channel, handler);
    };
  };
}

const bridge: ZoeyBridge = {
  bootstrap: (): Promise<BootstrapInfo> => ipcRenderer.invoke(IPC.bootstrap),

  getConnection: (): Promise<ConnectionStatus> => ipcRenderer.invoke(IPC.getConnection),

  getSnapshot: (): Promise<Snapshot> => ipcRenderer.invoke(IPC.getSnapshot),

  onConnection: (listener: (status: ConnectionStatus) => void): (() => void) => {
    const handler = (_event: IpcRendererEvent, status: ConnectionStatus): void => listener(status);
    ipcRenderer.on(IPC.connectionChanged, handler);
    return () => {
      ipcRenderer.off(IPC.connectionChanged, handler);
    };
  },

  onHealth: (listener: (health: DaemonHealth) => void): (() => void) => {
    const handler = (_event: IpcRendererEvent, health: DaemonHealth): void => listener(health);
    ipcRenderer.on(IPC.healthChanged, handler);
    return () => {
      ipcRenderer.off(IPC.healthChanged, handler);
    };
  },

  onAgentState: (listener: (state: string) => void): (() => void) => {
    const handler = (_event: IpcRendererEvent, state: string): void => listener(state);
    ipcRenderer.on(IPC.agentStateChanged, handler);
    return () => {
      ipcRenderer.off(IPC.agentStateChanged, handler);
    };
  },

  onDisplayChanged: (listener: () => void): (() => void) => {
    const handler = (): void => listener();
    ipcRenderer.on(IPC.displayChanged, handler);
    return () => {
      ipcRenderer.off(IPC.displayChanged, handler);
    };
  },

  onAuditAppended: subscribe<AuditEntry>(IPC.auditAppended),
  onAuditHistory: subscribe<AuditEntry[]>(IPC.auditHistory),
  onPtySessions: subscribe<PtySession[]>(IPC.ptySessions),
  onTranscriptLine: subscribe<TranscriptLine>(IPC.transcriptLine),
  onMicState: subscribe<MicState>(IPC.micState),
  onNotification: subscribe<OrbNotification>(IPC.notify),

  /**
   * An EDGE, not an action. Note what is NOT here: no `startRecording`, no way
   * to name an action at all. The renderer reports that a key moved and main
   * decides what that means under the current mode — same rule as the rest of
   * this bridge, applied to the one command where being wrong opens a
   * microphone. The value is re-checked in main; this narrowing is convenience,
   * not the guard.
   */
  pushToTalkEdge: (edge: 'down' | 'up'): void => {
    if (edge === 'down' || edge === 'up') ipcRenderer.send(IPC.pttEdge, edge);
  },

  setPushToTalkMode: (mode: PttMode): void => {
    if (mode === 'toggle' || mode === 'hold') ipcRenderer.send(IPC.pttSetMode, mode);
  },

  retryConnection: (): void => ipcRenderer.send(IPC.retryConnection),
  minimizeWindow: (): void => ipcRenderer.send(IPC.windowMinimize),
  closeWindow: (): void => ipcRenderer.send(IPC.windowClose),

  // Coerced to a string here rather than trusted from the caller: this channel
  // ends up in a log line, and the renderer does not get to choose its shape.
  reportMetrics: (line: string): void => ipcRenderer.send(IPC.devMetrics, String(line).slice(0, 400)),
};

contextBridge.exposeInMainWorld('zoey', bridge);
