// tcli-daemon.mjs — `tcli daemon status | start | stop | restart`. Node built-ins only.
//
// Reached through tcli.cmd (`tcli daemon <verb>`), or directly:
//   node C:\dev\tessa\apps\console\resources\tcli-daemon.mjs status
//
// THE RULES IT KEEPS (they are why it is shaped like this):
//   * LIVENESS IS PROVED, NEVER ASSUMED. `status` and `start` decide "running" with ONE
//     authenticated cmd.ping, not the pid alone — pids are reused after a reboot.
//   * IT CAN NEVER TRIP THE LOCKOUT. At most one handshake per run; the token is read from
//     runtime.json just before connecting; nothing connects when runtime.json's pid is dead
//     or no longer a python process. Five failed handshakes in 60 s disable the daemon's
//     listener until restart (CONTRACT §2.3) — that would lock the Orb out too.
//   * STOP IS A LOCAL REQUEST THAT PROVES THE TOKEN WITHOUT WRITING IT. stop-request.json
//     beside runtime.json carries an HMAC of the daemon's pid keyed with its token. No
//     network, no protocol change, nothing a web page can reach. The daemon audits it and
//     runs its clean shutdown; a request for any other pid is ignored.
//   * IT NEVER KILLS. If a daemon does not stop in time, it says so and leaves it running.
//   * IT NEVER OPENS THE DAEMON'S SINGLE-INSTANCE GUARD. The daemon alone decides that.
//   * start/restart launch through scripts\start-tessa.cmd (--now skips the sign-in audio
//     wait), detached, so the daemon outlives this process and its shell.
//
// Exit codes: 0 done / running, 1 failed, 3 not running (status, stop).

import { spawn, execFileSync } from 'node:child_process';
import { createHmac, randomBytes, createHash } from 'node:crypto';
import {
  closeSync, existsSync, openSync, readFileSync, readSync, readdirSync, renameSync, statSync, writeFileSync,
} from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, '..', '..', '..');
const LAUNCHER = join(REPO, 'scripts', 'start-tessa.cmd');
const LOG_DIR = join(REPO, 'data', 'logs');
// Mirrors core/security/runtime.py::local_appdata_root(). TESSA_RUNTIME_DIR is the daemon's
// TEST-ONLY isolation variable; honoured here for reading, refused for start/restart.
const RUNTIME_DIR = process.env.TESSA_RUNTIME_DIR || join(process.env.LOCALAPPDATA || '', 'Tessa');
const RUNTIME_FILE = join(RUNTIME_DIR, 'runtime.json');
const STOP_FILE = join(RUNTIME_DIR, 'stop-request.json');

// Measured 2026-10-06 on this machine (alive round, alive-NUMBERS.md):
//   clean stop WITH --voice: 353 ms -> wait 15 s (the floor), never kill.
//   start (--now) to runtime.json WITH --voice: ~25.5 s -> twice that.
const STOP_WAIT_MS = 60_000;
const READY_WAIT_MS = 120_000;
const PROGRESS_MS = 10_000;
const LAUNCH_SEEN_MS = 20_000;
const LAUNCH_TRIES = 3;
const LOG_FREE_MS = 15_000;
const HANDSHAKE_MS = 5_000;

const out = (s = '') => process.stdout.write(s + '\n');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const secs = (ms) => Math.round(ms / 1000);

function readRuntime() {
  if (!existsSync(RUNTIME_FILE)) return null;
  try {
    const d = JSON.parse(readFileSync(RUNTIME_FILE, 'utf8'));
    return Number.isInteger(d.pid) && Number.isInteger(d.port) && typeof d.token === 'string' ? d : null;
  } catch {
    return null;
  }
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === 'EPERM';
  }
}

