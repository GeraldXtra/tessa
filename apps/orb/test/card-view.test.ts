import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { cardFixture } from '../src/main/card-fixtures.ts';
import { APP_TOOL, FROZEN_CARD_TOOLS, TYPING_TOOL, WRITE_TOOL, isFrozenCard, markInvisibleText } from '../src/shared/cards.ts';
import { cardModel, chunkText, keysView, visibleSegments, visibleString } from '../src/renderer/ui/card-view.ts';

function only(kind: string) {
  const set = cardFixture(kind);
  assert.ok(set && set.length === 1, `fixture ${kind}`);
  return set[0]!;
}

test('a right-to-left override is shown by name and never applied', () => {
  const segs = visibleSegments('notes\u202Etxt.py');
  assert.deepEqual(segs, [{ t: 'notes' }, { m: '[U+202E]' }, { t: 'txt.py' }]);
  assert.equal(visibleString('notes\u202Etxt.py').includes('\u202E'), false);
});

test('isolates, zero width, tags, no-break and soft hyphen are all shown', () => {
  const s = visibleString('a\u2066b\u2069c\u200Bd\u{E0041}e\u00A0f\u00ADg\uFEFFh\u2028i\u0085j');
  assert.equal(s, 'a[U+2066]b[U+2069]c[U+200B]d[U+E0041]e[U+00A0]f[U+00AD]g[U+FEFF]h[U+2028]i[U+0085]j');
});

test('control characters show visibly and runs are counted', () => {
  assert.equal(visibleString('x\u0001\u0001\u0001\u0001\u0001y'), 'x[U+0001 x5]y');
  assert.equal(visibleString('\u0007\u001b\u007f'), '[U+0007][U+001B][U+007F]');
  assert.equal(visibleString('a lone\rreturn'), 'a lone[U+000D]return');
  assert.equal(visibleString('\uD800'), '[U+D800]');
});

test('text keeps its tabs and line breaks, and CRLF reads as one line break', () => {
  assert.equal(visibleString('a\tb\nc\r\nd'), 'a\tb\nc\nd');
});

test('typed text names the special keys', () => {
  assert.equal(visibleString('line one\nline two\tend', 'typed'), 'line one[Enter]\nline two[Tab]end');
  assert.equal(visibleString('a\r\nb', 'typed'), 'a[Enter]\nb');
  assert.equal(visibleString('\b\x1b', 'typed'), '[Backspace][Esc]');
});

test('markup and markdown stay as the characters they are', () => {
  const raw = '<b>x</b> <img src=x onerror="alert(1)"> &amp; [a](https://example.invalid) **y**';
  assert.deepEqual(visibleSegments(raw), [{ t: raw }]);
});

test('key chords are named, and what was sent is kept', () => {
  assert.deepEqual(keysView('ctrl+v'), { chords: ['[Ctrl+V]'], raw: 'ctrl+v' });
  assert.deepEqual(keysView('enter').chords, ['[Enter]']);
  assert.deepEqual(keysView(['ctrl+shift+s', 'tab']).chords, ['[Ctrl+Shift+S]', '[Tab]']);
  assert.deepEqual(keysView([['alt', 'f4']]).chords, ['[Alt+F4]']);
  assert.deepEqual(keysView({ odd: 1 }), { chords: null, raw: '{"odd":1}' });
});

test('chunks cover the text exactly', () => {
  const text = 'abc\n'.repeat(1000) + 'x'.repeat(60_000) + '\r\nend';
  const chunks = chunkText(text);
  assert.equal(chunks.join(''), text);
  assert.ok(chunks.length > 5);
  assert.ok(chunks.every((c) => !c.endsWith('\r')));
});

test('the run card maps every file, the not-checked rows and the scope', () => {
  const card = only('run');
  const m = cardModel(card.tool, card.args);
  assert.equal(m.kind, 'run');
  assert.equal(m.frozen, true);
  assert.deepEqual(m.files.map((f) => f.file), ['main.py', 'helper.py', 'lib\\shapes.py']);
  assert.deepEqual(m.files.map((f) => f.hers), [true, false, true]);
  assert.ok(m.files.every((f) => /^[0-9a-f]{64}$/.test(f.sha256)));
  assert.ok(m.files.every((f) => f.text !== null && f.text.length > 0));
  assert.equal(m.files[0]!.text, (card.args['views'] as Record<string, string>)['main.py']);
  assert.deepEqual(m.unchecked.map((u) => [u.folder, u.files]), [['node_modules', 1234], ['.git', null], ['the rest of its folder', 37]]);
  const labels = m.rows.map((r) => r.label);
  assert.ok(labels.includes('SCOPE') && labels.includes('WHOSE') && labels.includes('SET SHA256'));
  assert.match(m.rows.find((r) => r.label === 'SCOPE')!.value, /\(script\)$/);
  assert.deepEqual(m.rest.map((r) => r.label), ['PROGRAM']);
});

