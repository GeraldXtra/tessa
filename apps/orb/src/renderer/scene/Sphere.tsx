import { useEffect, useRef } from 'react';

import type { AgentState } from '@tessa/protocol';

import { devVisualStore, jobRings, jobsStore, sentinelFlareStore } from '../state/plasma-inputs.ts';
import { approvalsStore } from '../state/approval-store.ts';
import { agentStateStore, connectionStore } from '../state/store.ts';
import { createPlasmaEngine, type MarkReport, type PlasmaEngine, type RenderPath } from './plasma-engine.ts';
import { BEAT_STALE_MS } from './plasma-model.ts';

interface SphereProps {
  forceFallback: boolean;
  clock: { hours: number; raw: boolean } | null;
  onEngineReady?: (engine: PlasmaEngine) => void;
  onStateRendered?: (state: AgentState, at: number) => void;
  onMark?: (mark: MarkReport) => void;
  onPath?: (path: RenderPath, reason: string) => void;
}

export function Sphere({
  forceFallback,
  clock,
  onEngineReady,
  onStateRendered,
  onMark,
  onPath,
}: SphereProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const ribbonRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<PlasmaEngine | null>(null);
  const onEngineReadyRef = useRef(onEngineReady);
  onEngineReadyRef.current = onEngineReady;
  const onStateRenderedRef = useRef(onStateRendered);
  onStateRenderedRef.current = onStateRendered;
  const onMarkRef = useRef(onMark);
  onMarkRef.current = onMark;
  const onPathRef = useRef(onPath);
  onPathRef.current = onPath;
  const initial = useRef({ forceFallback, clock });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const engine = createPlasmaEngine({
      canvas,
      ribbon: ribbonRef.current,
      getState: () => (approvalsStore.get().some((e) => e.invalidated === null) ? 'blocked' : agentStateStore.get()),
      forceFallback: initial.current.forceFallback,
      clock: initial.current.clock,
      onStateRendered: (state, at) => onStateRenderedRef.current?.(state, at),
      onMark: (mark) => onMarkRef.current?.(mark),
      onSentinel: (alert) => sentinelFlareStore.set(alert),
      onPath: (path, reason) => onPathRef.current?.(path, reason),
    });
    engineRef.current = engine;
    onEngineReadyRef.current?.(engine);

    let connectedAt = 0;
    let lastBeatAt = 0;
    const live = (): void => {
      const now = Date.now();
      const up = connectionStore.get().phase === 'connected';
      if (!up) {
        connectedAt = 0;
        lastBeatAt = 0;
      } else if (connectedAt === 0) {
        connectedAt = now;
      }
      const since = lastBeatAt > 0 ? lastBeatAt : connectedAt;
      const beating = up && now - since <= BEAT_STALE_MS;
      engine.setConnected(beating && !devVisualStore.get().disconnected);
    };
    engine.setConnected(false);
    live();

    const applyJobs = (): void => engine.setJobs(jobRings(jobsStore.get(), devVisualStore.get().jobs));
    const applyDev = (): void => {
      const d = devVisualStore.get();
      engine.setMuted(d.muted);
      engine.setNight(d.night);
      live();
      applyJobs();
    };
    applyDev();

    const offState = agentStateStore.subscribe(() => engine.noteStateChange(performance.now()));
    const offConnection = connectionStore.subscribe(live);
    const offBeat = window.tessa.onHealth(() => {
      lastBeatAt = Date.now();
      live();
      engine.beat();
    });
    const staleTimer = window.setInterval(live, 1000);
    const offLoad = window.tessa.onMachineLoad((load) => engine.setLoad(load.cpu, load.mem));
    const offVoice = window.tessa.onVoiceLevel((level) => engine.setVoiceLevel(level));
    const offDisplay = window.tessa.onDisplayChanged(() => engine.reprobeRefresh());
    const offJobs = jobsStore.subscribe(applyJobs);
    const offDev = devVisualStore.subscribe(applyDev);

    return () => {
      offState();
      offConnection();
      offBeat();
      window.clearInterval(staleTimer);
      offLoad();
      offVoice();
      offDisplay();
      offJobs();
      offDev();
      engine.dispose();
      engineRef.current = null;
    };
  }, []);

  return (
    <>
      <canvas ref={canvasRef} className="sphere-canvas" aria-hidden="true" />
      <canvas ref={ribbonRef} className="sphere-ribbon" aria-hidden="true" />
    </>
  );
}