function imageOf(pid) {
  try {
    const csv = execFileSync('tasklist', ['/FI', `PID eq ${pid}`, '/FO', 'CSV', '/NH'],
      { encoding: 'utf8', windowsHide: true, timeout: 15_000 });
    const m = csv.match(/^"([^"]+)"/m);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

// What runtime.json says, judged without a handshake: absent | stale | foreign | candidate.
function discover() {
  const rt = readRuntime();
  if (!rt) return { state: 'absent' };
  if (!pidAlive(rt.pid)) return { state: 'stale', rt };
  const img = imageOf(rt.pid);
  if (!img || !/^pythonw?\.exe$/i.test(img)) return { state: 'foreign', rt, img };
  return { state: 'candidate', rt };
}

// A real ULID (Crockford base32: no I, L, O, U). The daemon's hello check rejects any other
// id shape AND counts it as a failed authentication — a sloppy id here would be a lockout.
const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
function ulid() {
  let t = Date.now();
  let s = '';
  for (let i = 0; i < 10; i++) { s = CROCKFORD[t % 32] + s; t = Math.floor(t / 32); }
  for (const b of randomBytes(16)) s += CROCKFORD[b % 32];
  return s;
}

function envelope(type, payload) {
  return JSON.stringify({ v: 1, id: ulid(), ts: new Date().toISOString(), type, corr: null, payload });
}

// ONE handshake + one cmd.ping. The token is read from runtime.json right here.
function ping(expectPid) {
  return new Promise((done) => {
    const rt = readRuntime();
    if (!rt || rt.pid !== expectPid || !pidAlive(rt.pid)) {
      done({ ok: false, why: 'runtime.json changed or its pid is gone - not connecting' });
      return;
    }
    const t0 = Date.now();
    let settled = false;
    const finish = (r) => { if (!settled) { settled = true; clearTimeout(timer); try { ws.close(); } catch {} done(r); } };
    const ws = new WebSocket(`ws://127.0.0.1:${rt.port}/v1`, { headers: { Origin: 'tessa://console' } });
    const timer = setTimeout(() => finish({ ok: false, why: `no answer within ${HANDSHAKE_MS} ms` }), HANDSHAKE_MS);
    ws.onopen = () => ws.send(envelope('cmd.hello', {
      token: rt.token, surface: 'console', surfaceVersion: 'tcli', protocolVersion: 1,
    }));
    ws.onmessage = (m) => {
      let msg;
      try { msg = JSON.parse(String(m.data)); } catch { return; }
      if (msg.type === 'res.hello') ws.send(envelope('cmd.ping', {}));
      else if (msg.type === 'res.pong') finish({ ok: true, ms: Date.now() - t0, rt });
      else if (msg.type && msg.type.startsWith('err.')) finish({ ok: false, why: msg.type });
    };
    ws.onclose = (e) => finish({ ok: false, why: `closed ${e.code} ${e.reason || ''}`.trim() });
    ws.onerror = () => finish({ ok: false, why: 'connection error' });
  });
}

function logFiles() {
  if (!existsSync(LOG_DIR)) return [];
  return readdirSync(LOG_DIR).filter((n) => /^daemon-.*\.log$/i.test(n))
    .map((n) => join(LOG_DIR, n)).sort((a, b) => statSync(a).mtimeMs - statSync(b).mtimeMs);
}

function recentLines() {
  const files = logFiles().slice(-2);
  return files.flatMap((f) => readFileSync(f, 'utf8').split(/\r?\n/)).filter((l) => l.trim() !== '');
}

function tail(n = 20) {
  const lines = recentLines().slice(-n);
  out(`last ${lines.length} lines of the daemon log (${logFiles().slice(-1)[0] || 'none'}):`);
  for (const l of lines) out(`  ${l}`);
}

function logMark() {
  const mark = {};
  for (const f of logFiles()) {
    try { mark[f] = statSync(f).size; } catch {}
  }
  return mark;
}

function linesSince(mark) {
  const lines = [];
  for (const f of logFiles()) {
    let size;
    try { size = statSync(f).size; } catch { continue; }
    const from = mark[f] ?? 0;
    if (size <= from) continue;
    let fd;
    try {
      fd = openSync(f, 'r');
      const buf = Buffer.alloc(size - from);
      readSync(fd, buf, 0, buf.length, from);
      lines.push(...buf.toString('utf8').split(/\r?\n/).filter((l) => l.trim() !== ''));
    } catch {
    } finally {
      if (fd !== undefined) closeSync(fd);
    }
  }
  return lines;
}

function dailyLog() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return join(LOG_DIR, `daemon-${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}.log`);
}

