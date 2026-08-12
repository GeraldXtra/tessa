/// <reference types="vite/client" />
import type { ZoeyApi } from '../preload/index.ts'

declare global {
  interface Window {
    zoey: ZoeyApi
  }
}
export {}
