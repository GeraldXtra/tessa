import { strict as assert } from 'node:assert';
import { test } from 'node:test';

type Handler = (e: Record<string, unknown>) => void;

const handlers: Handler[] = [];
const responded: unknown[][] = [];
const reported: string[] = [];

Object.assign(globalThis, {
  HTMLElement: class {},
  HTMLTextAreaElement: class {},
  window: {
    addEventListener: (type: string, fn: Handler) => {
      if (type === 'keydown') handlers.push(fn);
    },
    removeEventListener: () => undefined,
    setTimeout: (fn: () => void) => {
      fn();
      return 0;
    },
    tessa: {
      respondToApproval: (...args: unknown[]) => responded.push(args),
      reportMetrics: (line: string) => reported.push(line),
    },
  },
});

function key(over: Record<string, unknown>): Record<string, unknown> {
  let prevented = false;
  return {
    key: '',
    code: '',
    keyCode: 0,
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    metaKey: false,
    repeat: false,
    isTrusted: true,
    target: null,
    preventDefault: () => {
      prevented = true;
    },
    get defaultPrevented() {
      return prevented;
    },
    ...over,
  };
}

test('no key press answers a card; a click does', async () => {
  const { approvalArrived, approvalsStore } = await import('../src/renderer/state/approval-store.ts');
  const { frontCardStore } = await import('../src/renderer/ui/stores.ts');
  const { installKeys } = await import('../src/renderer/ui/keys.ts');
  const { answerCard } = await import('../src/renderer/ui/Card.tsx');

  approvalArrived({ requestId: 'fixture-k1', tier: 'red', tool: 'input.type_checked', provenance: 'agent', expiresAt: new Date(Date.now() + 60_000).toISOString(), receivedAt: Date.now(), args: { op: 'type', text: 'x' }, frozen: ['op', 'text'], fixture: true });
  frontCardStore.set({ id: 'fixture-k1', shownAt: performance.now() - 10_000, at: Date.now() - 10_000 });
  installKeys((line) => reported.push(line));
  assert.equal(handlers.length, 1);
  const onKey = handlers[0]!;

  const presses = [
    { key: 'Enter', code: 'Enter', keyCode: 13, ctrlKey: true },
    { key: 'Backspace', code: 'Backspace', keyCode: 8, ctrlKey: true },
    { key: 'Enter', code: 'Enter', keyCode: 13 },
    { key: 'Enter', code: 'Enter', keyCode: 13, altKey: true },
    { key: 'Enter', code: 'Enter', keyCode: 13, metaKey: true },
    { key: 'Enter', code: 'Enter', keyCode: 13, shiftKey: true },
    { key: ' ', code: 'Space', keyCode: 32 },
    { key: 'y', code: 'KeyY', keyCode: 89 },
    { key: 'a', code: 'KeyA', keyCode: 65, ctrlKey: true },
    { key: 'Tab', code: 'Tab', keyCode: 9 },
    { key: 'Escape', code: 'Escape', keyCode: 27 },
  ];
  for (const p of presses) onKey(key(p));
  for (const p of presses) onKey(key({ ...p, repeat: true }));

  assert.deepEqual(responded, []);
  assert.equal(approvalsStore.get()[0]!.sent, null);
  assert.ok(reported.some((l) => l.includes('via=ctrl+enter outcome=refused: no key answers a card')));

  assert.equal(answerCard('approve', 'ctrl+enter'), false);
  assert.equal(answerCard('approve', 'enter-on-button'), false);
  assert.deepEqual(responded, []);

  assert.equal(answerCard('deny', 'click:mouse'), true);
  assert.deepEqual(responded, [['fixture-k1', 'deny', undefined]]);
});
