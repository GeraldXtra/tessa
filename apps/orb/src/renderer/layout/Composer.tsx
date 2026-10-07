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
 *
 * ─── why the draft is not component state ───
 * See state/compose-store.ts. The drawer unmounts this panel when another
 * rail opens, and the approval card closes the drawer; the draft has to
 * outlive both.
 */

import { useCallback, useEffect, useId, useLayoutEffect, useRef } from 'react';

import { AGENT_TEXT_MAX } from '../../shared/ipc-contract.ts';
import {
  closeQuick,
  composeAccepted,
  composePatch,
  composeRefused,
  composeStore,
  composeUnsentOnDrop,
  type ComposePlace,
} from '../state/compose-store.ts';
import { latencyMarkAck, latencyMarkSent } from '../state/latency.ts';
import { companionStore, connectionStore, devStore, railStore, useStore } from '../state/store.ts';
import { cardWaiting } from '../ui/keys.ts';
import { pushNote } from '../ui/stores.ts';

const HINT_DEFAULT = 'ENTER';
const HINT_OFFLINE = 'NO DAEMON';

const count = (n: number): string => n.toLocaleString('en-US');
const tooLong = (n: number): string =>
  `TOO LONG: ${count(n)} OF ${count(AGENT_TEXT_MAX)} CHARACTERS`;

const OFFLINE_PLACEHOLDER: Readonly<Record<string, string>> = {
  connecting: 'Connecting to the daemon. Cannot send yet.',
  offline: 'Daemon offline. Cannot send.',
  reconnecting: 'Daemon offline. Cannot send.',
  authRejected: 'The daemon refused this Orb. Cannot send.',
  protocolMismatch: 'Protocol mismatch with the daemon. Cannot send.',
};

const focusConsumed = { n: 0 };

export function Composer({ place }: { place: ComposePlace }) {
  const compose = useStore(composeStore);
  const companion = useStore(companionStore);
  const connection = useStore(connectionStore);
  const rail = useStore(railStore);
  const isDev = useStore(devStore);
  const uid = useId();
  const labelId = `compose-label-${uid}`;
  const hintId = `compose-hint-${uid}`;

  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const online = connection.phase === 'connected';
  const over = compose.draft.length > AGENT_TEXT_MAX;

  // The chord asked for focus. A counter rather than a boolean so two
  // requests in a row both land.
  useEffect(() => {
    if (compose.focusPlace !== place || compose.focusRequest <= focusConsumed.n) return;
    focusConsumed.n = compose.focusRequest;
    fieldRef.current?.focus();
  }, [compose.focusRequest, compose.focusPlace, place]);

  // The drawer closed under a focused box — Escape, or the approval card.
  // A focused control inside an aria-hidden panel keeps eating keystrokes
  // into something he cannot see, so focus is released with the drawer.
  useEffect(() => {
    if (place === 'drawer' && rail !== 'chat' && document.activeElement === fieldRef.current) fieldRef.current?.blur();
  }, [rail, place]);

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
    composePatch({ awaitingAck: { text, sentAt, from: place }, error: null });
    const result = await window.tessa.agentSend(companion.id, text);
    if (result.ok) {
      latencyMarkAck();
      composeAccepted(text, result.messageId, performance.now());
    } else {
      composeRefused(result.error);
      if ((result.code === 'unavailable' || connectionStore.get().phase !== 'connected') && composeUnsentOnDrop(text, sentAt, Date.now())) {
        pushNote('CHAT', 'Your message was not confirmed before the link dropped. Nothing was queued. It is still in the box.');
      }
    }
  }, [online, companion.id, place]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        // Stopped here so the window-level handlers never see it.
        event.preventDefault();
        event.stopPropagation();
        if ((event.ctrlKey || event.altKey || event.metaKey) && cardWaiting()) {
          const via = `${event.ctrlKey ? 'ctrl+' : ''}${event.altKey ? 'alt+' : ''}${event.metaKey ? 'meta+' : ''}enter`;
          window.tessa.reportMetrics(
            `CARD-ANSWER decision=approve via=${via} outcome=refused: focus is in the ${place} chat input, not on the card. Nothing sent.`,
          );
          return;
        }
        void send();
        return;
      }
      if (place !== 'drawer' && event.key === 'Enter') {
        event.preventDefault();
        return;
      }
      if (event.key.length === 1 || event.key === 'Backspace' || event.key === 'Delete') {
        lastKeyAt.current = performance.now();
      }
    },
    [send, place],
  );

  const onBlur = useCallback(() => {
    if (place === 'quick' && document.hasFocus() && composeStore.get().draft.trim().length === 0) closeQuick();
  }, [place]);

  const quickError = place === 'quick' && online && compose.error !== null ? `NOT SENT: ${compose.error}` : null;
  const hint = !online ? HINT_OFFLINE : over ? tooLong(compose.draft.length) : (quickError ?? HINT_DEFAULT);
  const tone = online && (compose.error !== null || over) ? 'error' : 'muted';
  const placeholder = online
    ? place === 'quick'
      ? `Message ${companion.name}. Enter sends, Esc cancels`
      : `Message ${companion.name}`
    : (OFFLINE_PLACEHOLDER[connection.phase] ?? 'Daemon offline. Cannot send.');

  return (
    <div
      className={place === 'quick' ? 'qin' : 'cin'}
      data-online={online}
      data-awaiting={compose.awaitingAck !== null}
      data-inflight={compose.inflight !== null}
    >
      <span className="p" id={labelId} aria-label={`Message ${companion.name}`}>
        {'>'}
      </span>
      <textarea
        ref={fieldRef}
        className="compose__field"
        data-place={place}
        value={compose.draft}
        rows={1}
        spellCheck={false}
        autoComplete="off"
        disabled={!online}
        placeholder={placeholder}
        aria-labelledby={labelId}
        aria-describedby={hintId}
        aria-invalid={tone === 'error' || undefined}
        onChange={(event) => composePatch({ draft: event.target.value, error: null })}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
      />
      <kbd id={hintId} data-tone={tone} title={quickError ?? undefined}>
        {hint}
      </kbd>
    </div>
  );
}
