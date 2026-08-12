/**
 * Smoke test for @zoey/protocol.
 *
 * Runs with zero dependencies:
 *   node --experimental-strip-types packages/protocol/test/smoke.ts
 *
 * Covers the contract rules that are easy to break silently:
 *   §2.1 Origin allowlist
 *   §3   envelope shape
 *   §3.2 unknown types are recognised as unknown but NOT rejected
 *   §3.3 ULID monotonicity within a millisecond
 *   §6.3 hydration cost computed without touching the file
 */

import assert from 'node:assert/strict';
import {
  PROTOCOL_VERSION, ALLOWED_ORIGINS, HANDSHAKE_DEADLINE_MS, CLOSE_CODES, ERROR_CODES,
  AGENT_STATES, TIERS, PROVENANCE, CLOUD_STATES, PTY_REPORT_EVENTS,
  JOB_STATUSES, CREATED_BY, SURFACES, SPAWN_MODES, FS_CHANGE_KINDS, DECISIONS, DECISIONS_SENDABLE,
  makeEnvelope, isEnvelope, isKnownType, isAllowedOrigin, hydrationBytes, ulid,
  parseDeepLink, buildDeepLink,
  ALL_KNOWN_TYPES, SHARED_EVENTS, CONSOLE_EVENTS, ORB_EVENTS,
  SHARED_COMMANDS, CONSOLE_COMMANDS, ORB_COMMANDS,
} from '../src/index.ts';

let passed = 0;
const ok = (name: string, fn: () => void) => {
  fn();
  passed++;
  console.log(`  ok  ${name}`);
};

console.log('@zoey/protocol smoke test\n');

/* ── version ─────────────────────────────────────────────────────────────── */

ok('PROTOCOL_VERSION is 1', () => {
  assert.equal(PROTOCOL_VERSION, 1);
});

ok('handshake deadline is 3000ms (CONTRACT §2.1)', () => {
  assert.equal(HANDSHAKE_DEADLINE_MS, 3000);
});

ok('close codes match CONTRACT §2.2', () => {
  assert.equal(CLOSE_CODES.Unauthorized, 4401);
  assert.equal(CLOSE_CODES.HandshakeTimeout, 4408);
  assert.equal(CLOSE_CODES.ProtocolMismatch, 4409);
  assert.equal(CLOSE_CODES.RateLimited, 4429);
});

/* ── §2.1 Origin allowlist ───────────────────────────────────────────────── */

ok('only zoey://console and zoey://orb are allowed origins', () => {
  assert.deepEqual([...ALLOWED_ORIGINS], ['zoey://console', 'zoey://orb']);
  assert.equal(isAllowedOrigin('zoey://console'), true);
  assert.equal(isAllowedOrigin('zoey://orb'), true);
});

ok('browser-shaped origins are rejected — the drive-by attack', () => {
  for (const bad of [
    'http://evil.com', 'https://evil.com', 'http://localhost:3000',
    'https://127.0.0.1', 'file://', 'null', '', undefined, null,
    'zoey://console.evil.com', 'zoey://consol', 'ZOEY://CONSOLE',
  ]) {
    assert.equal(isAllowedOrigin(bad as string), false, `should reject: ${String(bad)}`);
  }
});

/* ── §3 envelope ─────────────────────────────────────────────────────────── */

ok('makeEnvelope produces a valid envelope', () => {
  const e = makeEnvelope('evt.agent.state', { companionId: 'zoey', state: 'thinking' });
  assert.equal(isEnvelope(e), true);
  assert.equal(e.v, 1);
  assert.equal(e.type, 'evt.agent.state');
  assert.equal(e.corr, null);
  assert.equal(e.payload.state, 'thinking');
});

