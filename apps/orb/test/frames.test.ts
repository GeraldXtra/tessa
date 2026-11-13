import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { frameMix } from '../src/renderer/dev-drive.ts';

test('frame intervals land in the 60 Hz buckets by their nearest multiple of 16.67 ms', () => {
  const m = frameMix([16.6, 16.7, 16.8, 24.9, 25.0, 33.3, 41.6, 41.7, 50.0, 58.3, 66.7, 120.4]);
  assert.equal(m.frames, 12);
  assert.equal(m.at16, 4);
  assert.equal(m.at33, 3);
  assert.equal(m.at50, 3);
  assert.equal(m.longer, 2);
  assert.equal(m.longestMs, 120.4);
  assert.equal(m.at16 + m.at33 + m.at50 + m.longer, m.frames);
});

test('an empty recording reports nothing rather than inventing frames', () => {
  assert.deepEqual(frameMix([]), { frames: 0, at16: 0, at33: 0, at50: 0, longer: 0, longestMs: 0, p50Ms: 0, p95Ms: 0, hiddenSeen: false });
});
