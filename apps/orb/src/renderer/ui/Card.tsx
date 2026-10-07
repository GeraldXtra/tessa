import { forwardRef, useCallback, useLayoutEffect, useRef } from 'react';

import type { ApprovalDecision } from '../../shared/ipc-contract.ts';
import {
  MAX_EDITED_ARGS_BYTES,
  approvalClaim,
  approvalEdited,
  approvalReverted,
  approvalsStore,
  editedArgsBytes,
  editedArgsFor,
  effectiveValue,
  isActionable,
  isEdited,
  isFieldEdited,
  type ApprovalEntry,
} from '../state/approval-store.ts';
import { connectionStore, useStore } from '../state/store.ts';
import { TIER_MEANING, hmsOf, tierOf } from './format.ts';
import { ANSWER_LOCK_MS, frontCardStore } from './stores.ts';

const WHO: Readonly<Record<string, string>> = {
  human: 'You, typed or said',
  agent: 'Her own plan, the model proposed it',
  schedule: 'A scheduled job',
  external: 'Content fetched from off this machine',
  program: 'Output of a program on this machine',
  system: 'The daemon itself',
};

export function liveEntries(entries: readonly ApprovalEntry[]): ApprovalEntry[] {
  return entries.filter((e) => e.invalidated === null);
}

function report(line: string): void {
  window.tessa.reportMetrics(line);
}

export function answerCard(decision: ApprovalDecision, via: string): boolean {
  const entry = liveEntries(approvalsStore.get())[0];
  const front = frontCardStore.get();
  const id = entry?.request.requestId ?? 'none';
  const out = (outcome: string): boolean => {
    report(`CARD-ANSWER decision=${decision} via=${via} request=${id} outcome=${outcome}`);
    return outcome.startsWith('sent');
  };
  if (!entry || !front || front.id !== entry.request.requestId) return out('refused: no card in front');
  const age = performance.now() - front.shownAt;
  if (age < ANSWER_LOCK_MS) return out(`refused: ${age.toFixed(0)} ms after the card appeared, lock is ${ANSWER_LOCK_MS} ms`);
  if (!entry.request.fixture && connectionStore.get().phase !== 'connected') return out('refused: no daemon link');
  if (!isActionable(entry)) return out(`refused: already ${entry.sent ? `sent ${entry.sent}` : 'void'}`);
  const edited = decision === 'approve' ? editedArgsFor(entry) : undefined;
  if (edited && editedArgsBytes(entry) > MAX_EDITED_ARGS_BYTES) return out('refused: the edit is over the 16 KB the daemon accepts');
  if (!approvalClaim(entry.request.requestId, decision)) return out('refused: claim failed');
  window.tessa.respondToApproval(entry.request.requestId, decision, edited);
  return out(`sent${edited ? ' with editedArgs' : ''}${entry.request.fixture ? ' (fixture: main resolves it locally)' : ''}`);
}

function ArgField({ entry, name, editable }: { entry: ApprovalEntry; name: string; editable: boolean }) {
  const original = entry.request.args[name];
  const live = isActionable(entry);
  const onChange = useCallback(
    (event: React.ChangeEvent<HTMLTextAreaElement>) => approvalEdited(entry.request.requestId, name, event.target.value),
    [entry.request.requestId, name],
  );
  const edited = isFieldEdited(entry, name);
  const value = editable || edited ? effectiveValue(entry, name) : typeof original === 'string' ? original : JSON.stringify(original);
  return (
    <div className="arg" data-arg={name}>
      <span className="an">
        {name.toUpperCase()}
        {edited ? <span className="amb"> EDITED</span> : null}
        {editable ? null : <span className="ro"> READ-ONLY</span>}
      </span>
      {editable ? (
        <textarea
          className="cfield"
          value={value}
          onChange={onChange}
          disabled={!live}
          spellCheck={false}
          rows={Math.min(6, Math.max(1, Math.ceil(value.length / 44), value.split('\n').length))}
          aria-label={`${name}, editable`}
        />
      ) : (
        <div className="cfixed">{value}</div>
      )}
    </div>
  );
}

