import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { CARD_FIXTURE_KINDS, FILE_TEXT_CHARS, MAX_CARD_ARGS_BYTES, approvalFixtureDelay, approvalFixtureFlag, argsBytes, cardFixture } from '../src/main/card-fixtures.ts';

const ORB = process.env['TESSA_ORB_DIR'] ?? join(__dirname, '..');

test('his normal start loads no fixture card', () => {
  assert.deepEqual(approvalFixtureFlag(['C:\\dev\\tessa\\node_modules\\electron\\dist\\electron.exe', 'C:\\dev\\tessa\\apps\\orb'], true), []);
  assert.deepEqual(approvalFixtureFlag(['electron.exe', '.'], false), []);
  assert.equal(approvalFixtureDelay(['electron.exe', '.'], true), null);
});

test('fixtures load only with the dev flag, and never in a packaged build', () => {
  assert.deepEqual(approvalFixtureFlag(['electron.exe', '.', '--fixture-approval=run, write,,type'], true), ['run', 'write', 'type']);
  assert.deepEqual(approvalFixtureFlag(['electron.exe', '.', '--fixture-approval=run'], false), []);
  assert.deepEqual(approvalFixtureDelay(['x', '--fixture-approval-at=6000,4000'], true), { at: 6000, gap: 4000 });
  assert.equal(approvalFixtureDelay(['x', '--fixture-approval-at=6000'], false), null);
});

test('main reads the fixture flag through the tested function and only in dev', () => {
  const src = readFileSync(join(ORB, 'src', 'main', 'index.ts'), 'utf8');
  assert.ok(src.includes('const fixtureKinds = approvalFixtureFlag(process.argv, isDev);'));
  assert.ok(src.includes('const isDev = !app.isPackaged;'));
  assert.equal(/process\.argv\.find\(\(a\) => a\.startsWith\('--fixture-approval='\)\)/.test(src), false);
});

test('one fixture per new card type, each a fixture id with placeholder text', () => {
  assert.deepEqual([...CARD_FIXTURE_KINDS], ['run', 'run768', 'runrtl', 'write', 'type', 'keys', 'app']);
  const tools = new Set<string>();
  for (const kind of CARD_FIXTURE_KINDS) {
    const set = cardFixture(kind);
    assert.ok(set && set.length === 1, kind);
    const card = set[0]!;
    tools.add(card.tool);
    assert.ok(card.requestId.startsWith('fixture-'), kind);
    assert.deepEqual([...(card.frozen ?? [])].sort(), Object.keys(card.args).sort(), kind);
    assert.match(JSON.stringify(card.args), /placeholder|Placeholder|Example/, kind);
  }
  assert.deepEqual([...tools].sort(), ['app.launch_checked', 'code.run_mine', 'fs.write_checked', 'input.type_checked']);
  assert.equal(cardFixture('nonsense'), undefined);
});

test('the big run card is 768 KiB of args and keeps the daemon caps on file text', () => {
  const card = cardFixture('run768')![0]!;
  const bytes = argsBytes(card.args);
  assert.ok(bytes <= MAX_CARD_ARGS_BYTES && bytes > MAX_CARD_ARGS_BYTES - 1024, String(bytes));
  const views = card.args['views'] as Record<string, string>;
  assert.equal(Object.keys(views).length, 8);
  assert.ok(Object.values(views).every((t) => t.length <= FILE_TEXT_CHARS));
  assert.ok(Object.values(views).reduce((n, t) => n + t.length, 0) <= 512 * 1024);
  assert.equal((card.args['files'] as unknown[]).length, 200);
});

test('one fixture has a right-to-left mark in a file name', () => {
  const card = cardFixture('runrtl')![0]!;
  const names = (card.args['files'] as { file: string }[]).map((f) => f.file);
  assert.ok(names.some((n) => n.includes('\u202E')));
});
