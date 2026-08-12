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
  type BootstrapInfo,
  type ConnectionStatus,
  type ZoeyBridge,
} from '../shared/ipc-contract.ts';

const bridge: ZoeyBridge = {
  bootstrap: (): Promise<BootstrapInfo> => ipcRenderer.invoke(IPC.bootstrap),

  getConnection: (): Promise<ConnectionStatus> => ipcRenderer.invoke(IPC.getConnection),

  onConnection: (listener: (status: ConnectionStatus) => void): (() => void) => {
    const handler = (_event: IpcRendererEvent, status: ConnectionStatus): void => listener(status);
    ipcRenderer.on(IPC.connectionChanged, handler);
    return () => {
      ipcRenderer.off(IPC.connectionChanged, handler);
    };
  },

  retryConnection: (): void => ipcRenderer.send(IPC.retryConnection),
  minimizeWindow: (): void => ipcRenderer.send(IPC.windowMinimize),
  closeWindow: (): void => ipcRenderer.send(IPC.windowClose),
};

contextBridge.exposeInMainWorld('zoey', bridge);
