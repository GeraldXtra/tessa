/**
 * Dev-only instrumentation. Never rendered in a packaged build.
 *
 * Exists because two of this phase's claims are measurements, not assertions:
 * which tier the GPU probe chose on the HD 620, and whether that tier holds a
 * 30 fps frame budget while sharing two cores with the daemon. Spec §4 makes
 * missing a latency target a bug — so the number has to be visible, not
 * inferred from how it looks.
 *
 * Polls at 2 Hz. The engine keeps its own rolling percentiles; this only reads
 * them, so the overlay cannot perturb what it is measuring.
 */

import { useEffect, useState } from 'react';

import type { SphereTier } from '../../shared/ipc-contract.ts';
import { PARTICLE_COUNT } from '../scene/gpu-tier.ts';
import type { SphereStats } from '../scene/sphere-engine.ts';

interface DevOverlayProps {
  /**
   * The live tier from the store — NOT read off engine stats. When the tier
   * falls to 'dom' the Sphere unmounts and its engine is disposed, but the
   * stats closure keeps returning the last object it built. Reading the tier
   * from there made the overlay report `med · 8,000 pts` while a DOM sphere was
   * on screen, which is exactly the kind of instrument that makes you trust a
   * wrong number.
   */
  tier: SphereTier;
  readStats: (() => SphereStats) | null;
  tierReason: string;
  rendererName: string;
}

export function DevOverlay({ tier, readStats, tierReason, rendererName }: DevOverlayProps) {
  const [stats, setStats] = useState<SphereStats | null>(null);

  useEffect(() => {
    if (!readStats) {
      setStats(null);
      return;
    }
    const id = window.setInterval(() => setStats(readStats()), 500);
    return () => window.clearInterval(id);
  }, [readStats]);

  return (
    <div className="dev-overlay">
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">tier</span>
        <span>{`${tier} · ${PARTICLE_COUNT[tier].toLocaleString()} pts`}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">frame</span>
        <span>
          {stats && stats.p50 > 0
            ? `p50 ${stats.p50.toFixed(1)}ms · p95 ${stats.p95.toFixed(1)}ms · ${stats.fps.toFixed(0)}fps`
            : 'sampling…'}
        </span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">submit</span>
        <span>{stats ? `${stats.submitMs.toFixed(2)}ms` : '—'}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">gpu</span>
        <span className="dev-overlay__wrap">{rendererName}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">why</span>
        <span className="dev-overlay__wrap">{tierReason}</span>
      </div>
      <div className="dev-overlay__row dev-overlay__row--hint">
        <span className="dev-overlay__key">keys</span>
        <span>alt+1…6 states · esc closes drawer</span>
      </div>
    </div>
  );
}
