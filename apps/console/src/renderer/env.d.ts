/// <reference types="vite/client" />
import type { TessaApi } from '../preload/index.ts'

declare global {
  interface Window {
    tessa: TessaApi
  }
}
export {}

declare global {
  interface Window {
    /** Dev harness only: a readable rendering of the pane tree. */
    __tessaTree?: () => string
  }
}

declare global {
  interface Window {
    /** Dev harness only: the focused terminal's visible buffer. */
    __tessaBuffer?: () => string
  }
}
