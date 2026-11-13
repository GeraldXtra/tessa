import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { SCENE_FLAG, playScene, sceneFixtureFlag, sceneLevel } from '../src/main/scene-fixture.ts';
import { placeCallouts, boxOf } from '../src/renderer/scene/ultron/callouts.ts';
import { FrameLoop } from '../src/renderer/scene/ultron/loop.ts';
import { advanceClocks, newClocks, pace, settledStill, stepMix, targetMix, voiceTarget, WorkingDwell } from '../src/renderer/scene/ultron/model.ts';
import { NO_DATA, PLANETS, planetStatus } from '../src/renderer/scene/ultron/planets.ts';
import { GOVERNOR, LOW, QualityGovernor, TIERS, tierFromName } from '../src/renderer/scene/ultron/quality.ts';

const ORB = process.env['TESSA_ORB_DIR'] ?? join(__dirname, '..');
const STATES = ['idle', 'listening', 'thinking', 'speaking', 'working', 'blocked'];

test('blocked has no motion: every clock stands still once the blend reaches blocked', () => {
  const mix = targetMix('idle', false);
  const goal = targetMix('blocked', false);
  for (let i = 0; i < 400; i++) stepMix(mix, goal, 1 / 60, false);
  assert.equal(mix.bl, 1);
  assert.equal(pace(mix), 0);
  assert.ok(settledStill(mix, goal));
  const k = newClocks();
  advanceClocks(k, 1 / 60, targetMix('idle', false), 0, false);
  const before = JSON.stringify(k);
  for (let i = 0; i < 600; i++) advanceClocks(k, 1 / 60, mix, 1, false);
  assert.equal(JSON.stringify(k), before);
});

test('every other state keeps moving and never counts as still', () => {
  for (const s of ['idle', 'listening', 'thinking', 'speaking', 'working'] as const) {
    const mix = targetMix(s, s === 'working');
    const k = newClocks();
    advanceClocks(k, 0.5, mix, 0.5, false);
    assert.ok(k.clock > 0 && k.flow > 0, s);
    assert.equal(settledStill(mix, targetMix(s, s === 'working')), false, s);
  }
});

test('her voice level drives the orb only while she speaks, and only while fresh', () => {
  assert.equal(voiceTarget('speaking', 0.8, 100, false), 0.8);
  assert.equal(voiceTarget('speaking', 0.8, 900, false), 0);
  assert.equal(voiceTarget('listening', 0.8, 100, false), 0);
  assert.equal(voiceTarget('speaking', 0.8, 100, true), 0);
  assert.equal(voiceTarget('speaking', Number.NaN, 100, false), 0);
});

test('working opens the galaxy view only after a dwell, and leaves it after one', () => {
  const d = new WorkingDwell();
  assert.equal(d.update('working', 0), false);
  assert.equal(d.update('working', 800), false);
  assert.equal(d.update('working', 950), true);
  assert.equal(d.update('thinking', 1000), true);
  assert.equal(d.update('thinking', 2300), false);
});

test('a planet with no data source says NO DATA; the calendar shows only what the daemon sent', () => {
  for (const p of PLANETS) {
    assert.equal(planetStatus(p.key, { calendar: null, linkUp: true }).text, NO_DATA, p.key);
    if (p.key !== 'calendar') {
      const cal = { connected: true, events: [], stale: false, ageSeconds: 0, date: '2026-11-12' };
      assert.equal(planetStatus(p.key, { calendar: cal, linkUp: true }).text, NO_DATA, p.key);
    }
  }
  const off = planetStatus('calendar', { calendar: { connected: false, events: [], stale: false, ageSeconds: 0, date: '2026-11-12' }, linkUp: true });
  assert.deepEqual(off, { text: 'NOT CONNECTED', real: true });
  const on = planetStatus('calendar', { calendar: { connected: true, events: [{ start: 'x', title: 'y' } as never, { start: 'x', title: 'y' } as never], stale: false, ageSeconds: 0, date: '2026-11-12' }, linkUp: false });
  assert.deepEqual(on, { text: 'CONNECTED · 2 TODAY · STALE', real: true });
  assert.deepEqual(PLANETS.map((p) => p.name), ['PHONE', 'TELEGRAM', 'CALENDAR', 'X', 'FILES', 'CODE', 'BROWSER']);
});

