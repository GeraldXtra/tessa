/**
 * apps/console/src/renderer/terminal/gpu-probe.ts
 *
 * Decide the renderer rung BEFORE `term.open()`.
 *
 * Why this is mandatory rather than nice-to-have: **xterm 6 removed
 * `@xterm/addon-canvas`**, so the ladder is only two rungs deep — WebGL, then
 * DOM. There is no middle. Attaching the WebGL addon on a machine that cannot
 * support it does not degrade gracefully; it fails, and on this hardware
 * (Intel HD 620, driver 31.0.101.2130 — a legacy branch) that is a real
 * possibility rather than a hypothetical.
 *
 * The probe uses a THROWAWAY canvas. It never touches the terminal's own
 * context, so a failed probe costs nothing and cannot leave xterm in a
 * half-initialised state.
 */

export type Rung = 'webgl' | 'dom'

export interface ProbeResult {
  rung: Rung
  /** Human-readable, reported verbatim — never summarised away. */
  reason: string
  /** Raw strings when the driver exposes them. */
  vendor?: string
  renderer?: string
  /** True when the DOM rung was chosen by a flag rather than by capability. */
  forced: boolean
}

/**
 * Renderers that mean "no real GPU". Matching any of these selects DOM: a
 * software GL implementation is slower than the DOM renderer for a text grid,
 * so falling back is the faster choice, not merely the safer one.
 */
const SOFTWARE_MARKERS = ['swiftshader', 'llvmpipe', 'software', 'microsoft basic render']

/** `?forceDom` (or `#forceDom`) proves the fallback rung instead of assuming it. */
export function domForced(search: string = window.location.search + window.location.hash): boolean {
  return /(?:[?&#])forceDom\b/i.test(search)
}

export function probeGpu(force = domForced()): ProbeResult {
  if (force) {
    return {
      rung: 'dom',
      reason: 'DOM renderer FORCED via ?forceDom — proving the fallback rung, not a capability result',
      forced: true,
    }
  }

  let canvas: HTMLCanvasElement | null = null
  try {
    canvas = document.createElement('canvas')
    // `failIfMajorPerformanceCaveat` is the point of the whole probe: it makes
    // Chromium refuse a context that would be backed by a software rasteriser,
    // which is exactly the case we must not attach the WebGL addon for.
    const gl = canvas.getContext('webgl2', {
      failIfMajorPerformanceCaveat: true,
      antialias: false,
      depth: false,
      stencil: false,
      preserveDrawingBuffer: false,
    }) as WebGL2RenderingContext | null

    if (!gl) {
      return {
        rung: 'dom',
        reason: 'no webgl2 context (or major performance caveat) — falling back to DOM',
        forced: false,
      }
    }

    // WEBGL_debug_renderer_info is not guaranteed to be exposed. Its absence is
    // NOT a failure — it only costs us the human-readable driver string, so we
    // still take the WebGL rung and say why the detail is missing.
    const dbg = gl.getExtension('WEBGL_debug_renderer_info')
    const vendor = dbg ? String(gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL)) : undefined
    const renderer = dbg ? String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)) : undefined

    const hay = `${vendor ?? ''} ${renderer ?? ''}`.toLowerCase()
    const marker = SOFTWARE_MARKERS.find((m) => hay.includes(m))
    if (marker) {
      return {
        rung: 'dom',
        reason: `software renderer detected ("${marker}") — DOM is faster than software GL for a text grid`,
        vendor,
        renderer,
        forced: false,
      }
    }

    return {
      rung: 'webgl',
      reason: renderer
        ? `hardware webgl2 — ${renderer}`
        : 'hardware webgl2 (WEBGL_debug_renderer_info unavailable, so no driver string)',
      vendor,
      renderer,
      forced: false,
    }
  } catch (err) {
    return {
      rung: 'dom',
      reason: `webgl2 probe threw (${(err as Error).message}) — falling back to DOM`,
      forced: false,
    }
  } finally {
    // Release the probe context promptly. Chromium caps live WebGL contexts,
    // and leaking one here would eventually starve the terminal's own.
    if (canvas) {
      canvas.width = 0
      canvas.height = 0
      canvas = null
    }
  }
}
