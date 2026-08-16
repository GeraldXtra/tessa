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
 * ─── editing, and the wire that now carries it ───
 * The fields are editable because dictation is unbounded arbitrary text and
 * the card is the mechanism that fixes it, not a safety net.
 *
 * The previous build had to FAIL CLOSED here: §5.1 was
 * `{ requestId, decision, remember? }` with no field for edited arguments, so
 * approving an edited card would have authorised the original mangled text
 * while he read his correction. Session 1 has since added `editedArgs?`, so
 * APPROVE now sends what is in the boxes and the daemon executes THAT. Only the
 * changed keys go — see `editedArgsFor`.
 *
 * The daemon's own guards are worth knowing while reading this file, because
 * the card is written not to provoke them: the tool and the tier are never read
 * from the frame, keys must be a subset of the request, types must match, and
 * the payload is capped at 16 KB (`core/brain/approvals.py::resolve_edit`).
 * A refusal from any of those puts the request BACK daemon-side, so the card
 * returns with his edit intact rather than making him type it again.
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
  editedArgsBytes,
  editedArgsFor,
  effectiveValue,
  isActionable,
  isEdited,
  isEditable,
  isFieldEdited,
  MAX_EDITED_ARGS_BYTES,
  STACK_VISIBLE,
  type ApprovalEntry,
} from '../state/approval-store.ts';
import { connectionStore, useStore } from '../state/store.ts';

/** Shown once an edit would exceed what the daemon will accept. */
const OVERSIZE =
  `Your edit is over the ${Math.round(MAX_EDITED_ARGS_BYTES / 1024)} KB the daemon accepts ` +
  `for edited arguments. Shorten it — approving now would be refused.`;

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

function Card({
  entry,
  linkUp,
  onDecide,
}: {
  entry: ApprovalEntry;
  linkUp: boolean;
  onDecide: (id: string, d: ApprovalDecision) => void;
}) {
  const { request } = entry;
  const edited = isEdited(entry);
  const bytes = editedArgsBytes(entry);
  const oversize = bytes > MAX_EDITED_ARGS_BYTES;
  // Actionable needs the LINK as well as the card's own state. A card with no
  // socket behind it must not offer a button that silently does nothing —
  // Session 1's ruling 2 means the request is still alive, so the honest
  // rendering is "still pending, cannot answer from here yet".
  //
  // A FIXTURE is exempt, because a fixture decision never goes on the wire:
  // main resolves it locally and refuses to forward it. Gating it on the link
  // made every fixture card un-actionable whenever no daemon was running, which
  // is both untrue and the state most verification runs are in.
  const needsLink = !request.fixture;
  const live = isActionable(entry) && (linkUp || !needsLink) && !oversize;
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

      {edited ? (
        <p className="approval__size" data-over={oversize}>
          {oversize
            ? OVERSIZE
            : `Approving sends your edit — ${bytes} of ${MAX_EDITED_ARGS_BYTES} bytes. ` +
              `The daemon executes what is in these boxes, and audits both versions.`}
        </p>
      ) : null}

      {/* The daemon said no. Kept ON the card, not in a toast: the reason is
          about this payload and he is about to edit this payload again. */}
      {entry.refusal ? (
        <p className="approval__refused">
          The daemon refused this ({entry.refusal.code}): {entry.refusal.message}
          {entry.invalidated ? '' : ' — your edit is intact; correct it and try again.'}
        </p>
      ) : null}

      {/* Suppressed when a refusal is showing. Both bands rendering at once
          produced "The daemon refused this (notFound): …" immediately above
          "Recorded locally: unknown. Nothing was sent" — two sentences about
          the same event, one of them false: something WAS sent, and it was
          refused. The refusal is the specific, true account; this generic band
          is for the paths that have no refusal to report. */}
      {entry.invalidated && !entry.refusal ? (
        <p className="approval__void">
          {entry.invalidated === 'daemonRestarted'
            ? 'The daemon restarted. It does not carry approvals across a restart, so this ' +
              'request is gone — nothing ran, and nothing is queued. Ask again if you still want it.'
            : entry.invalidated === 'expired'
              ? 'The 30-minute approval window lapsed. Treated as REJECTED; nothing was sent.'
              : // A fixture has no daemon behind it, so it must not claim one.
                // An earlier run of this card said "Resolved by the daemon: deny"
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

      {/* Link down, request alive. The distinction Session 1's ruling 2 makes,
          said in words: this did not go away, you just cannot answer it yet. */}
      {!linkUp && needsLink && !entry.invalidated && !entry.sent ? (
        <p className="approval__waiting">
          No connection to the daemon. This request is still pending on its side — it survives
          this window closing — but it cannot be answered from here until the link returns.
        </p>
      ) : null}

      <div className="approval__actions">
        {edited && isActionable(entry) ? (
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
              disabled={!live}
              title={oversize ? OVERSIZE : undefined}
              onClick={() => onDecide(request.requestId, 'approve')}
            >
              {edited ? 'approve edited' : 'approve'}
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
  const connection = useStore(connectionStore);
  const linkUp = connection.phase === 'connected';

  const onDecide = useCallback((requestId: string, decision: ApprovalDecision) => {
    // Read the edit BEFORE claiming: `approvalClaim` mutates the entry, and
    // reading the store afterwards would race its own write.
    const entry = approvalsStore.get().find((e) => e.request.requestId === requestId);
    if (!entry) return;
    // A deny executes nothing, so it never carries edited arguments — main
    // refuses that combination anyway, and sending it would be asking for a
    // refusal the surface itself created.
    const edited = decision === 'approve' ? editedArgsFor(entry) : undefined;

    // Claim SECOND. `approvalClaim` is a check-and-set: if it returns false the
    // card was already decided or already void, and nothing goes on the wire.
    // This is the fast layer; main refuses an unknown id independently.
    if (!approvalClaim(requestId, decision)) return;
    window.zoey.respondToApproval(requestId, decision, edited);
  }, []);

  if (entries.length === 0) return null;

  const shown = entries.slice(0, STACK_VISIBLE);
  const queued = entries.length - shown.length;

  return (
    <ul className="approvals" aria-label="Approval requests">
      {shown.map((entry) => (
        <Card
          key={entry.request.requestId}
          entry={entry}
          linkUp={linkUp}
          onDecide={onDecide}
        />
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
