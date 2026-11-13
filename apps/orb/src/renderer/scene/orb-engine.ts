import { createPlasmaEngine, type Followers, type JobRingInput, type PlasmaEngine, type PlasmaEngineOptions } from './plasma-engine.ts';
import type { Box } from './ultron/callouts.ts';
import { createUltronEngine, type SceneExtras, type SceneStats } from './ultron/engine.ts';
import type { PlanetSources } from './ultron/planets.ts';

export type OrbEngine = PlasmaEngine & SceneExtras & { scene(): 'ultron' | 'sphere' };

interface Replay {
  placement: [number, number, number] | null;
  followers: Followers | null;
  compact: boolean;
  grown: boolean;
  jobs: readonly JobRingInput[];
  connected: boolean | null;
  muted: boolean;
  night: boolean;
  animating: boolean;
  frameCap: number | null;
}

const NO_SCENE: SceneStats = {
  tier: -1,
  tierName: 'SPHERE',
  pinned: false,
  onBattery: false,
  changes: 0,
  points: 0,
  frozen: false,
  galaxy: 0,
  gpuTimer: false,
  format: '',
  overlays: 'shader',
  callouts: [],
  unit: 0,
  shellPx: 0,
  bootDone: true,
};

export function createOrbEngine(options: PlasmaEngineOptions): OrbEngine {
  const replay: Replay = { placement: null, followers: null, compact: false, grown: false, jobs: [], connected: null, muted: false, night: false, animating: true, frameCap: null };
  let scene: (PlasmaEngine & Partial<SceneExtras>) | null = null;
  let kind: 'ultron' | 'sphere' = 'sphere';

  const toSphere = (why: string, freshCanvas: boolean): PlasmaEngine => {
    let canvas = options.canvas;
    if (freshCanvas) {
      const next = document.createElement('canvas');
      next.className = options.canvas.className;
      next.setAttribute('aria-hidden', 'true');
      options.canvas.parentElement?.insertBefore(next, options.canvas.nextSibling);
      options.canvas.style.display = 'none';
      canvas = next;
    }
    window.tessa?.reportMetrics?.(`SCENE fallback to today's sphere: ${why}`);
    const sphere = createPlasmaEngine({ ...options, canvas });
    kind = 'sphere';
    if (replay.placement) sphere.setPlacement(replay.placement[0], replay.placement[1], replay.placement[2], true);
    if (replay.followers) sphere.setFollowers(replay.followers);
    sphere.setCompact(replay.compact);
    if (replay.grown) sphere.grow();
    sphere.setJobs(replay.jobs);
    if (replay.connected !== null) sphere.setConnected(replay.connected);
    sphere.setMuted(replay.muted);
    sphere.setNight(replay.night);
    sphere.setFrameCap(replay.frameCap);
    sphere.setAnimating(replay.animating);
    return sphere;
  };

  if (options.forceFallback) {
    scene = toSphere('forced by --force-fallback', false);
  } else {
    try {
      scene = createUltronEngine({
        ...options,
        onPath: (path, reason) => {
          options.onPath?.(path, reason);
          if (path === 'fallback' && kind === 'ultron') {
            window.setTimeout(() => {
              const old = scene;
              scene = toSphere(reason, true);
              old?.dispose();
            }, 0);
          }
        },
      });
      kind = 'ultron';
    } catch (err) {
      scene = toSphere(String((err as Error)?.message ?? err).slice(0, 200), true);
    }
  }

  const s = (): PlasmaEngine & Partial<SceneExtras> => scene as PlasmaEngine & Partial<SceneExtras>;

  return {
    scene: () => kind,
    setPlacement(x, y, r, snap) {
      replay.placement = [x, y, r];
      s().setPlacement(x, y, r, snap);
    },
    setCompact(on) {
      replay.compact = on;
      s().setCompact(on);
    },
    grow() {
      replay.grown = true;
      s().grow();
    },
    setFollowers(f) {
      replay.followers = f;
      s().setFollowers(f);
    },
    setCentreOffset: (x, y) => s().setCentreOffset(x, y),
    setFit: (f) => s().setFit(f),
    retint: () => s().retint(),
    beat: () => s().beat(),
    setLoad: (c, m) => s().setLoad(c, m),
    setConnected(on) {
      replay.connected = on;
      s().setConnected(on);
    },
    setJobs(jobs) {
      replay.jobs = jobs;
      s().setJobs(jobs);
    },
    setVoiceLevel: (level) => s().setVoiceLevel(level),
    setMuted(on) {
      replay.muted = on;
      s().setMuted(on);
    },
    setNight(on) {
      replay.night = on;
      s().setNight(on);
    },
    flare: () => s().flare(),
    noteStateChange: (at, tau) => s().noteStateChange(at, tau),
    mark: (label, t0, budget) => s().mark(label, t0, budget),
    setFrameCap(fps) {
      replay.frameCap = fps;
      s().setFrameCap(fps);
    },
    reprobeRefresh: () => s().reprobeRefresh(),
    setAnimating(on) {
      replay.animating = on;
      s().setAnimating(on);
    },
    loseContextForTest: (ms) => s().loseContextForTest(ms),
    stats: () => s().stats(),
    takeIntervals: () => s().takeIntervals(),
    traceVoice: (on) => s().traceVoice(on),
    takeVoiceTrace: () => s().takeVoiceTrace(),
    dispose: () => s().dispose(),
    setStage: (box: Box | null) => s().setStage?.(box),
    setCallouts: (els) => s().setCallouts?.(els),
    setPlanetSources: (src: PlanetSources) => s().setPlanetSources?.(src),
    setOnBattery: (on) => s().setOnBattery?.(on),
    pinQuality: (tier) => s().pinQuality?.(tier),
    setOverlays: (inShader) => s().setOverlays?.(inShader),
    push: (x, y, strength) => s().push?.(x, y, strength),
    sceneStats: () => s().sceneStats?.() ?? NO_SCENE,
  };
}
