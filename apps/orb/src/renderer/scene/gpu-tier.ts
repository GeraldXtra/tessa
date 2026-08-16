/**
 * Renderer-side half of the GPU probe.
 *
 * The main process asked Chromium what it thinks (gpu-probe.ts). This asks the
 * actual context, because the two can disagree: Chromium can report
 * `webgl2: enabled` and still hand back a SwiftShader context, which is WebGL2
 * running on the CPU. On an i5-7200U that would take one of two physical cores
 * away from the daemon to draw a decorative sphere — the worst possible outcome
 * and the one that looks fine in a feature-status string.
 *
 * ─── on not auto-selecting HIGH ───
 * Nothing here ever returns 'high'. The rung exists, and `--force-tier=high`
 * reaches it, but a passing probe resolves to 'med'. We have benchmarked
 * exactly one machine, and inferring "this is a fast GPU" from a renderer
 * string is guesswork that fails toward *more* load. The governor in
 * sphere-engine.ts only ever demotes, for the same reason: a wrong guess
 * downward costs some particles, a wrong guess upward costs a frame budget the
 * daemon is sharing.
 */

import type { GpuHint, SphereTier } from '../../shared/ipc-contract.ts';

export interface TierProbe {
  tier: SphereTier;
  /** UNMASKED_RENDERER_WEBGL, or a short explanation when unavailable. */
  renderer: string;
  /** Why this tier, in words fit for a dev overlay. */
  reason: string;
}

/** Names that mean the GPU is not involved. */
const SOFTWARE_RENDERER = /swiftshader|basic render|llvmpipe|software|microsoft basic/i;

/**
 * Particles per rung. 'dom' draws no particles at all.
 *
 * ─── med went 8,000 -> 20,000 ON MEASUREMENT, and here is the measurement ───
 *
 * The old ladder was set before the shell had a crescent, when the fill-rate
 * estimate was a guess. At 20,000 particles with the crescent, on the owner's
 * HD 620 with its legacy driver, 60 s focused per state:
 *
 *   idle      cost p50/p95 0.20/0.30 ms   raf 16.7/17.0   shown 33.3/33.6   30.0 fps
 *   thinking  cost p50/p95 0.10/0.30 ms   raf 16.7/17.0   shown 33.3/33.6   30.0 fps
 *
 * 0.30 ms against COST_BUDGET_MS of 12 is 2.5% of the governor's budget, and
 * `shown` is pinned to exactly two 60 Hz vsync intervals in both states. The
 * old 8,000 was costing the sphere three quarters of its density for nothing.
 *
 * It matters visually and not just numerically: at 8,000 the particles have to
 * be large to cover the shell, and large particles on a Fibonacci lattice show
 * the lattice — the projection produces concentric arcs of dots that the
 * reference does not have. 20,000 smaller ones cover the same area with the
 * fine irregular speckle the reference has. Coverage is N x size², so trading
 * count for size at constant coverage is very nearly free on fill rate, which
 * is what the numbers above say.
 *
 * `low` is the governor's demotion rung and inherits the old med, so a demotion
 * lands on a count whose cost is known rather than on an untested one. `high`
 * is UNREACHABLE from probeSphereTier — it never returns anything but med, low
 * or dom — and exists only for `--force-tier=high`, so it is kept above med as
 * a headroom probe rather than pruned.
 */
export const PARTICLE_COUNT: Record<SphereTier, number> = {
  high: 32_000,
  med: 20_000,
  low: 8_000,
  dom: 0,
};

function readRendererName(gl: WebGL2RenderingContext): string {
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  if (!ext) return String(gl.getParameter(gl.RENDERER) ?? 'unknown');
  return String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) ?? 'unknown');
}

export function probeSphereTier(hint: GpuHint): TierProbe {
  if (hint.forcedTier) {
    return {
      tier: hint.forcedTier,
      renderer: 'not probed',
      reason: `forced by --force-tier=${hint.forcedTier}`,
    };
  }

  const canvas = document.createElement('canvas');
  canvas.width = 1;
  canvas.height = 1;

  const options: WebGLContextAttributes = {
    alpha: false,
    antialias: false,
    depth: false,
    stencil: false,
    powerPreference: 'low-power',
    // The flag that makes this probe worth running: Chromium refuses the
    // context outright rather than silently handing back a software one.
    failIfMajorPerformanceCaveat: true,
  };

  let gl = canvas.getContext('webgl2', options) as WebGL2RenderingContext | null;
  let caveat = false;

  if (!gl) {
    // Retry without the flag. If THIS also fails there is no WebGL2 at all and
    // the DOM rung is the only option left.
    caveat = true;
    gl = canvas.getContext('webgl2', {
      ...options,
      failIfMajorPerformanceCaveat: false,
    }) as WebGL2RenderingContext | null;
  }

  if (!gl) {
    return {
      tier: 'dom',
      renderer: 'none',
      reason: 'no WebGL2 context available',
    };
  }

  const renderer = readRendererName(gl);
  const software = SOFTWARE_RENDERER.test(renderer);

  // Release the probe context immediately. Browsers cap the number of live
  // WebGL contexts and will drop the OLDEST one to make room — which would be
  // the sphere's.
  gl.getExtension('WEBGL_lose_context')?.loseContext();

  if (software) {
    return { tier: 'low', renderer, reason: 'software rasteriser detected' };
  }
  if (caveat) {
    return { tier: 'low', renderer, reason: 'context only available with a performance caveat' };
  }
  // ── the feature table is deliberately NOT consulted below this point ──
  //
  // It was, and it was wrong. Measured on the owner's machine, Electron 43
  // reports no `webgl2` key at all and the whole table reads as software:
  //
  //   webgl=disabled_off        gpu_compositing=disabled_software
  //   rasterization=disabled_software   2d_canvas=disabled_software
  //
  // while the live context is ANGLE / Intel HD Graphics 620 / Direct3D11,
  // obtained WITH failIfMajorPerformanceCaveat set. Chromium composites and
  // rasterises in software on this legacy driver, but WebGL itself runs on the
  // GPU — different subsystems, and the table only describes the first. Trusting
  // it cost the sphere 5,000 particles for nothing.
  //
  // Everything that genuinely indicates software rendering is already caught
  // above, by evidence that cannot be second-guessed: no context at all, a
  // context only obtainable with a performance caveat, or a renderer string
  // naming a software rasteriser. `hint` is carried for the dev overlay and the
  // startup log, not for this decision.
  return { tier: 'med', renderer, reason: 'hardware WebGL2 confirmed by live context' };
}
