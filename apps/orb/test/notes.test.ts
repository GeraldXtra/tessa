import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { noteFor, noteLive, noteStack, type NoteLike } from '../src/renderer/ui/notes-view.ts';

const FADE = 450;

function note(id: number, at: number, sticky: boolean, cleared: number | null = null): NoteLike & { at: number } {
  return { id, at, sticky, cleared, until: sticky ? Number.POSITIVE_INFINITY : at + 6000 };
}

test('error-level daemon notifications are sticky; info and warn still fade', () => {
  const base = { id: 'n1', at: 0, from: 'daemon' as const, title: 'Audit chain check failed', body: 'Placeholder fixture reason.' };
  assert.equal(noteFor({ ...base, level: 'error' } as never).sticky, true);
  assert.equal(noteFor({ ...base, level: 'warn' } as never).sticky, false);
  assert.equal(noteFor({ ...base, level: 'info' } as never).sticky, false);
  assert.equal(noteFor({ ...base, level: 'error', from: 'orb' } as never).sticky, false);
});

test('a sticky note stays until it is clicked away, then fades like any other', () => {
  const n = note(1, 0, true);
  assert.equal(noteLive(n, 60 * 60 * 1000, FADE), true);
  const cleared = { ...n, cleared: 5000, until: 5000 };
  assert.equal(noteLive(cleared, 5200, FADE), true);
  assert.equal(noteLive(cleared, 5500, FADE), false);
  assert.equal(noteLive(note(2, 0, false), 6400, FADE), true);
  assert.equal(noteLive(note(2, 0, false), 6500, FADE), false);
});

test('a waiting error stays on the stack even when newer notes arrive', () => {
  const now = 10_000;
  const list = [note(5, 9000, false), note(4, 8000, false), note(3, 7000, false), note(2, 6000, false), note(1, 100, true)];
  const { shown, more } = noteStack(list, now, FADE, 3);
  assert.equal(shown[0]?.id, 1);
  assert.deepEqual(shown.map((n) => n.id), [1, 5, 4]);
  assert.equal(more, 2);
  const after = noteStack([...list.slice(0, 4), { ...list[4]!, cleared: now, until: now }], now + 1000, FADE, 3);
  assert.equal(after.shown.some((n) => n.id === 1), false);
});