test('a run card keeps a file whose text was dropped, with where to read it', () => {
  const card = only('run768');
  const m = cardModel(card.tool, card.args);
  assert.equal(m.files.length, 200);
  const dropped = m.files.filter((f) => f.text === null);
  assert.equal(dropped.length, 192);
  assert.ok(dropped.every((f) => f.view.startsWith('Too long to show') && /^[0-9a-f]{64}$/.test(f.sha256)));
});

test('a text on the card for a file not in its list is shown, not hidden', () => {
  const m = cardModel('code.run_mine', { files: [{ file: 'a.py', sha256: 'f'.repeat(64) }], views: { 'a.py': 'x = 1\n', 'b.py': 'y = 2\n' } });
  assert.deepEqual(m.files.map((f) => [f.file, f.orphan]), [['a.py', false], ['b.py', true]]);
});

test('a right-to-left mark in a file name reaches the model whole and shows by name', () => {
  const card = only('runrtl');
  const m = cardModel(card.tool, card.args);
  assert.equal(m.files[0]!.file, 'notes\u202Etxt.py');
  assert.equal(visibleString(m.files[0]!.file), 'notes[U+202E]txt.py');
});

test('the write card shows the path and the whole text', () => {
  const card = only('write');
  const m = cardModel(card.tool, card.args);
  assert.equal(m.kind, 'write');
  assert.equal(m.rows.find((r) => r.label === 'PATH')!.value, 'C:\\Example\\TessaTest\\placeholder-note.txt');
  assert.equal(m.blocks.length, 1);
  assert.equal(m.blocks[0]!.text, card.args['text']);
  assert.equal(m.blocks[0]!.mode, 'text');
  assert.deepEqual(m.rest, []);
});

test('the typing card shows the exact keys, the program, the window class and the focused element', () => {
  const card = only('type');
  const m = cardModel(card.tool, card.args);
  assert.equal(m.kind, 'type');
  assert.equal(m.blocks[0]!.mode, 'typed');
  assert.equal(visibleString(m.blocks[0]!.text, 'typed'), 'placeholder words[Enter]\nsecond line[Tab]after a tab');
  const rows = Object.fromEntries(m.rows.map((r) => [r.label, r.value]));
  assert.equal(rows['PROGRAM'], 'C:\\Program Files\\Windows NT\\Accessories\\wordpad.exe');
  assert.equal(rows['WINDOW CLASS'], 'WordPadClass');
  assert.equal(rows['FOCUSED ELEMENT'], 'RICHEDIT50W');
  assert.equal(rows['INTO THE WINDOW'], 'Placeholder Document - WordPad');
  assert.deepEqual(m.rest, []);
});

test('the key card names the chord and warns about the clipboard', () => {
  const card = only('keys');
  const m = cardModel(card.tool, card.args);
  assert.deepEqual(m.keys, { chords: ['[Ctrl+V]'], raw: 'ctrl+v' });
  assert.deepEqual(m.warnings, ['This pastes whatever is on the clipboard.']);
});

test('the app card shows the publisher, what approving means and the signature', () => {
  const card = only('app');
  const m = cardModel(card.tool, card.args);
  assert.equal(m.kind, 'app');
  const rows = Object.fromEntries(m.rows.map((r) => [r.label, r.value]));
  assert.match(rows['PUBLISHER'] ?? '', /Example Publisher Ltd/);
  assert.equal(rows['SIGNATURE'], 'valid');
  assert.match(rows['ISSUER'] ?? '', /Example Code Signing CA/);
  assert.match(rows['REVOCATION'] ?? '', /not revoked/);
  assert.match(m.approving ?? '', /^Approving trusts/);
  assert.equal(m.files.length, 1);
  assert.deepEqual(m.rest.map((r) => r.label).sort(), ['NAME', 'PROGRAM']);
});

test('nothing the daemon sends is dropped: unknown fields land in the rest', () => {
  const m = cardModel(TYPING_TOOL, { op: 'type', text: 'x', window: 'w', novel_field: { a: 1 } });
  assert.deepEqual(m.rest, [{ label: 'NOVEL_FIELD', value: '{\n  "a": 1\n}', mono: true }]);
});

test('the four new card types and any card listing files are frozen; older cards are not', () => {
  for (const tool of ['code.run_mine', WRITE_TOOL, TYPING_TOOL, APP_TOOL]) assert.ok(FROZEN_CARD_TOOLS.includes(tool));
  assert.equal(isFrozenCard('x.post', { text: 'x' }), false);
  assert.equal(isFrozenCard('shell.execute', { command: 'x' }), false);
  assert.equal(isFrozenCard('shell.execute', { command: 'x', files: [] }), true);
  assert.equal(cardModel('x.post', { text: 'x' }).kind, 'plain');
});

test('notification text marks invisible characters too', () => {
  assert.equal(markInvisibleText('Audit\u202E chain\tcheck'), 'Audit[U+202E] chain check');
});
