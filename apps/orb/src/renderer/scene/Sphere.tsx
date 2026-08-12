/**
 * The React boundary around the sphere — and it is a boundary, not a wrapper.
 *
 * This component mounts a canvas, hands it to the imperative engine once, and
 * then gets out of the way. It does NOT re-render when the agent state changes,
 * when the amplitude moves, or on any frame. The engine reads state through a
 * getter (state/store.ts) so React is never in the animation path.
 *
 * The only things that cross back in are tier changes and the drawer offset,
 * both rare and both pushed through imperative setters.
 */

import { useEffect, useRef } from 'react';

import type { SphereTier } from '../../shared/ipc-contract.ts';
import { agentStateStore } from '../state/store.ts';
import { createSphereEngine, type SphereEngine, type SphereStats } from './sphere-engine.ts';

interface SphereProps {
  tier: SphereTier;
  /** How far to shift the sphere left, in pixels, when a drawer is open. */
  offsetPx: number;
  onTierChange: (tier: SphereTier, reason: string) => void;
  /** Dev overlay polls this; absent in production. */
  onEngineReady?: (readStats: () => SphereStats) => void;
}

export function Sphere({ tier, offsetPx, onTierChange, onEngineReady }: SphereProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<SphereEngine | null>(null);

  // Held in refs so the mount effect can stay dependency-free without capturing
  // a stale callback. Re-running it would mean tearing down and rebuilding a
  // WebGL context, which is the single most expensive thing this app can do.
  const onTierChangeRef = useRef(onTierChange);
  onTierChangeRef.current = onTierChange;
  const onEngineReadyRef = useRef(onEngineReady);
  onEngineReadyRef.current = onEngineReady;
  const initialTierRef = useRef(tier);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const engine = createSphereEngine({
      canvas,
      initialTier: initialTierRef.current,
      getState: () => agentStateStore.get(),
      onTierChange: (next, reason) => onTierChangeRef.current(next, reason),
    });
    engineRef.current = engine;
    onEngineReadyRef.current?.(engine.stats);

    return () => {
      engine.dispose();
      engineRef.current = null;
    };
  }, []);

  useEffect(() => {
    engineRef.current?.setTier(tier);
  }, [tier]);

  useEffect(() => {
    engineRef.current?.setCentreOffsetPx(offsetPx);
  }, [offsetPx]);

  return <canvas ref={canvasRef} className="sphere-canvas" aria-hidden="true" />;
}
