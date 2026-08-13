/**
 * Dev-only instrumentation. Never rendered in a packaged build.
 *
 * Rebuilt after the first version produced a number that could not be acted on.
 * It showed a single `FRAME` figure that was really the pacer's own output
 * interval — bounded below by the frame target, so it could never read healthy —
 * and it never indicated that a window had gone stale, which it does the moment
 * the app loses focus. Three separate rows now, because they answer three
 * different questions and only one of them is a verdict:
 *
 *   COST     our own work per frame. The only row a tier change can move.
 *   RAF      how often the browser offers a frame at all. The ceiling.
 *   SHOWN    the cadence actually presented. A pacing readout, not a budget.
 *
 * Polls at 2 Hz and only reads what the engine already computed, so the overlay
 * cannot perturb what it measures.
 */

import { useEffect, useState } from 'react';

import type { SphereTier } from '../../shared/ipc-contract.ts';
import { PARTICLE_COUNT } from '../scene/gpu-tier.ts';
import { STATS_STALE_AFTER_MS, type SphereStats } from '../scene/sphere-engine.ts';

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

function pair(label: string, p50: number, p95: number) {
  return `${label} p50 ${p50.toFixed(p50 < 10 ? 2 : 1)} · p95 ${p95.toFixed(p95 < 10 ? 2 : 1)}ms`;
}

export function DevOverlay({ tier, readStats, tierReason, rendererName }: DevOverlayProps) {
  const [stats, setStats] = useState<SphereStats | null>(null);
  const [now, setNow] = useState(() => performance.now());

  useEffect(() => {
    if (!readStats) {
      setStats(null);
      return;
    }
    const id = window.setInterval(() => {
      setStats(readStats());
      setNow(performance.now());
    }, 500);
    return () => window.clearInterval(id);
  }, [readStats]);

  const published = stats && stats.publishedAt > 0;
  const ageMs = published ? now - stats.publishedAt : 0;
  const stale = published ? ageMs > STATS_STALE_AFTER_MS : false;

  // A window collected while unfocused was paced to 10 fps deliberately. Saying
  // so is the difference between "slow" and "idling on purpose".
  const qualifier = !published
    ? 'collecting…'
    : stale
      ? `stale ${(ageMs / 1000).toFixed(0)}s`
      : stats.focused
        ? `${stats.samples} frames`
        : 'unfocused — paced to 10fps';

  return (
    <div className="dev-overlay" data-stale={stale || (published && !stats.focused)}>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">tier</span>
        <span>{`${tier} · ${PARTICLE_COUNT[tier].toLocaleString()} pts`}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">cost</span>
        <span>{published ? pair('', stats.cost.p50, stats.cost.p95) : '—'}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">raf</span>
        <span>{published ? pair('', stats.raf.p50, stats.raf.p95) : '—'}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">shown</span>
        <span>
          {published
            ? `${pair('', stats.present.p50, stats.present.p95)} · ${stats.fps.toFixed(0)}fps`
            : '—'}
        </span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">window</span>
        <span>{qualifier}</span>
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
