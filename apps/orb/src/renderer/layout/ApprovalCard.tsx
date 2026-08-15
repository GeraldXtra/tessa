/**
 * The approval card. CONTRACT §4.1 / §5.1 — and a security surface.
 *
 * Everything below follows from one sentence: the owner is authorising a
 * SPECIFIC ACT, and what he reads has to be that act.
 *
 * ─── the payload is DATA, and is rendered as data ───
 * Every argument reaches the screen through JSX text interpolation or a
 * controlled `<textarea value>`. There is no `dangerouslySetInnerHTML` in this
 * file, no `innerHTML`, and nothing is parsed. A tool argument containing
 * `<button>APPROVE</button>` renders as those twenty-five characters. That is
 * not a convention here, it is the only mechanism used.
 *
 * ─── no payload may imitate the card ───
 * React escaping stops markup, but it does not stop a payload that simply SAYS
 * "APPROVE" in convincing words. So the chrome is fenced structurally, not
 * typographically: every argument is rendered inside `.approval__payload`,
 * which is inset, has its own surface, its own hairline, and a literal PAYLOAD
 * label. The tier badge, the provenance, and the two buttons all live OUTSIDE
 * that box and are the only interactive elements on the card. Text inside the
 * box cannot become a button, cannot escape the box, and is visibly not chrome.
 *
 * ─── the buttons cannot be pushed off screen ───
 * `.approval__payload` scrolls; the card does not. A 5,000-character tweet
 * makes the payload box scroll internally while the header and the footer stay
 * fixed, so APPROVE and REJECT are reachable at any payload size. The character
 * count is shown for exactly the reason the scroll bar is not enough: he needs
 * to know there is more, not just be able to find it.
 *
 * ─── there is no default and no timeout-approves ───
 * Neither button is focused on mount, neither is `type="submit"`, and Enter
 * does nothing. Expiry invalidates the card (see approvalsSweepExpired) and
 * sends NOTHING — CONTRACT §5.1 reserves `expired` to the daemon.
 *
 * ─── editing, and the wire that cannot carry it ───
 * The fields are editable because dictation is unbounded arbitrary text and
 * the card is the mechanism that fixes it, not a safety net. But CONTRACT §5.1
 * defines `cmd.permission.respond` as `{ requestId, decision, remember? }` —
 * there is no field for edited arguments, and this session may not invent one.
 * So an edited card FAILS CLOSED: APPROVE is refused with the reason named on
 * screen, REJECT stays live. Sending `approve` after an edit would authorise
 * the ORIGINAL text while he reads his correction, which is the single worst
 * outcome this card can produce.
 */

import { useCallback } from 'react';

import type { ApprovalDecision } from '../../shared/ipc-contract.ts';
import {
  approvalClaim,
  approvalDismissed,
  approvalEdited,
  approvalReverted,
  approvalsStore,
  approvalOverflowStore,
  effectiveValue,
  isActionable,
  isEdited,
  isEditable,
  isFieldEdited,
  STACK_VISIBLE,
  type ApprovalEntry,
} from '../state/approval-store.ts';
import { useStore } from '../state/store.ts';

/** Why APPROVE is refused after an edit. Names the field that does not exist. */
const EDIT_BLOCKED =
  'CONTRACT §5.1 defines cmd.permission.respond as { requestId, decision, remember? }. ' +
  'It has no field for edited arguments, so approving now would authorise the ORIGINAL ' +
  'text, not yours. Reject this and re-issue it.';

function tierWord(tier: string): string {
  // §R.7: a tier is always shown as the WORD. Colour alone is not a label, and
  // this card is the one place where being colour-blind must cost nothing.
  return tier.trim() || 'unknown';
}

function ArgumentRow({ entry, name }: { entry: ApprovalEntry; name: string }) {
  const original = entry.request.args[name];
  const editable = isEditable(original);
  const value = editable ? effectiveValue(entry, name) : JSON.stringify(original);
  const edited = isFieldEdited(entry, name);
  const live = isActionable(entry);

  const onChange = useCallback(
    (event: React.ChangeEvent<HTMLTextAreaElement>) => {
      approvalEdited(entry.request.requestId, name, event.target.value);
    },
    [entry.request.requestId, name],
  );

  return (
    // `data-arg` and the card's `data-request` are addressing hooks, not
    // styling. They let the dev driver target ONE field on ONE card by name,
    // which is what makes the two-pending proof possible without the OS: a
    // selector that can only say "the first textarea" cannot demonstrate that
    // answering the second request left the first untouched.
    <div className="approval__arg" data-arg={name} data-edited={edited}>
      <span className="approval__arg-name">
        {name}
        {edited ? <span className="approval__arg-flag"> edited</span> : null}
      </span>

      {editable ? (
        <textarea
          className="approval__field"
          value={value}
          onChange={onChange}
          disabled={!live}
          spellCheck={false}
          rows={Math.min(12, Math.max(1, value.split('\n').length))}
          aria-label={`${name} — editable`}
        />
      ) : (
        // Not a string: shown verbatim as JSON and NOT editable. A text box
        // that turns 443 into "443" changes the action with nothing on screen
        // to show it changed.
        <code className="approval__field approval__field--fixed">{value}</code>
      )}

      <span className="approval__arg-meta">
        {editable ? `${value.length} chars` : `${typeof original} — not editable`}
      </span>
    </div>
  );
}

