/**
 * Main-process half of the GPU probe.
 *
 * Spec §10: "HD 620, legacy driver — runtime GPU probe with DOM fallback is
 * mandatory." The dangerous case on this machine is not a clean absence of
 * WebGL. It is Chromium quietly falling back to SwiftShader and handing the
 * renderer a *working* WebGL2 context that is actually running on the CPU —
 * which on a 2-core part would take one of the two physical cores away from the
 * daemon to draw a decorative sphere.
 *
 * Chromium already knows the answer, so ask it here before the window exists,
 * and let the renderer refine it with its own context probe (scene/gpu-tier.ts).
 */

import { app } from 'electron';

import type { GpuHint, SphereTier } from '../shared/ipc-contract.ts';

const TIERS: readonly SphereTier[] = ['high', 'med', 'low', 'dom'];

/**
 * `--force-tier=<high|med|low|dom>`.
 *
 * Exists so the fallback rungs can actually be verified. A fallback path that
 * has never been executed is a guess, and this one only triggers on hardware we
 * cannot reproduce on demand.
 */
export function parseForcedTier(argv: readonly string[]): SphereTier | null {
  const flag = argv.find((arg) => arg.startsWith('--force-tier='));
  if (!flag) return null;

  const value = flag.slice('--force-tier='.length);
  return TIERS.includes(value as SphereTier) ? (value as SphereTier) : null;
}

/**
 * Must be called after `app.whenReady()` — the GPU process has not reported in
 * before that and every field reads as 'unknown'.
 */
/**
 * Chromium's vocabulary: 'enabled', 'enabled_readback', 'unavailable_software',
 * 'disabled_software', 'disabled_off', 'disabled_off_ok'.
 */
const SOFTWARE_STATUS = /software|disabled|unavailable/i;

export function probeGpu(argv: readonly string[]): GpuHint {
  // Cast rather than lean on Electron's GPUFeatureStatus interface: the field
  // set has changed between major versions, and an unknown key should degrade
  // to 'unknown' rather than fail to compile on the next upgrade.
  const status = app.getGPUFeatureStatus() as unknown as Record<string, string | undefined>;

  const webgl2 = status['webgl2'] ?? status['webgl'] ?? 'unknown';
  const gpuCompositing = status['gpu_compositing'] ?? 'unknown';

  // ── two corrections, both learned by measuring this machine ──
  //
  // 1. 'unknown' is the ABSENCE of a signal, not a bad one. Electron 43 does
  //    not report a `webgl2` key at all here, and treating the resulting
  //    'unknown' as evidence of software rendering demoted a perfectly good
  //    D3D11 path.
  //
  // 2. gpu_compositing is NOT a proxy for WebGL performance. This machine
  //    reports `gpu_compositing: disabled_software` — Chromium composites the
  //    window in software on this legacy driver — while WebGL itself runs on
  //    the GPU through ANGLE/D3D11. They are separate subsystems, and conflating
  //    them cost the sphere 5,000 particles for no reason. Kept for display,
  //    excluded from the decision.
  const softwareSuspected = webgl2 !== 'unknown' && SOFTWARE_STATUS.test(webgl2);

  return {
    webgl2,
    gpuCompositing,
    softwareSuspected,
    forcedTier: parseForcedTier(argv),
  };
}

/** Full feature table, for the startup log. Key names drift between majors. */
export function gpuFeatureSummary(): string {
  const status = app.getGPUFeatureStatus() as unknown as Record<string, string | undefined>;
  return Object.entries(status)
    .map(([key, value]) => `${key}=${value}`)
    .join(' ');
}