ok('ts is ISO-8601 UTC with exactly milliseconds', () => {
  const e = makeEnvelope('cmd.ping', {} as never);
  assert.match(e.ts, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
});

ok('isEnvelope rejects malformed frames', () => {
  const good = makeEnvelope('cmd.ping', {} as never);
  assert.equal(isEnvelope({ ...good, v: 2 }), false, 'wrong version');
  assert.equal(isEnvelope({ ...good, id: 'not-a-ulid' }), false, 'bad id');
  assert.equal(isEnvelope({ ...good, ts: '2026-08-12' }), false, 'bad ts');
  assert.equal(isEnvelope({ ...good, type: 'bogus.type' }), false, 'bad type prefix');
  assert.equal(isEnvelope({ ...good, payload: [] }), false, 'payload must be an object');
  assert.equal(isEnvelope({ ...good, payload: null }), false, 'payload must not be null');
  assert.equal(isEnvelope({ ...good, corr: 'nope' }), false, 'corr must be ULID or null');
  assert.equal(isEnvelope(null), false);
  assert.equal(isEnvelope('string'), false);
});

ok('corr round-trips for request/response pairing', () => {
  const req = makeEnvelope('cmd.ping', {} as never);
  const res = makeEnvelope('evt.daemon.health', {
    uptimeS: 1, cpuPct: 0, memMB: 1, apiReachable: true, budgetSpent: 0, budgetCap: 0,
  }, req.id);
  assert.equal(res.corr, req.id);
  assert.equal(isEnvelope(res), true);
});

/* ── §3.2 forward compatibility ──────────────────────────────────────────── */

ok('unknown types are structurally VALID but flagged unknown (§3.2)', () => {
  const future = { ...makeEnvelope('cmd.ping', {} as never), type: 'evt.future.feature' };
  // Structurally valid — a surface must NOT reject this, it must ignore it.
  assert.equal(isEnvelope(future), true);
  // But the daemon can tell it is not in the contract.
  assert.equal(isKnownType('evt.future.feature'), false);
  assert.equal(isKnownType('evt.agent.state'), true);
});

ok('unknown payload fields do not invalidate a known type (§3.2)', () => {
  const e = makeEnvelope('evt.agent.state', {
    companionId: 'zoey', state: 'idle', futureField: 42,
  } as never);
  assert.equal(isEnvelope(e), true);
});

/* ── type registry ───────────────────────────────────────────────────────── */

ok('no type name is claimed by two namespaces', () => {
  const all = [
    ...SHARED_EVENTS, ...CONSOLE_EVENTS, ...ORB_EVENTS,
    ...SHARED_COMMANDS, ...CONSOLE_COMMANDS, ...ORB_COMMANDS,
  ];
  assert.equal(new Set(all).size, all.length, 'duplicate type name across namespaces');
  assert.equal(ALL_KNOWN_TYPES.size, all.length);
});

ok('Console never claims an Orb namespace and vice versa', () => {
  for (const t of CONSOLE_EVENTS) assert.match(t, /^evt\.(pty|fs)\./);
  for (const t of CONSOLE_COMMANDS) assert.match(t, /^cmd\.(pty|fs|window)\./);
  for (const t of ORB_EVENTS) assert.match(t, /^evt\.(voice|scene)\./);
  for (const t of ORB_COMMANDS) assert.match(t, /^cmd\.(voice|scene)\./);
});

/* ── §7.4 closed enums ───────────────────────────────────────────────────── */

ok('closed enums match the contract exactly (§7.4)', () => {
  assert.deepEqual([...AGENT_STATES],
    ['idle', 'listening', 'thinking', 'speaking', 'working', 'blocked']);
  assert.deepEqual([...TIERS], ['green', 'amber', 'red']);
  assert.deepEqual([...PROVENANCE],
    ['human', 'program', 'agent', 'schedule', 'external', 'system']);
  assert.deepEqual([...CLOUD_STATES],
    ['local', 'cloudOnly', 'pinned', 'partial', 'unknown']);
  assert.deepEqual([...JOB_STATUSES],
    ['queued', 'running', 'blocked', 'succeeded', 'failed', 'cancelled', 'needsReview']);
  assert.deepEqual([...CREATED_BY],
    ['user', 'agent', 'schedule', 'fileWatch', 'email', 'webhook', 'systemEvent']);
  assert.deepEqual([...SPAWN_MODES], ['window', 'tab', 'pane', 'cdCurrent']);
  assert.deepEqual([...FS_CHANGE_KINDS],
    ['created', 'modified', 'deleted', 'renamed', 'hydrationChanged']);
  assert.deepEqual([...DECISIONS], ['approve', 'deny', 'expired']);
});

ok('SURFACES stays console|orb — `mobile` deliberately excluded', () => {
  // A phone cannot read %LOCALAPPDATA%\Zoey\runtime.json and cannot reach
  // 127.0.0.1. Adding the value would promise a capability §1/§2 cannot serve.
  assert.deepEqual([...SURFACES], ['console', 'orb']);
});

ok('a surface may send approve|deny but NOT expired (§4.1)', () => {
  assert.deepEqual([...DECISIONS_SENDABLE], ['approve', 'deny']);
  assert.ok(!(DECISIONS_SENDABLE as readonly string[]).includes('expired'),
    'expired is daemon-emitted only — a surface sending it is a contract violation');
});

ok('`blocked` and `needsReview` are distinct job states', () => {
  // blocked = approval outstanding, still live.
  // needsReview = the 30-minute window lapsed unanswered (spec §5 rule 5).
  const s = JOB_STATUSES as readonly string[];
  assert.ok(s.includes('blocked') && s.includes('needsReview'));
});

ok('deep links cannot reach cdCurrent — it mutates an existing session', () => {
  assert.equal(parseDeepLink('zoey://open?path=C%3A%5Cdev&mode=cdCurrent'), null);
  // ...but it IS a valid spawnAt mode from inside the app.
  assert.ok((SPAWN_MODES as readonly string[]).includes('cdCurrent'));
});

/* ── §3.3 ULID monotonicity ──────────────────────────────────────────────── */

ok('ULIDs are 26 chars and strictly increasing within one millisecond', () => {
  const fixed = 1_754_988_843_221;
  const ids = Array.from({ length: 500 }, () => ulid(fixed));
  for (const id of ids) assert.match(id, /^[0-9A-HJKMNP-TV-Z]{26}$/);
  for (let i = 1; i < ids.length; i++) {
    assert.ok(ids[i]! > ids[i - 1]!, `not monotonic at ${i}: ${ids[i - 1]} -> ${ids[i]}`);
  }
});

ok('ULID timestamp prefix sorts across milliseconds', () => {
  const a = ulid(1_754_988_843_221);
  const b = ulid(1_754_988_843_222);
  assert.ok(b > a);
});

/* ── §6.3 hydration cost ─────────────────────────────────────────────────── */

ok('hydrationBytes computes cost from attributes alone (§6.3)', () => {
  // A fully dehydrated OneDrive placeholder: real size known, nothing allocated.
  assert.equal(hydrationBytes({ size: 3_400_000_000, allocSize: 0 }), 3_400_000_000);
  // Fully local: nothing to download.
  assert.equal(hydrationBytes({ size: 1432, allocSize: 4096 }), 0);
  // Partially hydrated.
  assert.equal(hydrationBytes({ size: 1000, allocSize: 400 }), 600);
});

/* ── §4.2 the byte stream is NOT in the protocol ─────────────────────────── */

ok('PTY byte stream is absent from the contract (§4.2)', () => {
  for (const banned of ['evt.pty.data', 'cmd.pty.write', 'cmd.pty.resize', 'cmd.pty.kill']) {
    assert.equal(ALL_KNOWN_TYPES.has(banned), false,
      `${banned} must not exist — terminal bytes never traverse the daemon`);
  }
  // What IS present: authorization, audit, revocation.
  assert.equal(ALL_KNOWN_TYPES.has('cmd.pty.requestSpawn'), true);
  assert.equal(ALL_KNOWN_TYPES.has('cmd.pty.report'), true);
  assert.equal(ALL_KNOWN_TYPES.has('evt.pty.revoke'), true);
  assert.equal(ALL_KNOWN_TYPES.has('evt.pty.sessions'), true);
});

ok('pty lifecycle report events are a closed set (§6.5)', () => {
  assert.deepEqual([...PTY_REPORT_EVENTS],
    ['started', 'exited', 'cwdChanged', 'titleChanged', 'killed', 'startFailed']);
});

/* ── §6.6 deep-link safety ───────────────────────────────────────────────── */

ok('valid deep links parse', () => {
  assert.deepEqual(
    parseDeepLink('zoey://open?path=C%3A%5Cdev%5Czoey&mode=tab'),
    { path: 'C:\\dev\\zoey', mode: 'tab' });
  // mode defaults to window
  assert.deepEqual(
    parseDeepLink('zoey://open?path=C%3A%5Cdev'),
    { path: 'C:\\dev', mode: 'window' });
});

ok('deep link REJECTS any command parameter — RCE vector (§6.6)', () => {
  for (const hostile of [
    'zoey://open?path=C%3A%5C&cmd=calc.exe',
    'zoey://open?path=C%3A%5C&mode=tab&cmd=rm+-rf+%2F',
    'zoey://open?path=C%3A%5C&run=whoami',
    'zoey://open?path=C%3A%5C&exec=1',
    'zoey://run?cmd=calc.exe',
  ]) {
    assert.equal(parseDeepLink(hostile), null, `must reject: ${hostile}`);
  }
});

ok('deep link rejects wrong scheme, host, or mode', () => {
  assert.equal(parseDeepLink('https://open?path=C%3A%5C'), null, 'wrong scheme');
  assert.equal(parseDeepLink('zoey://run?path=C%3A%5C'), null, 'wrong host');
  assert.equal(parseDeepLink('zoey://open?mode=window'), null, 'missing path');
  assert.equal(parseDeepLink('zoey://open?path=C%3A%5C&mode=bogus'), null, 'bad mode');
  assert.equal(parseDeepLink('not a url'), null);
});

ok('buildDeepLink round-trips through parseDeepLink', () => {
  const link = { path: 'C:\\Users\\SERIOUS-PC\\OneDrive\\My Resume', mode: 'window' as const };
  assert.deepEqual(parseDeepLink(buildDeepLink(link)), link);
});

/* ── error codes ─────────────────────────────────────────────────────────── */

ok('unknown-type error keeps the connection open (§7.6 contract intent)', () => {
  assert.ok((ERROR_CODES as readonly string[]).includes('protocol.unknownType'));
});

console.log(`\n${passed} passed\n`);
