import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { renderToStaticMarkup } from 'react-dom/server';

import { CARD_FIXTURE_KINDS, cardFixture, type FixtureCard } from '../src/main/card-fixtures.ts';
import { INVISIBLE_INNER } from '../src/shared/cards.ts';
import type { PermissionRequest } from '../src/shared/ipc-contract.ts';
import { approvalArrived, approvalsStore } from '../src/renderer/state/approval-store.ts';
import { Card } from '../src/renderer/ui/Card.tsx';
import { CardDetail, DENSE_MARKS, TextBox } from '../src/renderer/ui/CardDetail.tsx';
import { cardModel } from '../src/renderer/ui/card-view.ts';

const RAW_INVISIBLE = new RegExp(`[${INVISIBLE_INNER}]`, 'u');

function request(base: FixtureCard, frozen?: string[]): PermissionRequest {
  return { ...base, expiresAt: new Date(Date.now() + 60_000).toISOString(), receivedAt: Date.now(), fixture: true, ...(frozen ? { frozen } : {}) };
}

function renderCard(req: PermissionRequest): string {
  approvalsStore.set([]);
  approvalArrived(req);
  return renderToStaticMarkup(<Card width={372} />);
}

function detail(kind: string): string {
  const base = cardFixture(kind)![0]!;
  return renderToStaticMarkup(<CardDetail model={cardModel(base.tool, base.args)} />);
}

test('markup in a card is text: no element comes from content', () => {
  const html = detail('write');
  assert.ok(html.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
  assert.ok(html.includes('&lt;b&gt;this is not bold&lt;/b&gt;'));
  assert.ok(html.includes('**not markdown**'));
  assert.ok(html.includes('[not a link](https://example.invalid)'));
  assert.equal(/<(img|b|a|script|iframe|style|link)[\s>]/i.test(html), false);
});

test('a hostile old-style card is still text', () => {
  const html = renderCard(request({ requestId: 'fixture-x', tier: 'red', tool: 'x.post', provenance: 'external', expiresAt: '', args: { text: '<script>alert(1)</script><a href="https://example.invalid">x</a>', replyTo: '<img src=x>' } }));
  assert.equal(/<(script|a|img|iframe|style|link)[\s>]/i.test(html), false);
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('&lt;a href=&quot;https://example.invalid&quot;&gt;'));
});

test('every new card renders with no raw invisible or control character left in it', () => {
  for (const kind of CARD_FIXTURE_KINDS) {
    const html = detail(kind);
    assert.equal(RAW_INVISIBLE.test(html.replace(/[\t\n]/g, '')), false, kind);
  }
});

test('marks render in their own element so they cannot pass for file text', () => {
  const html = detail('runrtl');
  assert.ok(html.includes('notes<span class="esc">[U+202E]</span>txt.py'));
  assert.ok(html.includes('<span class="esc">[U+2066]</span>'));
  assert.ok(html.includes('<span class="esc">[U+0001 x5]</span>'));
  assert.ok(html.includes('<span class="esc">[U+000D]</span>'));
});

test('the run card lists every file and opens only the first one', () => {
  const base = cardFixture('run768')![0]!;
  const html = renderToStaticMarkup(<CardDetail model={cardModel(base.tool, base.args)} />);
  assert.equal((html.match(/class="fi( open)?"/g) ?? []).length, 200);
  assert.equal((html.match(/class="fi open"/g) ?? []).length, 1);
  assert.equal((html.match(/class="code"/g) ?? []).length, 1);
  assert.ok(html.includes('placeholder part 1'));
  assert.equal(html.includes('placeholder part 3'), false);
  assert.ok(html.includes('NOT CHECKED, 2'));
});

test('a sha256 shows short with the full one on demand', () => {
  const html = detail('run');
  assert.ok(/<span class="mono">[0-9a-f]{12}<\/span><button type="button" class="shab"[^>]*>FULL<\/button>/.test(html));
});

test('the new cards offer no editing even when the daemon froze nothing', () => {
  for (const kind of CARD_FIXTURE_KINDS) {
    const html = renderCard(request(cardFixture(kind)![0]!, []));
    assert.equal(html.includes('<textarea'), false, kind);
    assert.ok(html.includes('NOTHING ON THIS CARD CAN BE CHANGED') || html.includes('FIXTURE DATA'), kind);
  }
});

test('older cards that allow editing keep it', () => {
  const html = renderCard(request({ requestId: 'fixture-old', tier: 'red', tool: 'x.post', provenance: 'agent', expiresAt: '', args: { text: 'placeholder post' } }, []));
  assert.ok(html.includes('<textarea'));
});

test('an editable field holding invisible characters also shows them by name', () => {
  const html = renderCard(request({ requestId: 'fixture-old2', tier: 'red', tool: 'x.post', provenance: 'agent', expiresAt: '', args: { text: 'placeholder\u202Epost' } }, []));
  assert.ok(html.includes('placeholder<span class="esc">[U+202E]</span>post'));
});

test('the buttons carry no key hint and the card says only a click answers it', () => {
  const html = renderCard(request(cardFixture('type')![0]!));
  assert.equal(html.includes('<kbd'), false);
  assert.ok(/>APPROVE<\/button>/.test(html));
  assert.ok(/>DENY<\/button>/.test(html));
  assert.ok(html.includes('ONLY A CLICK ANSWERS'));
});

test('the app card shows what approving means beside the buttons', () => {
  const html = renderCard(request(cardFixture('app')![0]!));
  const apv = html.indexOf('class="apv"');
  const btns = html.indexOf('class="btns"');
  assert.ok(apv > 0 && btns > apv);
  assert.ok(html.slice(apv, btns).includes('Approving trusts every app signed by Example Publisher Ltd'));
});

test('a part dense with invisible characters renders as one marked block, never raw', () => {
  const text = 'a\u202Eb\u2066'.repeat(DENSE_MARKS);
  const html = renderToStaticMarkup(<TextBox text={text} label="dense" />);
  assert.ok(html.includes('<div class="ck dense">'));
  assert.ok(html.includes('RUNS OF INVISIBLE OR CONTROL CHARACTERS IN THIS PART'));
  assert.ok(html.includes('a[U+202E]b[U+2066]a[U+202E]'));
  assert.equal(html.includes('class="esc"'), false);
  assert.equal(RAW_INVISIBLE.test(html), false);
});

test('a part with few marks keeps each mark in its own element', () => {
  const html = renderToStaticMarkup(<TextBox text={'x\u202Ey'.repeat(10)} label="sparse" />);
  assert.equal(html.includes('dense'), false);
  assert.equal((html.match(/class="esc"/g) ?? []).length, 10);
});
