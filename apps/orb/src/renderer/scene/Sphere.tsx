import { useEffect, useRef } from 'react';

import type { AgentState } from '@tessa/protocol';

import type { SceneBoot } from '../../shared/ipc-contract.ts';
import { devVisualStore, jobRings, jobsStore, sentinelFlareStore } from '../state/plasma-inputs.ts';
import { approvalsStore } from '../state/approval-store.ts';
import { sceneHookStore, stageStore } from '../state/scene-inputs.ts';
import { agentStateStore, calendarStore, connectionStore } from '../state/store.ts';
import { bootStore } from '../ui/stores.ts';
import { createOrbEngine, type OrbEngine } from './orb-engine.ts';
import type { MarkReport, RenderPath } from './plasma-engine.ts';
import { BEAT_STALE_MS } from './plasma-model.ts';
import { tierFromName } from './ultron/quality.ts';

interface SphereProps {
  forceFallback: boolean;
  clock: { hours: number; raw: boolean } | null;
  scene: SceneBoot;
  isDev: boolean;
  onEngineReady?: (engine: OrbEngine) => void;
  onStateRendered?: (state: AgentState, at: number) => void;
  onMark?: (mark: MarkReport) => void;
  onPath?: (path: RenderPath, reason: string) => void;
}

export function Sphere({ forceFallback, clock, scene, isDev, onEngineReady, onStateRendered, onMark, onPath }: SphereProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const ribbonRef = useRef<HTMLCanvasElement>(null);
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  const onEngineReadyRef = useRef(onEngineReady);
  onEngineReadyRef.current = onEngineReady;
  const onStateRenderedRef = useRef(onStateRendered);
  onStateRenderedRef.current = onStateRendered;
  const onMarkRef = useRef(onMark);
  onMarkRef.current = onMark;
  const onPathRef = useRef(onPath);
  onPathRef.current = onPath;
  const initial = useRef({ forceFallback, clock, scene, isDev });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const boot = initial.current;
    const engine = createOrbEngine({
      canvas,
      ribbon: ribbonRef.current,
      getState: () => (approvalsStore.get().some((e) => e.invalidated === null) ? 'blocked' : agentStateStore.get()),
      forceFallback: boot.forceFallback,
      clock: boot.clock,
      onStateRendered: (state, at) => onStateRenderedRef.current?.(state, at),
      onMark: (mark) => onMarkRef.current?.(mark),
      onSentinel: (alert) => sentinelFlareStore.set(alert),
      onPath: (path, reason) => onPathRef.current?.(path, reason),
      hold: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    });
    engine.setCallouts([leftRef.current, rightRef.current]);
    engine.setOverlays(!boot.scene.cssOverlays);
    engine.setOnBattery(boot.scene.onBattery);
    const pinned = tierFromName(boot.scene.quality);
    if (pinned !== null) engine.pinQuality(pinned);
    onEngineReadyRef.current?.(engine);
    sceneHookStore.set({ push: (x, y, s) => engine.push(x, y, s) });

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

    const sources = (): void => engine.setPlanetSources({ calendar: calendarStore.get(), linkUp: connectionStore.get().phase === 'connected' });
    sources();
    const applyJobs = (): void => engine.setJobs(jobRings(jobsStore.get(), devVisualStore.get().jobs));
    const applyDev = (): void => {
      const d = devVisualStore.get();
      engine.setMuted(d.muted);
      engine.setNight(d.night);
      live();
      applyJobs();
    };
    applyDev();
    const applyStage = (): void => engine.setStage(stageStore.get());
    applyStage();
    const applyBoot = (): void => {
      if (bootStore.get() !== 'log') engine.grow();
    };
    applyBoot();

    const offState = agentStateStore.subscribe(() => engine.noteStateChange(performance.now()));
    const offCards = approvalsStore.subscribe(() => engine.noteStateChange(performance.now()));
    const offConnection = connectionStore.subscribe(() => {
      live();
      sources();
    });
    const offCalendar = calendarStore.subscribe(sources);
    const offStage = stageStore.subscribe(applyStage);
    const offBoot = bootStore.subscribe(applyBoot);
    const offBeat = window.tessa.onHealth(() => {
      lastBeatAt = Date.now();
      live();
      engine.beat();
    });
    const staleTimer = window.setInterval(live, 1000);
    const offLoad = window.tessa.onMachineLoad((load) => engine.setLoad(load.cpu, load.mem));
    const offVoice = window.tessa.onVoiceLevel((push) => engine.setVoiceLevel(push.level));
    const offPower = window.tessa.onPower((power) => {
      engine.setOnBattery(power.onBattery);
      window.tessa.reportMetrics(`SCENE power ${power.onBattery ? 'battery' : 'charger'} (${power.source})`);
    });
    const offDisplay = window.tessa.onDisplayChanged(() => engine.reprobeRefresh());
    const offJobs = jobsStore.subscribe(applyJobs);
    const offDev = devVisualStore.subscribe(applyDev);
    const metrics = boot.isDev
      ? window.setInterval(() => {
          const s = engine.sceneStats();
          window.tessa.reportMetrics(
            `SCENE kind=${engine.scene()} tier=${s.tierName} pinned=${s.pinned} battery=${s.onBattery} changes=${s.changes} points=${s.points} ` +
              `frozen=${s.frozen} galaxy=${s.galaxy.toFixed(2)} gpuTimer=${s.gpuTimer} fmt=${s.format} overlays=${s.overlays} ` +
              `callouts=${s.callouts.join('/')} unit=${s.unit.toFixed(1)} shellPx=${s.shellPx.toFixed(1)} boot=${s.bootDone}`,
          );
        }, 5000)
      : 0;

    return () => {
      offState();
      offCards();
      offConnection();
      offCalendar();
      offStage();
      offBoot();
      offBeat();
      window.clearInterval(staleTimer);
      window.clearInterval(metrics);
      offLoad();
      offVoice();
      offPower();
      offDisplay();
      offJobs();
      offDev();
      sceneHookStore.set(null);
      engine.dispose();
    };
  }, []);

  return (
    <>
      <canvas ref={canvasRef} className="sphere-canvas" aria-hidden="true" />
      {scene.cssOverlays ? (
        <>
          <div className="scene-ov vig" aria-hidden="true" />
          <div className="scene-ov grain" aria-hidden="true" />
          <div className="scene-ov scan" aria-hidden="true" />
        </>
      ) : null}
      <div ref={leftRef} className="call l" aria-hidden="true">
        <div className="cn" />
        <div className="cd" />
        <div className="cs" />
      </div>
      <div ref={rightRef} className="call r" aria-hidden="true">
        <div className="cn" />
        <div className="cd" />
        <div className="cs" />
      </div>
      <canvas ref={ribbonRef} className="sphere-ribbon" aria-hidden="true" />
    </>
  );
}
