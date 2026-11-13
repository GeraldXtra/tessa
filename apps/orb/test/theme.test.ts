import { strict as assert } from 'node:assert';
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import tokens from '../../../packages/tokens/tokens.json';
import { SWITCH_TO, loadTheme, saveTheme, themeFileAllowed } from '../src/main/theme-state.ts';
import { DEFAULT_THEME, THEME_IDS, themeForKey } from '../src/renderer/theme.ts';
import { luminance, scenePalette, sceneLift } from '../src/renderer/scene/ultron/model.ts';

const ORB = process.env['TESSA_ORB_DIR'] ?? join(__dirname, '..');
const REPO = join(ORB, '..', '..');
const STEPS = ['core', 'body', 'idle', 'deep', 'mid', 'hot', 'bg'];
const table = (tokens as unknown as { color: Record<string, { value: string }> }).color;

function rgb(value: string): [number, number, number] {
  const n = Number.parseInt(value.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function lab(c: [number, number, number]): [number, number, number] {
  const lin = (v: number): number => {
    const x = v / 255;
    return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
  };
  const [r, g, b] = c.map(lin) as [number, number, number];
  const X = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047;
  const Y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b;
  const Z = (0.0193339 * r + 0.119192 * g + 0.9503041 * b) / 1.08883;
  const f = (t: number): number => (t > (6 / 29) ** 3 ? Math.cbrt(t) : t / (3 * (6 / 29) ** 2) + 4 / 29);
  return [116 * f(Y) - 16, 500 * (f(X) - f(Y)), 200 * (f(Y) - f(Z))];
}

function de2000(a: [number, number, number], b: [number, number, number]): number {
  const [L1, a1, b1] = a;
  const [L2, a2, b2] = b;
  const rad = Math.PI / 180;
  const C1 = Math.hypot(a1, b1);
  const C2 = Math.hypot(a2, b2);
  const Cb = (C1 + C2) / 2;
  const G = 0.5 * (1 - Math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)));
  const a1p = (1 + G) * a1;
  const a2p = (1 + G) * a2;
  const C1p = Math.hypot(a1p, b1);
  const C2p = Math.hypot(a2p, b2);
  const h1p = ((Math.atan2(b1, a1p) / rad) + 360) % 360;
  const h2p = ((Math.atan2(b2, a2p) / rad) + 360) % 360;
  let dh = h2p - h1p;
  if (C1p * C2p === 0) dh = 0;
  else if (dh > 180) dh -= 360;
  else if (dh < -180) dh += 360;
  const dL = L2 - L1;
  const dC = C2p - C1p;
  const dH = 2 * Math.sqrt(C1p * C2p) * Math.sin((dh / 2) * rad);
  const Lb = (L1 + L2) / 2;
  const Cbp = (C1p + C2p) / 2;
  let hb = h1p + h2p;
  if (C1p * C2p !== 0) hb = Math.abs(h1p - h2p) <= 180 ? (h1p + h2p) / 2 : h1p + h2p < 360 ? (h1p + h2p + 360) / 2 : (h1p + h2p - 360) / 2;
  const T = 1 - 0.17 * Math.cos((hb - 30) * rad) + 0.24 * Math.cos(2 * hb * rad) + 0.32 * Math.cos((3 * hb + 6) * rad) - 0.2 * Math.cos((4 * hb - 63) * rad);
  const dth = 30 * Math.exp(-(((hb - 275) / 25) ** 2));
  const Rc = 2 * Math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7));
  const Sl = 1 + (0.015 * (Lb - 50) ** 2) / Math.sqrt(20 + (Lb - 50) ** 2);
  const Sc = 1 + 0.045 * Cbp;
  const Sh = 1 + 0.015 * Cbp * T;
  const Rt = -Math.sin(2 * dth * rad) * Rc;
  return Math.sqrt((dL / Sl) ** 2 + (dC / Sc) ** 2 + (dH / Sh) ** 2 + Rt * (dC / Sc) * (dH / Sh));
}

test('the colour difference matches the published CIEDE2000 test pairs', () => {
  assert.equal(de2000([50, 2.6772, -79.7751], [50, 0, -82.7485]).toFixed(4), '2.0425');
  assert.equal(de2000([50, 2.5, 0], [73, 25, -18]).toFixed(4), '27.1492');
});

test('tokens exist for all seven themes, in tokens.json and the generated css', () => {
  assert.deepEqual([...THEME_IDS], ['gold', 'magenta', 'cyan', 'violet', 'emerald', 'red', 'ultron']);
  const css = readFileSync(join(REPO, 'packages', 'tokens', 'dist', 'tokens.css'), 'utf8');
  for (const id of THEME_IDS) {
    for (const step of STEPS) {
      const name = `theme-${id}-${step}`;
      assert.ok(table[name], `${name} in tokens.json`);
      assert.ok(css.includes(`--${name}:`), `--${name} in tokens.css`);
    }
  }
});

