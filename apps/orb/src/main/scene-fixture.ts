export interface SceneStep {
  state: string;
  ms: number;
}

export interface SceneFixture {
  steps: SceneStep[];
  loop: boolean;
  levelHz: number;
}

export const SCENE_FLAG = '--fixture-scene=';

export function sceneFixtureFlag(argv: readonly string[], isDev: boolean, states: readonly string[]): SceneFixture | null {
  if (!isDev) return null;
  const flag = argv.find((a) => a.startsWith(SCENE_FLAG));
  if (!flag) return null;
  const steps: SceneStep[] = [];
  let loop = false;
  let levelHz = 30;
  for (const part of flag.slice(SCENE_FLAG.length).split(',')) {
    const p = part.trim();
    if (!p) continue;
    if (p === 'loop') {
      loop = true;
      continue;
    }
    const hz = /^hz:(\d{1,3})$/.exec(p);
    if (hz) {
      levelHz = Math.max(1, Math.min(120, Number(hz[1])));
      continue;
    }
    const [state, ms] = p.split(':');
    if (!state || !states.includes(state)) continue;
    const n = ms === undefined ? 0 : Number.parseInt(ms, 10);
    steps.push({ state, ms: Number.isFinite(n) && n > 0 ? Math.min(n, 3_600_000) : 0 });
  }
  return steps.length > 0 ? { steps, loop, levelHz } : null;
}

export function sceneLevel(t: number): number {
  const syllable = Math.abs(Math.sin(t * 2 * Math.PI * 4.1));
  const phrase = 0.5 + 0.5 * Math.sin(t * 2 * Math.PI * 0.43 + 1.1);
  const breath = Math.sin(t * 2 * Math.PI * 0.21) > -0.6 ? 1 : 0.06;
  const v = (0.1 + 0.78 * syllable * (0.35 + 0.65 * phrase)) * breath;
  return Math.max(0, Math.min(1, v));
}

export type SceneSend = (type: string, payload: Record<string, unknown>) => void;

export function playScene(fx: SceneFixture, send: SceneSend, log: (line: string) => void): () => void {
  let index = 0;
  let stepTimer: ReturnType<typeof setTimeout> | null = null;
  let levelTimer: ReturnType<typeof setInterval> | null = null;
  let seq = 0;
  const stopLevel = (): void => {
    if (!levelTimer) return;
    clearInterval(levelTimer);
    levelTimer = null;
    seq += 1;
    send('evt.voice.amplitude', { companionId: 'tessa', level: 0, seq });
  };
  const startLevel = (): void => {
    stopLevel();
    const start = Date.now();
    levelTimer = setInterval(() => {
      seq += 1;
      send('evt.voice.amplitude', { companionId: 'tessa', level: sceneLevel((Date.now() - start) / 1000), seq });
    }, 1000 / fx.levelHz);
  };
  const next = (): void => {
    if (index >= fx.steps.length) {
      if (!fx.loop) return;
      index = 0;
    }
    const step = fx.steps[index] as SceneStep;
    index += 1;
    log(`SCENE-FIXTURE state=${step.state} for ${step.ms > 0 ? `${step.ms} ms` : 'ever'} wall=${Date.now()} (a fixture state, not hers)`);
    send('evt.agent.state', { companionId: 'tessa', state: step.state });
    if (step.state === 'speaking') startLevel();
    else stopLevel();
    if (step.ms > 0) stepTimer = setTimeout(next, step.ms);
  };
  next();
  return () => {
    if (stepTimer) clearTimeout(stepTimer);
    stopLevel();
  };
}