test('callouts stay in the stage, off the orb, one per side', () => {
  const stage = { x0: 300, y0: 40, x1: 980, y1: 620 };
  const orb = { cx: 640, cy: 376, r: 209 };
  const planets = [
    { x: 420, y: 240, r: 9, vis: 1, front: true },
    { x: 900, y: 300, r: 11, vis: 1, front: true },
    { x: 640, y: 376, r: 12, vis: 1, front: true },
    { x: 1200, y: 300, r: 12, vis: 1, front: true },
  ];
  const slots = placeCallouts([null, null], planets, stage, orb, 0);
  for (const s of slots) {
    if (!s) continue;
    const b = boxOf(planets[s.planet]!, s.side);
    assert.ok(b.x0 >= stage.x0 && b.x1 <= stage.x1 && b.y0 >= stage.y0 && b.y1 <= stage.y1);
    assert.notEqual(s.planet, 2);
    assert.notEqual(s.planet, 3);
  }
  assert.ok(slots[0] === null || slots[1] === null || slots[0].planet !== slots[1].planet);
});

function feed(g: QualityGovernor, from: number, seconds: number, interval: number, gpu: number | null): number {
  let t = from;
  const end = from + seconds * 1000;
  while (t < end) {
    t += interval;
    g.frame(interval, gpu, t, 1000 / 60);
  }
  return t;
}

test('quality steps down when the GPU falls behind and back up when there is room', () => {
  const seen: number[] = [];
  const g = new QualityGovernor(GOVERNOR, (tier) => seen.push(tier));
  let t = feed(g, 0, 5, 1000 / 60, 6);
  assert.equal(g.tier, 0);
  t = feed(g, t, 6, 1000 / 60, 15.5);
  assert.ok(g.tier >= 1, `stepped down, tier ${g.tier}`);
  const low = g.tier;
  t = feed(g, t, 30, 1000 / 60, 15.5);
  assert.ok(g.tier >= low);
  t = feed(g, t, 60, 1000 / 60, 4);
  assert.ok(g.tier < low, `stepped back up, tier ${g.tier}`);
  t = feed(g, t, 120, 1000 / 60, 4);
  assert.equal(g.tier, 0);
  for (let i = 1; i < seen.length; i++) assert.equal(Math.abs((seen[i] ?? 0) - (seen[i - 1] ?? 0)), 1, 'one tier at a time');
  assert.ok(t > 0);
});

test('quality does not flicker: under a load that comes and goes it moves one way, settles between steps, and a failed raise waits longer', () => {
  const changes: [number, number][] = [];
  let now = 0;
  const g = new QualityGovernor(GOVERNOR, (tier) => changes.push([now, tier]));
  now = feed(g, 0, 4, 1000 / 60, 5);
  for (let i = 0; i < 120; i++) {
    const slow = i % 2 === 0;
    const end = now + 1000;
    while (now < end) {
      now += 1000 / 60;
      g.frame(1000 / 60, slow ? 15.8 : 5, now, 1000 / 60);
    }
  }
  for (let i = 1; i < changes.length; i++) {
    assert.ok((changes[i]![1] - changes[i - 1]![1]) * (changes[1]![1] - changes[0]![1]) > 0, 'no reversal while the load alternates');
    assert.ok(changes[i]![0] - changes[i - 1]![0] >= GOVERNOR.settleMs, 'each step waits for the last to settle');
  }
  assert.ok(changes.length <= TIERS.length - 1);
  const h = new QualityGovernor(GOVERNOR);
  let t = feed(h, 0, 4, 1000 / 60, 5);
  t = feed(h, t, 4, 1000 / 60, 15.5);
  const down = h.tier;
  assert.ok(down >= 1);
  t = feed(h, t, 12.5, 1000 / 60, 4);
  assert.equal(h.tier, down - 1);
  t = feed(h, t, 3, 1000 / 60, 15.5);
  assert.equal(h.tier, down, 'a raise that does not hold goes straight back');
  t = feed(h, t, 15, 1000 / 60, 4);
  assert.equal(h.tier, down, 'the next try waits longer than the first');
  t = feed(h, t, 10, 1000 / 60, 4);
  assert.equal(h.tier, down - 1);
  assert.ok(t > 0);
});