test('the approval amber is unchanged, and the Orb still takes its amber from it', () => {
  assert.deepEqual(rgb(table['blocked-mid']!.value), [255, 162, 26]);
  assert.deepEqual(rgb(table['blocked-deep']!.value), [74, 35, 0]);
  assert.deepEqual(rgb(table['blocked-hot']!.value), [255, 228, 181]);
  const css = readFileSync(join(ORB, 'src', 'renderer', 'styles', 'orb.css'), 'utf8');
  assert.ok(css.includes('--amber: var(--blocked-mid);'));
});

test('every ultron step is at least 15 CIEDE2000 from the approval amber (and from the brief\'s 255,163,26)', () => {
  const amber = lab(rgb(table['blocked-mid']!.value));
  const brief = lab([255, 163, 26]);
  for (const step of ['core', 'body', 'idle', 'deep', 'mid', 'hot', 'bg']) {
    const c = lab(rgb(table[`theme-ultron-${step}`]!.value));
    assert.ok(de2000(c, amber) >= 15, `${step} ${de2000(c, amber).toFixed(2)}`);
    assert.ok(de2000(c, brief) >= 15, `${step} vs brief ${de2000(c, brief).toFixed(2)}`);
  }
  const body = de2000(lab(rgb(table['theme-ultron-body']!.value)), amber);
  assert.ok(body > 16 && body < 17, body.toFixed(2));
});

test('ultron is the default and sits on Ctrl+Shift+7, the six old keys unchanged', () => {
  assert.equal(DEFAULT_THEME, 'ultron');
  assert.equal(themeForKey('Digit1'), 'gold');
  assert.equal(themeForKey('Digit6'), 'red');
  assert.equal(themeForKey('Digit7'), 'ultron');
  assert.equal(themeForKey('', 55), 'ultron');
  assert.equal(themeForKey('Digit8'), null);
});

test('the scene palette lifts dark oranges to a common brightness and leaves bright ones alone', () => {
  const p = scenePalette({ core: [1, 0.79, 0.63], body: [1, 0.447, 0.11], idle: [0.78, 0.31, 0.05], deep: [0.29, 0.11, 0.02] });
  assert.ok(Math.abs(luminance(p.bright) - 0.48) < 0.01);
  assert.equal(sceneLift([1, 0.7, 0]), 1);
  assert.equal(sceneLift([0.01, 0.01, 0.01]), 1.7);
});

function withHome(fn: (dir: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), 'orb-theme-test-'));
  const was = process.env['LOCALAPPDATA'];
  process.env['LOCALAPPDATA'] = dir;
  try {
    fn(dir);
  } finally {
    if (was === undefined) delete process.env['LOCALAPPDATA'];
    else process.env['LOCALAPPDATA'] = was;
    rmSync(dir, { recursive: true, force: true });
  }
}

test('his stored theme switches to ultron once, keeps a note of what it was, and his later choice sticks', () => {
  withHome((dir) => {
    const file = join(dir, 'Tessa', 'orb-theme.json');
    mkdirSync(join(dir, 'Tessa'), { recursive: true });
    writeFileSync(file, `${JSON.stringify({ theme: 'gold', v: 2 })}\n`);
    const first = loadTheme();
    assert.equal(first.theme, SWITCH_TO);
    assert.equal(first.migrated, true);
    assert.equal(first.was, 'gold');
    assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), { theme: 'ultron', v: 3, was: 'gold' });
    const again = loadTheme();
    assert.equal(again.theme, 'ultron');
    assert.equal(again.migrated, false);
    saveTheme('cyan');
    assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), { theme: 'cyan', v: 3, was: 'gold' });
    const later = loadTheme();
    assert.equal(later.theme, 'cyan');
    assert.equal(later.migrated, false);
  });
});

test('an unversioned file switches too; no file means the ultron default and nothing written', () => {
  withHome((dir) => {
    const file = join(dir, 'Tessa', 'orb-theme.json');
    const none = loadTheme();
    assert.equal(none.theme, 'ultron');
    assert.equal(existsSync(file), false);
    mkdirSync(join(dir, 'Tessa'), { recursive: true });
    writeFileSync(file, '{"theme":"magenta"}');
    const r = loadTheme();
    assert.equal(r.theme, 'ultron');
    assert.equal(r.was, 'magenta');
  });
});

test('dev and test launches never read or write the real theme file', () => {
  assert.equal(themeFileAllowed(true, false), false);
  assert.equal(themeFileAllowed(true, true), true);
  assert.equal(themeFileAllowed(false, false), true);
  const src = readFileSync(join(ORB, 'src', 'main', 'index.ts'), 'utf8');
  assert.ok(src.includes('const themeLoad = !themeFileAllowed(instrumented, themePersist)'));
  assert.ok(src.includes('if (!themeFileAllowed(instrumented, themePersist)) {'));
  assert.ok(src.includes("const themePersist = isDev && process.argv.includes('--dev-theme-persist') && throwawayAppData;"));
  assert.equal(/instrumented && !themePersist/.test(src), false);
});