export const Card = forwardRef<HTMLElement, { width: number }>(function Card({ width }, ref) {
  const entries = useStore(approvalsStore);
  const connection = useStore(connectionStore);
  const live = liveEntries(entries);
  const entry = live[0];
  const armed = useRef<{ decision: ApprovalDecision; pointer: string } | null>(null);
  const frontId = entry?.request.requestId ?? null;

  useLayoutEffect(() => {
    frontCardStore.set(frontId ? { id: frontId, shownAt: performance.now(), at: Date.now() } : null);
    if (frontId) report(`CARD-SHOWN request=${frontId} lock=${ANSWER_LOCK_MS}ms`);
  }, [frontId]);

  if (!entry) return null;
  const { request } = entry;
  const n = live.length;
  const tier = tierOf(request);
  const frozenKnown = Array.isArray(request.frozen);
  const frozen = new Set(request.frozen ?? []);
  const names = Object.keys(request.args);
  const linkUp = connection.phase === 'connected';
  const usable = isActionable(entry) && (linkUp || request.fixture === true);
  const oversize = editedArgsBytes(entry) > MAX_EDITED_ARGS_BYTES;
  const expires = Date.parse(request.expiresAt);

  const onPointerDown = (decision: ApprovalDecision) => (e: React.PointerEvent) => {
    armed.current = e.pointerType === 'mouse' || e.pointerType === 'pen' || e.pointerType === 'touch' ? { decision, pointer: e.pointerType } : null;
  };
  const onClick = (decision: ApprovalDecision) => (e: React.MouseEvent) => {
    const arm = armed.current;
    armed.current = null;
    if (e.detail < 1 || !arm || arm.decision !== decision) {
      report(`CARD-ANSWER decision=${decision} via=click request=${request.requestId} outcome=refused: not a pointer click (detail=${e.detail}, armed=${arm ? arm.decision : 'no'})`);
      return;
    }
    answerCard(decision, `click:${arm.pointer}`);
  };
  const onKeyDown = (decision: ApprovalDecision) => (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault();
      report(`CARD-ANSWER decision=${decision} via=${e.key === 'Enter' ? 'enter' : 'space'}-on-button request=${request.requestId} outcome=refused: keys on a button never answer`);
    }
  };
  const onKeyUp = (e: React.KeyboardEvent) => {
    if (e.key === ' ' || e.key === 'Spacebar') e.preventDefault();
  };

  return (
    <section ref={ref} className="slot card" style={{ width }} tabIndex={-1} role="alertdialog" aria-label="Approval needed" data-request={request.requestId}>
      <div className="cb">
        <span>WAITING ON YOU</span>
        <span>{n > 1 ? `1 OF ${n}` : 'APPROVAL'}</span>
      </div>
      <div className="bd">
        {request.fixture ? <div className="fx">FIXTURE CARD. NO DECISION ON IT REACHES THE DAEMON.</div> : null}
        <div className="tierline">
          <span>{tier ? tier.toUpperCase() : request.tier.toUpperCase()}</span>
          <span>{tier ? TIER_MEANING[tier].toUpperCase() : ''}</span>
        </div>
        <div className="act2">{request.tool}</div>
        <div className="det">
          {names.length === 0 ? <div className="dim">No arguments.</div> : names.map((name) => (
            <ArgField key={name} entry={entry} name={name} editable={frozenKnown && typeof request.args[name] === 'string' && !frozen.has(name)} />
          ))}
        </div>
        <dl>
          <dt>FROM</dt>
          <dd>
            {request.provenance.toUpperCase()}
            {WHO[request.provenance] ? <span className="dim">, {WHO[request.provenance]}</span> : null}
          </dd>
          {Number.isFinite(expires) ? (
            <>
              <dt>EXPIRES</dt>
              <dd>{hmsOf(request.expiresAt)}</dd>
            </>
          ) : null}
        </dl>
        {isEdited(entry) ? (
          <div className={`cnote${oversize ? ' amb' : ''}`}>
            {oversize
              ? `Your edit is over the ${Math.round(MAX_EDITED_ARGS_BYTES / 1024)} KB the daemon accepts. Shorten it.`
              : `APPROVE sends your edit, ${editedArgsBytes(entry)} of ${MAX_EDITED_ARGS_BYTES} bytes.`}
            {isActionable(entry) ? (
              <button type="button" className="revert" onClick={() => approvalReverted(request.requestId)}>
                REVERT EDITS
              </button>
            ) : null}
          </div>
        ) : null}
        {entry.refusal ? (
          <div className="cnote">
            The daemon refused this ({entry.refusal.code}): {entry.refusal.message}. Your edit is intact.
          </div>
        ) : null}
        {entry.sent ? <div className="cnote">Sent {entry.sent.toUpperCase()}. Waiting for the daemon.</div> : null}
        {!linkUp && !request.fixture && !entry.sent ? (
          <div className="cnote">No daemon link. The request is still pending on the daemon and cannot be answered from here until the link returns.</div>
        ) : null}
      </div>
      <div className="btns">
        <button
          type="button"
          className="ok"
          disabled={!usable || oversize}
          onPointerDown={onPointerDown('approve')}
          onClick={onClick('approve')}
          onKeyDown={onKeyDown('approve')}
          onKeyUp={onKeyUp}
        >
          APPROVE <kbd>CTRL ENTER</kbd>
        </button>
        <button
          type="button"
          className="no"
          disabled={!usable}
          onPointerDown={onPointerDown('deny')}
          onClick={onClick('deny')}
          onKeyDown={onKeyDown('deny')}
          onKeyUp={onKeyUp}
        >
          DENY <kbd>CTRL BKSP</kbd>
        </button>
      </div>
      <div className="cf">
        {request.fixture
          ? 'FIXTURE DATA. THE SPHERE STAYS VISIBLE AND STILL.'
          : frozenKnown
            ? 'A PLAIN ENTER NEVER APPROVES. THE SPHERE STAYS VISIBLE AND STILL.'
            : 'READ-ONLY: THIS REQUEST ARRIVED WITHOUT ITS FROZEN LIST.'}
      </div>
      {n > 1 ? <div className="stack" /> : null}
    </section>
  );
});

export function cardFieldHasFocus(): boolean {
  const el = document.activeElement;
  return el instanceof HTMLTextAreaElement && el.classList.contains('cfield');
}
