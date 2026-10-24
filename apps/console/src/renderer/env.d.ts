/// <reference types="vite/client" />
import type { TessaApi } from '../preload/index.ts'

declare global {
  interface Window {
    tessa: TessaApi
  }
}
export {}
