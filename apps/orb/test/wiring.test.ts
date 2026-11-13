import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { GRANT_TIMEOUT_MS, childEnv, foregroundScript, parseGrant, powershellPath, validPid } from '../src/main/foreground.ts';
import { TOPICS, subscribedTo, topicMatches } from '../src/main/topics.ts';
import { TYPING_TOOL, isReasked, reaskedLine } from '../src/shared/cards.ts';
import { noteFor } from '../src/renderer/ui/notes-view.ts';

const ORB = process.env['TESSA_ORB_DIR'] ?? join(__dirname, '..');
const read = (...p: string[]): string => readFileSync(join(ORB, ...p), 'utf8');

test('the subscriptions include the exact topic notification and the turn timing topic', () => {
  assert.ok((TOPICS as readonly string[]).includes('notification'));
  assert.ok((TOPICS as readonly string[]).includes('turn.*'));
  assert.equal(subscribedTo('evt.notification'), true);
  assert.equal(subscribedTo('evt.turn.timing'), true);
  assert.equal(subscribedTo('evt.permission.request'), true);
  assert.equal(subscribedTo('evt.agent.state'), true);
});

test('under the matcher R1 describes, notification.* would not have matched', () => {
  assert.equal(topicMatches('notification.*', 'evt.notification'), false);
  assert.equal(topicMatches('notification', 'evt.notification'), true);
  assert.equal(topicMatches('turn.*', 'evt.turn.timing'), true);
  assert.equal(topicMatches('turn', 'evt.turn.timing'), false);
});

test('the socket subscribes with that list', () => {
  const src = read('src', 'main', 'ws-client.ts');
  assert.ok(src.includes("import { TOPICS } from './topics.ts';"));
  assert.ok(src.includes("makeEnvelope('cmd.subscribe', { topics: [...TOPICS] })"));
  assert.ok(src.includes("parsed.type === 'evt.notification'"));
  assert.ok(src.includes("parsed.type === 'evt.turn.timing'"));
});

test('the grant script carries only a checked process id', () => {
  assert.equal(validPid(25196), true);
  for (const bad of [0, -1, 1.5, Number.NaN, 2 ** 33, '25196', null]) assert.equal(validPid(bad), false);
  const script = foregroundScript(25196);
  assert.ok(script.includes('[TessaOrb.Foreground]::Grant(25196)'));
  assert.ok(script.includes('bool ok = AllowSetForegroundWindow(pid); int err = System.Runtime.InteropServices.Marshal.GetLastWin32Error();'));
  assert.ok(script.includes('SetLastError = true'));
  assert.equal((script.match(/25196/g) ?? []).length, 1);
  assert.throws(() => foregroundScript(-5));
  assert.throws(() => foregroundScript(1.5));
});

test('the grant reads its answer and runs PowerShell from the system folder without LIB', () => {
  assert.deepEqual(parseGrant('noise\r\nASFW 1 0\r\n'), { ok: true, lastError: 0 });
  assert.deepEqual(parseGrant('ASFW 0 5'), { ok: false, lastError: 5 });
  assert.equal(parseGrant('ASFW yes'), null);
  assert.equal(powershellPath({ SystemRoot: 'C:\\Windows' }), join('C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'));
  assert.equal(powershellPath({}), null);
  assert.deepEqual(childEnv({ LIB: 'x', lib: 'y', PATH: 'p', Other: 'o' }), { PATH: 'p', Other: 'o' });
  assert.ok(GRANT_TIMEOUT_MS >= 1000 && GRANT_TIMEOUT_MS <= 10_000);
});

test('main grants the foreground before an approved typing card leaves, and only then', () => {
  const src = read('src', 'main', 'index.ts');
  const start = src.indexOf('ipcMain.on(IPC.approvalRespond');
  const end = src.indexOf('ipcMain.', start + 10);
  const handler = src.slice(start, end);
  const typing = handler.indexOf("const typingApproval = decision === 'approve' && request.tool === TYPING_TOOL;");
  const grant = handler.indexOf('await grantForeground(pid)');
  const stillHeld = handler.indexOf('if (!refusableApprovals.has(requestId))');
  const send = handler.indexOf('connection?.sendPermissionResponse(requestId, decision, edited)');
  assert.ok(typing > 0 && grant > typing && stillHeld > grant && send > stillHeld);
  assert.ok(handler.indexOf('pendingApprovals.delete(requestId)') < grant);
  assert.ok(handler.includes('edited && isFrozenCard(request.tool, request.args)'));
});

test('err.permission.pending after an approval means a new card is up', () => {
  assert.equal(isReasked('permission.pending'), true);
  for (const code of ['permission.denied', 'notFound', 'internal', 'protocol.badEnvelope', 'unavailable']) assert.equal(isReasked(code), false);
  assert.match(reaskedLine(TYPING_TOOL), /^A new card is up\. .*nothing was typed/);
  assert.match(reaskedLine('code.run_mine'), /^A new card is up\./);
  const main = read('src', 'main', 'index.ts');
  assert.ok(main.includes('const reasked = isReasked(code);'));
  assert.ok(main.includes('...(reasked ? { reasked: true } : {}),'));
  const app = read('src', 'renderer', 'App.tsx');
  assert.ok(app.includes("if (refusal.reasked) {\n        pushNote('APPROVAL', reaskedLine(entry?.request.tool ?? refusal.tool ?? ''), 'wait');"));
});

test('a daemon notification shows in the stack under its own name and level', () => {
  assert.deepEqual(noteFor({ id: 'd1', level: 'error', title: 'Audit chain check failed at seq 9', body: 'Placeholder body.', from: 'daemon' }), {
    src: 'DAEMON ERROR',
    msg: 'Audit chain check failed at seq 9. Placeholder body.',
    kind: 'threat',
    sticky: true,
  });
  assert.equal(noteFor({ id: 'd2', level: 'warn', title: 't', body: '', from: 'daemon' }).kind, 'wait');
  assert.equal(noteFor({ id: 'd3', level: 'info', title: '', body: 'b', from: 'daemon' }).src, 'DAEMON');
  assert.equal(noteFor({ id: 'o1', level: 'info', title: 'x', body: 'y' }).src, 'ORB');
});