function Card({ entry, onDecide }: { entry: ApprovalEntry; onDecide: (id: string, d: ApprovalDecision) => void }) {
  const { request } = entry;
  const live = isActionable(entry);
  const edited = isEdited(entry);
  const names = Object.keys(request.args);

  return (
    <li
      className="approval"
      data-request={request.requestId}
      data-tier={request.tier}
      data-live={live}
      data-edited={edited}
      data-invalidated={entry.invalidated ?? 'no'}
    >
      {/* Chrome band. Fixed text, no payload reaches it — this is what makes
          the card recognisable as the Orb's own surface rather than something
          a tool argument drew. */}
      <div className="approval__chrome">
        <span className="approval__mark">ZOEY</span>
        <span className="approval__chrome-label">approval required</span>
        <span className="tier" data-tier={request.tier}>
          {tierWord(request.tier)}
        </span>
      </div>

      {request.fixture ? (
        <p className="approval__fixture">
          DEV FIXTURE — this request did not come from the daemon. Nothing can be executed by it.
        </p>
      ) : null}

      <div className="approval__head">
        <span className="approval__tool">{request.tool}</span>
        <span className="approval__prov" data-provenance={request.provenance}>
          {request.provenance}
        </span>
      </div>

      {/* THE FENCE. Everything the daemon sent is inside this box. */}
      <div className="approval__payload">
        <span className="approval__payload-label">payload</span>
        {names.length === 0 ? (
          <p className="approval__empty">no arguments</p>
        ) : (
          names.map((name) => <ArgumentRow key={name} entry={entry} name={name} />)
        )}
      </div>

      {edited && live ? <p className="approval__blocked">{EDIT_BLOCKED}</p> : null}

      {entry.invalidated ? (
        <p className="approval__void">
          {entry.invalidated === 'disconnected'
            ? 'The daemon connection dropped. This request no longer exists — nothing will run.'
            : entry.invalidated === 'expired'
              ? 'The 30-minute approval window lapsed. Treated as REJECTED; nothing was sent.'
              : // A fixture has no daemon behind it, so it must not claim one.
                // The first run of this card said "Resolved by the daemon: deny"
                // over a card main had explicitly refused to put on the wire —
                // true-sounding, and false. On this surface that is not a
                // cosmetic difference.
                request.fixture
                ? `Recorded locally: ${entry.resolved ?? 'unknown'}. Nothing was sent — this is a fixture.`
                : `Resolved by the daemon: ${entry.resolved ?? 'unknown'}.`}
        </p>
      ) : null}

      {entry.sent && !entry.invalidated ? (
        <p className="approval__sent">Sent {entry.sent} — waiting for the daemon.</p>
      ) : null}

      <div className="approval__actions">
        {edited && live ? (
          <button
            type="button"
            className="approval__revert"
            onClick={() => approvalReverted(request.requestId)}
          >
            revert edits
          </button>
        ) : null}

        {entry.invalidated ? (
          <button
            type="button"
            className="approval__dismiss"
            onClick={() => approvalDismissed(request.requestId)}
          >
            dismiss
          </button>
        ) : (
          <>
            {/* REJECT first in the DOM. Tab order reaches the safe action
                before the destructive one, and neither is autofocused. */}
            <button
              type="button"
              className="approval__btn approval__btn--reject"
              disabled={!live}
              onClick={() => onDecide(request.requestId, 'deny')}
            >
              reject
            </button>
            <button
              type="button"
              className="approval__btn approval__btn--approve"
              disabled={!live || edited}
              title={edited ? EDIT_BLOCKED : undefined}
              onClick={() => onDecide(request.requestId, 'approve')}
            >
              approve
            </button>
          </>
        )}
      </div>
    </li>
  );
}

export function ApprovalStack() {
  const entries = useStore(approvalsStore);
  const overflow = useStore(approvalOverflowStore);

  const onDecide = useCallback((requestId: string, decision: ApprovalDecision) => {
    // Claim FIRST. `approvalClaim` is a check-and-set: if it returns false the
    // card was already decided or already void, and nothing goes on the wire.
    // This is the fast layer; main refuses an unknown id independently.
    if (!approvalClaim(requestId, decision)) return;
    window.zoey.respondToApproval(requestId, decision);
  }, []);

  if (entries.length === 0) return null;

  const shown = entries.slice(0, STACK_VISIBLE);
  const queued = entries.length - shown.length;

  return (
    <ul className="approvals" aria-label="Approval requests">
      {shown.map((entry) => (
        <Card key={entry.request.requestId} entry={entry} onDecide={onDecide} />
      ))}
      {queued > 0 ? (
        <li className="approvals__more">
          {queued} more waiting — answer one to see the next
        </li>
      ) : null}
      {overflow > 0 ? (
        <li className="approvals__overflow">
          {overflow} request(s) refused: the queue is full. Nothing was discarded silently.
        </li>
      ) : null}
    </ul>
  );
}

// Re-exported so the store's own names stay importable from one place in tests
// and in App.tsx, without this file becoming the store's public interface.
export { approvalsStore };
