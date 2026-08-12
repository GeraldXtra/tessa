/// <reference types="vite/client" />

import type { ZoeyBridge } from '../shared/ipc-contract.ts';

declare global {
  interface Window {
    /**
     * The only channel out of the renderer, installed by src/preload/index.ts.
     * Status reads and parameterless verbs — no token, no arbitrary frames.
     */
    zoey: ZoeyBridge;
  }
}