test('on battery the scene holds LOW, and returns to probing when the charger comes back', () => {
  const g = new QualityGovernor();
  let t = feed(g, 0, 5, 1000 / 60, 4);
  g.setBattery(true, t);
  assert.equal(g.tier, LOW);
  t = feed(g, t, 300, 1000 / 60, 2);
  assert.equal(g.tier, LOW);
  g.setBattery(false, t);
  t = feed(g, t, 200, 1000 / 60, 2);
  assert.equal(g.tier, 0);
  assert.equal(TIERS[LOW]?.name, 'LOW');
  assert.equal(TIERS[LOW]?.scale, 0.8);
  assert.equal(tierFromName('low'), LOW);
  assert.equal(tierFromName('high'), 0);
  assert.equal(tierFromName('bogus'), null);
});

test('a hidden or minimised window draws nothing, and drawing resumes when it shows again', () => {
  let hidden = true;
  const queue: ((ts: number) => void)[] = [];
  let ticks = 0;
  const loop = new FrameLoop(
    { request: (cb) => queue.push(cb), cancel: () => {}, hidden: () => hidden },
    () => {
      ticks += 1;
      return true;
    },
  );
  loop.start();
  assert.equal(queue.length, 0, 'no frame requested while hidden');
  hidden = false;
  loop.poke();
  assert.equal(queue.length, 1);
  queue.shift()!(16);
  assert.equal(ticks, 1);
  hidden = true;
  queue.shift()!(33);
  assert.equal(ticks, 1, 'a frame that lands while hidden draws nothing');
  assert.equal(queue.length, 0, 'and asks for no more');
  hidden = false;
  loop.poke();
  queue.shift()!(50);
  assert.equal(ticks, 2);
});

test('a still scene parks its loop and wakes on demand', () => {
  const queue: ((ts: number) => void)[] = [];
  let more = false;
  let ticks = 0;
  const loop = new FrameLoop({ request: (cb) => queue.push(cb), cancel: () => {}, hidden: () => false }, () => {
    ticks += 1;
    return more;
  });
  loop.start();
  queue.shift()!(1);
  assert.equal(queue.length, 0);
  assert.equal(loop.isRunning(), false);
  more = true;
  loop.poke();
  queue.shift()!(2);
  assert.equal(queue.length, 1);
  assert.equal(ticks, 2);
});

test('the scene fixture loads only with its dev flag, never in his normal start', () => {
  assert.equal(sceneFixtureFlag(['C:\\dev\\tessa\\node_modules\\electron\\dist\\electron.exe', 'C:\\dev\\tessa\\apps\\orb'], true, STATES), null);
  assert.equal(sceneFixtureFlag(['electron.exe', '.', `${SCENE_FLAG}speaking`], false, STATES), null);
  const fx = sceneFixtureFlag(['electron.exe', '.', `${SCENE_FLAG}idle:8000,bogus,speaking:9000,blocked,loop,hz:24`], true, STATES);
  assert.deepEqual(fx, { steps: [{ state: 'idle', ms: 8000 }, { state: 'speaking', ms: 9000 }, { state: 'blocked', ms: 0 }], loop: true, levelHz: 24 });
  const src = readFileSync(join(ORB, 'src', 'main', 'index.ts'), 'utf8');
  assert.ok(src.includes('const sceneFixture = sceneFixtureFlag(process.argv, isDev, AGENT_STATES);'));
  assert.equal(src.includes("startsWith('--fixture-scene=')"), false);
  const win = readFileSync(join(ORB, 'src', 'main', 'window.ts'), 'utf8');
  assert.ok(win.includes('const INSTRUMENTATION_FLAG = /^--(force|probe|capture|dev|fixture|stop|ptt)-/;'));
});

test('the fixture sends states through the link and a voice level only while speaking', () => {
  const sent: [string, Record<string, unknown>][] = [];
  const stop = playScene({ steps: [{ state: 'speaking', ms: 0 }], loop: false, levelHz: 50 }, (type, payload) => sent.push([type, payload]), () => {});
  assert.deepEqual(sent[0], ['evt.agent.state', { companionId: 'tessa', state: 'speaking' }]);
  stop();
  const last = sent[sent.length - 1]!;
  assert.equal(last[0], 'evt.voice.amplitude');
  assert.equal(last[1]['level'], 0);
  for (let t = 0; t < 20; t += 0.013) {
    const v = sceneLevel(t);
    assert.ok(v >= 0 && v <= 1);
  }
});
