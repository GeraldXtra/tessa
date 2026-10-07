import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';

import type { TranscriptLine } from '../../shared/ipc-contract.ts';
import { Composer } from '../layout/Composer.tsx';
import { useComposeField, type Unconfirmed } from '../state/compose-store.ts';
import { agentStateStore, connectionStore, transcriptStore, useStore } from '../state/store.ts';
import { ErrorLine, Loading, Val } from './bits.tsx';
import { hmOf, hms, hmsOf } from './format.ts';
import { LINK_WORD } from './TopBar.tsx';

export const DOCK_LINES = 8;
export const DOCK_MIN_PX = 150;
export const EMPTY_CHAT = 'No messages yet this session. Type below, or press / from anywhere.';
const STICK_SLOP_PX = 24;

export function ChatMessage({ line, short = false }: { line: TranscriptLine; short?: boolean }) {
  return (
    <div className={`msg${line.role === 'user' ? ' me' : ''}`}>
      <div className="who">
        <span>
          {line.role === 'user' ? 'YOU' : line.role === 'assistant' ? 'TESSA' : line.role.toUpperCase()}
          {!short && line.via ? ` · ${line.via.toUpperCase()}` : ''}
        </span>
        <span>{short ? hmOf(line.ts) : hmsOf(line.ts)}</span>
      </div>
      <div className="tx">{line.text}</div>
    </div>
  );
}

export function UnconfirmedLine({ u }: { u: Unconfirmed }) {
  const sent = hms(new Date(performance.timeOrigin + u.sentAt));
  const dropped = hms(new Date(u.droppedAt));
  const preview = u.text.length > 80 ? `${u.text.slice(0, 80)}…` : u.text;
  return (
    <div className="nodata unconf" data-stage={u.stage}>
      <b>UNCONFIRMED</b>
      {u.stage === 'ack'
        ? `Sent at ${sent}. The link dropped at ${dropped} before the daemon confirmed it. Nothing was queued. The text is still in the box: "${preview}"`
        : `Sent at ${sent} and accepted. The link dropped at ${dropped} before her answer arrived. Nothing was queued or resent: "${preview}"`}
    </div>
  );
}

function useStickToBottom(ref: RefObject<HTMLElement | null>, key: string): void {
  const following = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    const onScroll = (): void => {
      following.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLOP_PX;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    const ro = new ResizeObserver(() => {
      if (following.current) el.scrollTop = el.scrollHeight;
    });
    ro.observe(el);
    return () => {
      el.removeEventListener('scroll', onScroll);
      ro.disconnect();
    };
  }, [ref]);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [ref, key]);
}

export function ChatDock({ dockRef }: { dockRef: RefObject<HTMLElement | null> }) {
  const lines = useStore(transcriptStore);
  const connection = useStore(connectionStore);
  const state = useStore(agentStateStore);
  const awaiting = useComposeField('awaitingAck');
  const inflight = useComposeField('inflight');
  const error = useComposeField('error');
  const unconfirmed = useComposeField('unconfirmed');
  const listRef = useRef<HTMLDivElement>(null);
  const shown = lines.slice(-DOCK_LINES);
  const online = connection.phase === 'connected';
  const word = online ? (awaiting !== null || inflight !== null ? state.toUpperCase() : 'READY') : LINK_WORD[connection.phase];
  useStickToBottom(listRef, `${lines.length}:${shown[shown.length - 1]?.messageId ?? ''}:${unconfirmed?.droppedAt ?? 0}:${connection.phase}`);
  return (
    <section ref={dockRef} className="pnl br chatdock" aria-label="Chat with Tessa">
      <div className="ph">
        <span className="lab">CHAT</span>
        <Val className="lab" text={word} />
      </div>
      <div className="cl" ref={listRef} aria-live="polite">
        {shown.length === 0 ? (
          connection.phase === 'connecting' ? <Loading rows={3} /> : <div className="dim">{EMPTY_CHAT}</div>
        ) : (
          shown.map((l, i) => <ChatMessage key={`${l.messageId}-${lines.length - shown.length + i}`} line={l} short />)
        )}
        {unconfirmed ? <UnconfirmedLine u={unconfirmed} /> : null}
      </div>
      {online && error !== null ? <ErrorLine text={error} /> : null}
      <Composer place="dock" />
    </section>
  );
}

export function QuickLine({ quickRef }: { quickRef: RefObject<HTMLDivElement | null> }) {
  const open = useComposeField('quickOpen');
  return (
    <div ref={quickRef} className={`quick br${open ? ' on' : ''}`} aria-hidden={!open}>
      {open ? <Composer place="quick" /> : null}
    </div>
  );
}
