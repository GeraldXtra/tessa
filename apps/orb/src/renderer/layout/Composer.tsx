/**
 * THE COMPOSE BOX — one typed input to the active companion, pinned to the
 * bottom of the TRACE drawer. The transcript above it is the same renderer
 * the voice path uses (rails/TracePanel.tsx); this file adds an input and
 * nothing that draws a line.
 *
 * ─── the one rule everything here bends around ───
 * NOTHING IS RENDERED THAT THE DAEMON DID NOT SEND. His own line appears in
 * the transcript only when `evt.transcript.message` echoes it back; the box
 * clears only when `res.agent.accepted` arrives; the hint line's error text
 * is the daemon's `message`, the client's own 3 s silence, or the character
 * cap — never a status invented here. The sphere and the state chip already
 * show thinking, so there is no spinner, no "sending…", no second indicator.
 *
 * ─── keys ───
 *   Enter          send. Shift+Enter is a newline (the textarea's default).
 *   Escape         clear the draft if there is one; else cancel the turn in
 *                  flight (`cmd.agent.cancel`); else let it through, and the
 *                  drawer closes as it always has.
 *   Ctrl+Shift+Space  opens TRACE and focuses this box from anywhere in the
 *                  Orb, including the canvas. Bound in App.tsx beside Escape.
 *
 * ─── why the draft is not component state ───
 * See state/compose-store.ts. The drawer unmounts this panel when another
 * rail opens, and the approval card closes the drawer; the draft has to
 * outlive both.
 */

import { useCallback, useEffect, useLayoutEffect, useRef } from 'react';

import { AGENT_TEXT_MAX } from '../../shared/ipc-contract.ts';
import {
  composeAccepted,
  composePatch,
  composeRefused,
  composeStore,
} from '../state/compose-store.ts';
import { latencyMarkAck, latencyMarkSent } from '../state/latency.ts';
import { companionStore, connectionStore, devStore, railStore, useStore } from '../state/store.ts';

const HINT_DEFAULT = 'ENTER TO SEND · SHIFT+ENTER NEW LINE';
const HINT_OFFLINE = 'NO DAEMON';

const count = (n: number): string => n.toLocaleString('en-US');
const tooLong = (n: number): string =>
  `TOO LONG: ${count(n)} OF ${count(AGENT_TEXT_MAX)} CHARACTERS`;

export function Composer() {
  const compose = useStore(composeStore);
  const companion = useStore(companionStore);
  const connection = useStore(connectionStore);
  const rail = useStore(railStore);
  const isDev = useStore(devStore);

  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const online = connection.phase === 'connected';
  const over = compose.draft.length > AGENT_TEXT_MAX;

  // The chord asked for focus. A counter rather than a boolean so two
  // requests in a row both land.
  useEffect(() => {
    if (compose.focusRequest > 0) fieldRef.current?.focus();
  }, [compose.focusRequest]);

  // The drawer closed under a focused box — Escape, or the approval card.
  // A focused control inside an aria-hidden panel keeps eating keystrokes
  // into something he cannot see, so focus is released with the drawer.
  useEffect(() => {
    if (rail !== 'trace' && document.activeElement === fieldRef.current) fieldRef.current?.blur();
  }, [rail]);

  /**
   * Keystroke → glyph, dev only. `lastKeyAt` is stamped in the keydown
   * handler for printable keys; the layout effect below runs after React
   * commits the new value and asks for the next frame, which is the one that
   * paints the glyph. One rAF per keystroke, only in a dev build.
   */
  const lastKeyAt = useRef<number | null>(null);
  useLayoutEffect(() => {
    const at = lastKeyAt.current;
    if (at === null) return;
    lastKeyAt.current = null;
    if (!isDev) return;
    requestAnimationFrame(() => {
      window.tessa.reportMetrics(`KEY-PAINT keydownToPaint=${(performance.now() - at).toFixed(2)}`);
    });
  }, [compose.draft, isDev]);

  const send = useCallback(async () => {
    const s = composeStore.get();
    if (!online || s.awaitingAck) return;
    const text = s.draft;
    if (text.trim().length === 0) return;
    if (text.length > AGENT_TEXT_MAX) {
      // Refused, with the number. Never truncated.
      composePatch({ error: tooLong(text.length) });
      return;
    }
    const sentAt = performance.now();
    latencyMarkSent(sentAt);
    composePatch({ awaitingAck: { text, sentAt }, error: null });
    const result = await window.tessa.agentSend(companion.id, text);
    if (result.ok) {
      latencyMarkAck();
      composeAccepted(text, result.messageId, performance.now());
    } else {
      composeRefused(result.error);
    }
  }, [online, companion.id]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        // Stopped here so the window-level handlers never see it.
        event.preventDefault();
        event.stopPropagation();
        void send();
        return;
      }
      if (event.key === 'Escape') {
        const s = composeStore.get();
        if (s.draft.length > 0) {
          event.preventDefault();
          event.stopPropagation();
          composePatch({ draft: '', error: null });
          return;
        }
        if (s.inflight) {
          event.preventDefault();
          event.stopPropagation();
          window.tessa.agentCancel(companion.id, s.inflight.messageId);
          return;
        }
        // Nothing to clear, nothing to cancel: fall through to App.tsx,
        // which closes the drawer on Escape as it always has.
        return;
      }
      if (event.key.length === 1 || event.key === 'Backspace' || event.key === 'Delete') {
        lastKeyAt.current = performance.now();
      }
    },
    [send, companion.id],
  );

  const hint = !online
    ? HINT_OFFLINE
    : (compose.error ?? (over ? tooLong(compose.draft.length) : HINT_DEFAULT));
  const tone = online && (compose.error !== null || over) ? 'error' : 'muted';

  return (
    <div
      className="compose"
      data-online={online}
      data-awaiting={compose.awaitingAck !== null}
      data-inflight={compose.inflight !== null}
    >
      <span className="compose__label" id="compose-label">
        {companion.name}
      </span>
      <textarea
        ref={fieldRef}
        className="compose__field"
        value={compose.draft}
        rows={1}
        spellCheck={false}
        autoComplete="off"
        disabled={!online}
        placeholder={online ? `Type to ${companion.name}` : undefined}
        aria-labelledby="compose-label"
        aria-describedby="compose-hint"
        aria-invalid={tone === 'error' || undefined}
        onChange={(event) => composePatch({ draft: event.target.value, error: null })}
        onKeyDown={onKeyDown}
      />
      <span className="compose__hint" id="compose-hint" data-tone={tone}>
        {hint}
      </span>
    </div>
  );
}
