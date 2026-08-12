/**
 * Step 0 smoke test — @lydell/node-pty on this machine, in PLAIN NODE.
 *
 * This is NOT the hard gate. Node-API is ABI-stable, so a plain-Node load
 * essentially cannot fail for ABI reasons, and plain Node cannot exercise the
 * failure mode that actually threatens the design (node-pty constructs a
 * worker_threads Worker for its Windows conout connection, and Electron's
 * utilityProcess has historically thrown on `new Worker()`).
 *
 * What this DOES prove, cheaply, before ~250 MB of Electron is downloaded:
 *   1. the prebuilt conpty.node loads at all
 *   2. the platform-specific optional dependency resolved
 *   3. ConPTY on THIS build (Windows 11 22631) can spawn, echo, RESIZE and exit
 *      without hanging — 22631 predates several ConPTY fixes, so resize is
 *      exercised deliberately rather than assumed
 *
 * Every phase is wrapped in a timeout: a hang is a documented ConPTY failure
 * mode, and a test that hangs forever reports nothing.
 *
 * Run: node apps/console/scripts/smoke-pty.mjs
 */

import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

const TIMEOUT_MS = 15_000;
let failures = 0;

const ok = (name, detail = '') => console.log(`  ok    ${name}${detail ? `  ${detail}` : ''}`);
const bad = (name, detail = '') => {
  failures++;
  console.log(`  FAIL  ${name}${detail ? `  ${detail}` : ''}`);
};

function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(`TIMEOUT after ${ms}ms: ${label}`)), ms).unref(),
    ),
  ]);
}

console.log('\nStep 0 — @lydell/node-pty smoke test (plain Node)\n');

// ── 1. does the prebuilt load ────────────────────────────────────────────────
let pty;
const t0 = process.hrtime.bigint();
try {
  pty = require('@lydell/node-pty');
  const loadMs = Number(process.hrtime.bigint() - t0) / 1e6;
  ok('prebuilt conpty.node loads', `(${loadMs.toFixed(0)} ms)`);
} catch (err) {
  bad('prebuilt conpty.node loads', err.message);
  console.log('\nSTOP. The binary did not load — do not download Electron.\n');
  process.exit(1);
}

console.log(`  info  node ${process.version}  napi=${process.versions.napi ?? 'n/a'}  ${process.platform}/${process.arch}`);

// ── 2. spawn, echo, resize, exit ─────────────────────────────────────────────
const run = () =>
  new Promise((resolve, reject) => {
    let out = '';
    let resized = false;
    let term;

    try {
      term = pty.spawn('cmd.exe', ['/Q'], {
        name: 'xterm-color',
        cols: 80,
        rows: 24,
        cwd: process.env.USERPROFILE || process.cwd(),
        env: process.env,
      });
    } catch (err) {
      return reject(new Error(`spawn threw: ${err.message}`));
    }

    ok('cmd.exe spawned', `pid=${term.pid}`);

    const firstByteAt = { t: null };
    term.onData((d) => {
      if (firstByteAt.t === null) firstByteAt.t = process.hrtime.bigint();
      out += d;

      // Once the shell is clearly alive, exercise resize — the 22631 risk.
      if (!resized && out.includes('ZOEY_PTY_OK')) {
        resized = true;
        try {
          term.resize(120, 40);
          ok('pty.resize(120,40) did not throw or hang');
        } catch (err) {
          bad('pty.resize', err.message);
        }
        setTimeout(() => term.write('exit\r'), 250);
      }
    });

    term.onExit(({ exitCode, signal }) => {
      resolve({ out, exitCode, signal, firstByteAt: firstByteAt.t });
    });

    // Marker chosen so it cannot collide with the echoed command line itself.
    term.write('echo ZOEY_PTY%_%OK\r');
    setTimeout(() => term.write('echo ZOEY_PTY_OK\r'), 400);
  });

try {
  const t1 = process.hrtime.bigint();
  const res = await withTimeout(run(), TIMEOUT_MS, 'spawn/echo/resize/exit cycle');
  const totalMs = Number(process.hrtime.bigint() - t1) / 1e6;

  if (res.firstByteAt) {
    const ttfb = Number(res.firstByteAt - t1) / 1e6;
    ok('first byte received', `(${ttfb.toFixed(0)} ms)`);
  } else {
    bad('first byte received', 'no data ever arrived');
  }

  if (res.out.includes('ZOEY_PTY_OK')) ok('echo round-tripped through the pty');
  else bad('echo round-tripped', `output did not contain the marker (${res.out.length} bytes)`);

  if (res.exitCode === 0) ok('clean exit', `code=${res.exitCode}`);
  else bad('clean exit', `code=${res.exitCode} signal=${res.signal}`);

  ok('full cycle completed without hanging', `(${totalMs.toFixed(0)} ms)`);
} catch (err) {
  bad('spawn/echo/resize/exit cycle', err.message);
  if (String(err.message).startsWith('TIMEOUT')) {
    console.log('\n  A hang here is the known ConPTY defect class on builds predating the fixes.');
    console.log('  Escape hatch: @lydell/node-pty 1.2.0-beta.x bundles its own conpty.dll/OpenConsole.exe.');
  }
}

console.log(failures === 0 ? '\nStep 0 PASSED — safe to proceed to Step 1.\n' : `\n${failures} failure(s) — do NOT download Electron yet.\n`);
process.exit(failures === 0 ? 0 : 1);
