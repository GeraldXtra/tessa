import type { AgentState } from '@tessa/protocol';

import type { JobRingInput, PlasmaEngine } from './scene/plasma-engine.ts';
import { STATE_ORDER, TAU_INTERRUPT_S, TAU_WAKE_S } from './scene/plasma-model.ts';
import { devVisualStore } from './state/plasma-inputs.ts';
import { agentStateStore } from './state/store.ts';
import { THEME_IDS, applyTheme, currentTheme, type ThemeId } from './theme.ts';

export interface DevKeysOptions {
  engine: PlasmaEngine;
  report: (line: string) => void;
  toggleOverlay: () => void;
}

const MAX_DEV_JOBS = 4;

function editable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

export function installDevKeys({ engine, report, toggleOverlay }: DevKeysOptions): () => void {
  let cap30 = false;
  let jobSeq = 0;
  let reelToken = 0;
  let reelOn = false;
  const timers = new Set<number>();

  const patch = (next: Partial<ReturnType<typeof devVisualStore.get>>): void => {
    devVisualStore.set({ ...devVisualStore.get(), ...next });
  };

  function go(next: AgentState, t0: number, label: string, budget: number, tau?: number): boolean {
    if (agentStateStore.get() === next) {
      report(`DEVKEY ${label}: already ${next}`);
      return false;
    }
    const prev = agentStateStore.get();
    agentStateStore.set(next);
    engine.noteStateChange(t0, tau);
    engine.mark(label, t0, budget);
    if (next === 'blocked') holdNewestJob(true);
    else if (prev === 'blocked') holdNewestJob(false);
    report(`DEVKEY ${label} -> ${next}`);
    return true;
  }

  function wake(t0: number): void {
    const d = devVisualStore.get();
    if (d.disconnected) return report('DEVKEY Wake ignored: disconnected');
    if (d.muted) return report('DEVKEY Wake ignored: muted');
    if (agentStateStore.get() === 'blocked') return report('DEVKEY Wake ignored: blocked');
    go('listening', t0, 'Wake', 200, TAU_WAKE_S);
  }

  function interrupt(t0: number): void {
    if (agentStateStore.get() !== 'speaking') return report('DEVKEY Interrupt ignored: not speaking');
    go('listening', t0, 'Interrupted', 120, TAU_INTERRUPT_S);
  }

  function threat(t0: number): void {
    engine.flare();
    engine.mark('Threat flare', t0, 0);
    report('DEVKEY Threat flare');
  }

  function toggleDisconnected(t0: number): void {
    const off = !devVisualStore.get().disconnected;
    patch({ disconnected: off });
    engine.mark(off ? 'Disconnected' : 'Reconnected', t0, 0);
    report(`DEVKEY ${off ? 'Disconnected' : 'Reconnected'} (visual only, the socket is untouched)`);
  }

  function setMuted(on: boolean, t0: number): void {
    patch({ muted: on });
    if (on && agentStateStore.get() === 'listening') agentStateStore.set('idle');
    engine.mark(on ? 'Muted' : 'Unmuted', t0, 0);
    report(`DEVKEY ${on ? 'Muted' : 'Unmuted'}`);
  }

  function summon(t0: number): void {
    if (!devVisualStore.get().muted) return report('DEVKEY Summon ignored: not muted');
    patch({ muted: false });
    go('listening', t0, 'Summoned', 200, TAU_WAKE_S);
  }

  function setNight(on: boolean, t0: number): void {
    patch({ night: on });
    engine.mark(on ? 'Night on' : 'Night off', t0, 0);
    report(`DEVKEY Night ${on ? 'on' : 'off'}`);
  }

  function updateJob(id: string, change: Partial<JobRingInput> | null): void {
    const jobs = devVisualStore.get().jobs;
    patch({ jobs: change === null ? jobs.filter((j) => j.id !== id) : jobs.map((j) => (j.id === id ? { ...j, ...change } : j)) });
  }

  function addJob(t0: number, durS?: number): string | null {
    const jobs = devVisualStore.get().jobs.filter((j) => j.finished === 'none');
    if (jobs.length >= MAX_DEV_JOBS) {
      report('DEVKEY Job refused: four rings is the most the Orb shows');
      return null;
    }
    jobSeq += 1;
    const id = `dev-job-${jobSeq}`;
    const dur = durS ?? 5 + (jobSeq % 6);
    patch({ jobs: [...devVisualStore.get().jobs, { id, progress: 0, waiting: false, finished: 'none' }] });
    let progress = 0;
    const timer = window.setInterval(() => {
      const job = devVisualStore.get().jobs.find((j) => j.id === id);
      if (!job) {
        window.clearInterval(timer);
        timers.delete(timer);
        return;
      }
      if (job.waiting) return;
      progress = Math.min(1, progress + 0.1 / dur);
      if (progress >= 1) {
        window.clearInterval(timer);
        timers.delete(timer);
        updateJob(id, { progress: 1, finished: 'ok' });
        const done = window.setTimeout(() => {
          timers.delete(done);
          updateJob(id, null);
        }, 2000);
        timers.add(done);
        return;
      }
      updateJob(id, { progress });
    }, 100);
    timers.add(timer);
    engine.mark('Job added', t0, 0);
    report(`DEVKEY Job added ${id} (${dur}s, visual only)`);
    return id;
  }

  function holdNewestJob(on: boolean): void {
    const live = devVisualStore.get().jobs.filter((j) => j.finished === 'none');
    const target = on ? live[live.length - 1] : undefined;
    patch({ jobs: devVisualStore.get().jobs.map((j) => ({ ...j, waiting: on && target !== undefined && j.id === target.id })) });
  }

  function setTheme(id: ThemeId, t0: number): void {
    applyTheme(id);
    engine.retint();
    engine.mark(`Theme ${id}`, t0, 0);
  }

  function stopReel(): void {
    if (!reelOn) return;
    reelOn = false;
    reelToken += 1;
    report('DEVKEY Reel stopped');
  }

  async function runReel(token: number): Promise<void> {
    const ok = (): boolean => reelOn && token === reelToken;
    const wait = (ms: number): Promise<boolean> =>
      new Promise((resolve) => {
        const t = window.setTimeout(() => {
          timers.delete(t);
          resolve(ok());
        }, ms);
        timers.add(t);
      });
    const now = (): number => performance.now();
    const startTheme = currentTheme();
    patch({ disconnected: false, muted: false, night: false, jobs: [] });
    go('idle', now(), 'Reel: idle', 0);
    if (!(await wait(4200))) return;
    wake(now());
    if (!(await wait(2600))) return;
    go('thinking', now(), 'Reel: thinking', 0);
    if (!(await wait(3300))) return;
    go('speaking', now(), 'Reel: speaking', 0);
    if (!(await wait(3000))) return;
    go('working', now(), 'Reel: working', 0);
    addJob(now(), 3);
    if (!(await wait(800))) return;
    addJob(now(), 6);
    if (!(await wait(4600))) return;
    go('speaking', now(), 'Reel: speaking', 0);
    if (!(await wait(1900))) return;
    interrupt(now());
    if (!(await wait(2400))) return;
    go('thinking', now(), 'Reel: thinking', 0);
    if (!(await wait(1900))) return;
    go('working', now(), 'Reel: working', 0);
    addJob(now(), 40);
    if (!(await wait(2500))) return;
    go('blocked', now(), 'Reel: blocked', 0);
    if (!(await wait(5200))) return;
    go('working', now(), 'Reel: approved', 0);
    if (!(await wait(3400))) return;
    threat(now());
    if (!(await wait(2700))) return;
    go('idle', now(), 'Reel: idle', 0);
    patch({ jobs: [] });
    if (!(await wait(2000))) return;
    setMuted(true, now());
    if (!(await wait(2100))) return;
    wake(now());
    if (!(await wait(2000))) return;
    summon(now());
    if (!(await wait(2400))) return;
    setNight(true, now());
    go('speaking', now(), 'Reel: speaking at night', 0);
    if (!(await wait(3000))) return;
    setNight(false, now());
    go('idle', now(), 'Reel: idle', 0);
    if (!(await wait(800))) return;
    toggleDisconnected(now());
    if (!(await wait(3900))) return;
    toggleDisconnected(now());
    if (!(await wait(2800))) return;
    const start = THEME_IDS.indexOf(startTheme);
    for (let i = 1; i <= THEME_IDS.length; i++) {
      const id = THEME_IDS[(start + i) % THEME_IDS.length];
      if (!id) continue;
      setTheme(id, now());
      if (!(await wait(i === THEME_IDS.length ? 1300 : 1100))) {
        setTheme(startTheme, now());
        return;
      }
    }
    stopReel();
  }

  function onKey(event: KeyboardEvent): void {
    if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return;
    if (editable(event.target)) return;
    const key = (event.key || '').toLowerCase();
    const t0 = event.timeStamp;
    const digit = /^Digit([1-6])$/.exec(event.code)?.[1] ?? (/^[1-6]$/.test(key) ? key : null);
    let handled = true;
    if (digit) {
      stopReel();
      const next = STATE_ORDER[Number.parseInt(digit, 10) - 1];
      if (next) go(next, t0, next, 0);
    } else {
      switch (key) {
        case 'w':
          stopReel();
          wake(t0);
          break;
        case 'i':
          stopReel();
          interrupt(t0);
          break;
        case 't':
          stopReel();
          threat(t0);
          break;
        case 'd':
          stopReel();
          toggleDisconnected(t0);
          break;
        case 'j':
          stopReel();
          addJob(t0);
          break;
        case 'm':
          stopReel();
          setMuted(!devVisualStore.get().muted, t0);
          break;
        case 's':
          if (devVisualStore.get().muted) {
            stopReel();
            summon(t0);
          } else handled = false;
          break;
        case 'n':
          stopReel();
          setNight(!devVisualStore.get().night, t0);
          break;
        case 'h':
          toggleOverlay();
          break;
        case 'k':
          cap30 = !cap30;
          engine.setFrameCap(cap30 ? 30 : null);
          report(`DEVKEY frame cap ${cap30 ? '30 fps' : 'off'}`);
          break;
        case 'p':
          if (reelOn) stopReel();
          else {
            reelOn = true;
            reelToken += 1;
            report('DEVKEY Reel started (visual only: no words, no voice, no load)');
            void runReel(reelToken);
          }
          break;
        case 'escape':
          if (reelOn) stopReel();
          else handled = false;
          break;
        default:
          handled = false;
      }
    }
    if (handled) event.preventDefault();
  }

  window.addEventListener('keydown', onKey);
  report('DEVKEY installed: 1-6 states, W I T D J M S N H K P (A and X are not bound)');
  return () => {
    window.removeEventListener('keydown', onKey);
    reelToken += 1;
    reelOn = false;
    for (const t of timers) {
      window.clearInterval(t);
      window.clearTimeout(t);
    }
    timers.clear();
  };
}
