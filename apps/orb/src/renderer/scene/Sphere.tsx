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

import type { AgentState } from '@zoey/protocol';

import type { SphereTier } from '../../shared/ipc-contract.ts';
import { agentStateStore } from '../state/store.ts';
import {
  createSphereEngine,
  type SphereEngine,
  type SphereEngineOptions,
} from './sphere-engine.ts';

interface SphereProps {
  tier: SphereTier;
  /**
   * Where the sphere sits, as a shift from the canvas centre. Positive x moves
   * it left by x/2 px, positive y moves it up by y/2 px.
   *
   * ONE NUMBER PAIR FOR THE WHOLE LAYOUT. App computes it from the composition's
   * base placement AND the drawer state together, so the two cannot fight — the
   * drawer used to own this value outright, which meant a composition that
   * starts the sphere off-centre would have been overwritten every time a
   * drawer opened.
   */
  offsetPx: number;
  offsetYPx: number;
  onTierChange: (tier: SphereTier, reason: string) => void;
  /**
   * The engine itself, once. The dev overlay polls `stats()` and the dev probes
   * call `probeFrame()`; handing up the whole object beats adding a prop per
   * imperative method. Nothing in production calls this.
   */
  onEngineReady?: (engine: SphereEngine) => void;
  /** Spec §4 measurement. See SphereEngineOptions.onStateRendered. */
  onStateRendered?: (state: AgentState, at: number) => void;
  /** DEV ONLY. `--force-depth=<0..1>`; null uses the engine default. */
  depthFar?: number | null;
  /** DEV ONLY. `--force-sphere=`; null uses the engine's measured defaults. */
  rim?: SphereEngineOptions['rim'] | null;
}

export function Sphere({
  tier,
  offsetPx,
  offsetYPx,
  onTierChange,
  onEngineReady,
  onStateRendered,
  depthFar,
  rim,
}: SphereProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<SphereEngine | null>(null);

  // Held in refs so the mount effect can stay dependency-free without capturing
  // a stale callback. Re-running it would mean tearing down and rebuilding a
  // WebGL context, which is the single most expensive thing this app can do.
  const onTierChangeRef = useRef(onTierChange);
  onTierChangeRef.current = onTierChange;
  const onEngineReadyRef = useRef(onEngineReady);
  onEngineReadyRef.current = onEngineReady;
  const onStateRenderedRef = useRef(onStateRendered);
  onStateRenderedRef.current = onStateRendered;
  const initialTierRef = useRef(tier);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const engine = createSphereEngine({
      canvas,
      initialTier: initialTierRef.current,
      getState: () => agentStateStore.get(),
      // Read through a ref-free capture on purpose: the mount effect is
      // dependency-free and rebuilding the WebGL context to change a constant
      // would be the most expensive thing in the app. A depth change needs a
      // relaunch, which is exactly how the before/after is taken anyway.
      ...(typeof depthFar === 'number' ? { depthFar } : {}),
      ...(rim ? { rim } : {}),
      onTierChange: (next, reason) => onTierChangeRef.current(next, reason),
      onStateRendered: (state, at) => onStateRenderedRef.current?.(state, at),
    });
    engineRef.current = engine;
    onEngineReadyRef.current?.(engine);

    // §R.8 item 8 — a display change can move us to a panel with a different
    // refresh rate, which changes the correct frame divider.
    const offDisplay = window.zoey.onDisplayChanged(() => {
      console.log('[orb] display changed — re-probing refresh rate');
      engine.reprobeRefresh();
    });

    // §R.1 — the equatorial pulse rides the real heartbeat. Subscribed here
    // rather than in App so it never passes through a React render: the pulse
    // must fire on arrival, not on the next reconciliation.
    const offBeat = window.zoey.onHealth((health) => {
      engine.beat();

      /**
       * §R.1 colour temperature, from the daemon's own CPU.
       *
       * LOAD_CEILING is the normalised-CPU figure treated as "fully hot". 25%
       * of one core is a judgement, not a measured threshold — it is picked so
       * the instrument has usable range for a process that idles near 0.3% and
       * will climb once the brain runs tool loops. Stated rather than hidden so
       * it can be retuned against real load when there is some.
       */
      const LOAD_CEILING = 25;
      engine.setLoad(health.cpuPct / LOAD_CEILING);
    });

    return () => {
      offBeat();
      offDisplay();
      engine.dispose();
      engineRef.current = null;
    };
  }, []);

  useEffect(() => {
    engineRef.current?.setTier(tier);
  }, [tier]);

  useEffect(() => {
    engineRef.current?.setCentreOffset(offsetPx, offsetYPx);
  }, [offsetPx, offsetYPx]);

  return <canvas ref={canvasRef} className="sphere-canvas" aria-hidden="true" />;
}