function logWritable() {
  const f = dailyLog();
  if (!existsSync(f)) return true;
  try {
    closeSync(openSync(f, 'a'));
    return true;
  } catch {
    return false;
  }
}

function startedPid(lines) {
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = lines[i].match(/\] startup: pid (\d+) /);
    if (m) return Number(m[1]);
  }
  return null;
}

function said(lines) {
  return lines.length ? lines[lines.length - 1] : 'nothing new yet';
}

function startupLine(pid) {
  const lines = recentLines();
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].includes('] startup: pid ' + pid + ' ')) return lines[i];
  }
  return null;
}

function lastExitReason() {
  const lines = recentLines();
  let start = -1;
  for (let i = lines.length - 1; i >= 0; i--) if (lines[i].includes('] startup: ')) { start = i; break; }
  if (start < 0) return 'no daemon start is recorded in the newest logs';
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i].includes('] exit: ')) return lines[i].slice(lines[i].indexOf('exit: '));
  }
  const pid = (lines[start].match(/startup: pid (\d+)/) || [])[1];
  return `no exit line after the last start (pid ${pid}): it ended without a clean stop - ` +
    'killed, crashed, power loss, or the session ended without notice';
}

const uptime = (startedAt) => {
  const s = Math.max(0, Math.round((Date.now() - Date.parse(startedAt)) / 1000));
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ${s % 60}s`;
};

async function status() {
  const d = discover();
  if (d.state === 'candidate') {
    const p = await ping(d.rt.pid);
    if (p.ok) {
      const sl = startupLine(d.rt.pid);
      const voice = sl ? (/flags [^|]*--voice/.test(sl) ? 'on' : 'off') : 'unknown (no startup line for this pid)';
      out(`Tessa is running: pid ${d.rt.pid}, port ${d.rt.port}, up ${uptime(d.rt.startedAt)} ` +
        `(since ${d.rt.startedAt}), voice ${voice}. Answered cmd.ping in ${p.ms} ms.`);
      return 0;
    }
    out(`pid ${d.rt.pid} is alive and named in runtime.json, but it did not answer: ${p.why}.`);
    out('Not retrying (one handshake per run, so tcli can never trip the lockout).');
    tail();
    return 1;
  }
  const why = { absent: 'no runtime.json', stale: `runtime.json names pid ${d.rt?.pid}, which is gone`,
    foreign: `runtime.json names pid ${d.rt?.pid}, which is now ${d.img || 'unknown'} - stale` }[d.state];
  out(`Tessa is not running (${why}).`);
  out(`last exit: ${lastExitReason()}`);
  tail();
  return 3;
}

function writeStopRequest(rt, restarting) {
  const nonce = randomBytes(16).toString('hex');
  const msg = `tessa-stop-v1|${rt.pid}|${nonce}|${restarting ? 1 : 0}`;
  const req = {
    v: 'tessa-stop-v1', pid: rt.pid, nonce, restarting, by: 'tcli',
    proof: createHmac('sha256', rt.token).update(msg).digest('hex'),
    requestedAt: new Date().toISOString(),
  };
  const tmp = STOP_FILE + '.tmp';
  writeFileSync(tmp, JSON.stringify(req), 'utf8');
  renameSync(tmp, STOP_FILE);
}

async function waitGone(pid, ms, mark) {
  const t0 = Date.now();
  let note = t0 + PROGRESS_MS;
  while (Date.now() - t0 < ms) {
    if (!pidAlive(pid)) return Date.now() - t0;
    if (Date.now() >= note) {
      out(`  ${secs(Date.now() - t0)} s: pid ${pid} is still stopping - the log says: ${said(linesSince(mark))}`);
      note += PROGRESS_MS;
    }
    await sleep(100);
  }
  return -1;
}

async function stopDaemon(restarting) {
  const d = discover();
  if (d.state !== 'candidate') {
    out(`Tessa is not running - nothing to stop (${d.state === 'absent' ? 'no runtime.json' : `runtime.json is ${d.state}`}).`);
    return { code: 3 };
  }
  const t0 = Date.now();
  const mark = logMark();
  writeStopRequest(d.rt, restarting);
  const ms = await waitGone(d.rt.pid, STOP_WAIT_MS, mark);
  if (ms < 0) {
    out(`Asked pid ${d.rt.pid} to stop; it is still running after ${STOP_WAIT_MS / 1000} s. NOT killed.`);
    out(`If it must go, end pid ${d.rt.pid} yourself.`);
    tail();
    return { code: 1 };
  }
  const left = readRuntime();
  out(`Stopped pid ${d.rt.pid} in ${Date.now() - t0} ms (clean shutdown, audited). ` +
    `runtime.json ${left && left.pid === d.rt.pid ? 'STILL PRESENT' : 'removed'}.`);
  return { code: 0, old: d.rt };
}

function launch() {
  if (process.env.TESSA_RUNTIME_DIR) {
    out('TESSA_RUNTIME_DIR is set (a test isolation variable). start-tessa.cmd clears it and starts');
    out('the REAL daemon, which this tcli would then not see. Unset it and run again.');
    return null;
  }
  if (!existsSync(LAUNCHER)) {
    out(`Cannot find ${LAUNCHER}.`);
    return null;
  }
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^TESSA_/i.test(k)));
  // NOT `detached`. DETACHED_PROCESS leaves cmd with no console at all, so any console program
  // it runs gets a brand-new console — with Windows Terminal as the default terminal, a terminal
  // window on screen (measured: 3 windows per start). stdio 'ignore' + windowsHide gives cmd a
  // console with NO window (CREATE_NO_WINDOW) that everything it runs inherits. The daemon is
  // pythonw.exe started by START /B: libuv's job object lets a grandchild break away silently,
  // so it is in no job and outlives this process and its shell (measured, T10).
  const child = spawn('cmd.exe', ['/d', '/c', LAUNCHER, '--now'],
    { cwd: REPO, env, stdio: 'ignore', windowsHide: true });
  const run = { exited: false, code: null, error: null, at: 0 };
  child.on('exit', (code) => { Object.assign(run, { exited: true, code, at: Date.now() }); });
  child.on('error', (e) => { Object.assign(run, { exited: true, error: e.code || String(e), at: Date.now() }); });
  child.unref();
  return run;
}

async function launchVerified(mark) {
  for (let attempt = 1; attempt <= LAUNCH_TRIES; attempt++) {
    const t0 = Date.now();
    while (!logWritable() && Date.now() - t0 < LOG_FREE_MS) await sleep(100);
    if (!logWritable()) {
      out(`${dailyLog()} is held by another process (no write sharing) after ${LOG_FREE_MS / 1000} s; ` +
        'the launcher writes there, launching anyway.');
    } else if (Date.now() - t0 >= 200) {
      out(`${dailyLog()} was held by another process for ${Date.now() - t0} ms; launching now.`);
    }
    const run = launch();
    if (!run) return { ok: false, refused: true };
    while (Date.now() - t0 < LAUNCH_SEEN_MS) {
      if (linesSince(mark).some((l) => l.startsWith('==== launcher'))) return { ok: true, attempt };
      if (run.exited && Date.now() - run.at > 2_000) break;
      await sleep(100);
    }
    if (linesSince(mark).some((l) => l.startsWith('==== launcher'))) return { ok: true, attempt };
    const how = run.error ? `could not run (${run.error})` :
      run.exited ? `exited ${run.code} and wrote nothing to the daemon log` :
      `wrote nothing to the daemon log in ${LAUNCH_SEEN_MS / 1000} s`;
    out(`Launch attempt ${attempt} of ${LAUNCH_TRIES}: the launcher ${how}; no daemon was started by it.` +
      (attempt < LAUNCH_TRIES ? ' Launching again.' : ''));
  }
  return { ok: false };
}

async function waitUp(oldPid, mark) {
  const t0 = Date.now();
  let note = t0 + PROGRESS_MS;
  let refusedSaid = false;
  while (Date.now() - t0 < READY_WAIT_MS) {
    const rt = readRuntime();
    if (rt && rt.pid !== oldPid && pidAlive(rt.pid)) {
      const p = await ping(rt.pid);   // the run's ONE handshake
      return p.ok ? { ok: true, rt, ms: Date.now() - t0, pingMs: p.ms } : { ok: false, why: p.why };
    }
    const fresh = linesSince(mark);
    const pid = startedPid(fresh);
    if (pid !== null && pid !== oldPid && !pidAlive(pid)) {
      const fresh2 = linesSince(mark);
      const ended = fresh2.slice().reverse().find((l) => l.includes('] exit: '));
      return { ok: false, why: `pid ${pid} started and then ended before it was ready - ` +
        (ended ? ended.slice(ended.indexOf('exit: ')) : `no exit line; the log last said: ${said(fresh2)}`) };
    }
    if (!refusedSaid && fresh.some((l) => l.includes('] Tessa is already running'))) {
      out('  a launch was refused because a daemon already holds the guard - waiting for that one.');
      refusedSaid = true;
    }
    if (Date.now() >= note) {
      out(`  ${secs(Date.now() - t0)} s: still starting${pid !== null ? ` (pid ${pid})` : ''} - the log says: ${said(fresh)}`);
      note += PROGRESS_MS;
    }
    await sleep(250);
  }
  return { ok: false, why: `no new runtime.json within ${READY_WAIT_MS / 1000} s; the log last said: ${said(linesSince(mark))}` };
}

const digest = (t) => createHash('sha256').update(t).digest('hex').slice(0, 12);

async function start() {
  const d = discover();
  if (d.state === 'candidate') {
    const p = await ping(d.rt.pid);
    if (p.ok) {
      out(`Tessa is already running (pid ${d.rt.pid}, port ${d.rt.port}) - nothing launched.`);
      return 0;
    }
    out(`pid ${d.rt.pid} is alive and named in runtime.json but did not answer (${p.why}); not launching a second.`);
    tail();
    return 1;
  }
  const t0 = Date.now();
  const mark = logMark();
  const l = await launchVerified(mark);
  if (l.refused) return 1;
  if (!l.ok) {
    out(`Tessa was NOT started: the launcher never ran to its first log line in ${LAUNCH_TRIES} attempts.`);
    tail();
    return 1;
  }
  const up = await waitUp(d.rt?.pid ?? -1, mark);
  if (!up.ok) {
    out(`Tessa did not come up: ${up.why}.`);
    tail();
    return 1;
  }
  out(`Tessa started: pid ${up.rt.pid}, port ${up.rt.port}; runtime.json after ${Date.now() - t0} ms, ` +
    `answered cmd.ping in ${up.pingMs} ms.`);
  return 0;
}

async function restart() {
  const t0 = Date.now();
  const s = await stopDaemon(true);
  if (s.code === 1) return 1;
  if (s.code === 3) out('Starting it.');
  const mark = logMark();
  const l = await launchVerified(mark);
  if (l.refused) return 1;
  if (!l.ok) {
    out(`She is STOPPED and was NOT restarted: the launcher never ran to its first log line in ${LAUNCH_TRIES} attempts. ` +
      'Run tcli daemon start.');
    tail();
    return 1;
  }
  const up = await waitUp(s.old?.pid ?? -1, mark);
  if (!up.ok) {
    out(`The new daemon did not come up: ${up.why}.`);
    tail();
    return 1;
  }
  out(`Restarted: old pid ${s.old?.pid ?? '-'} -> new pid ${up.rt.pid}, port ${up.rt.port}; ` +
    `new token: ${s.old ? (digest(s.old.token) !== digest(up.rt.token) ? 'yes' : 'NO') : 'n/a'}; ` +
    `${Date.now() - t0} ms end to end (cmd.ping ${up.pingMs} ms).`);
  return 0;
}

const verbs = { status, start, stop: async () => (await stopDaemon(false)).code, restart };
const verb = (process.argv[2] || '').toLowerCase();
if (!verbs[verb]) {
  out('usage: tcli daemon status | start | stop | restart');
  process.exit(1);
}
process.exit(await verbs[verb]());
