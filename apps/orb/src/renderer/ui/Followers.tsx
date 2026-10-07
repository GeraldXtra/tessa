import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';

import { useComposeField } from '../state/compose-store.ts';
import { captionStore } from '../state/plasma-inputs.ts';
import { agentStateStore, connectionStore, transcriptStore, useStore } from '../state/store.ts';
import { tickStore } from '../state/tick.ts';
import { Val } from './bits.tsx';
import { mmss } from './format.ts';
import { frontCardStore, stateSinceStore } from './stores.ts';

export function Chip({ chipRef }: { chipRef: RefObject<HTMLDivElement | null> }) {
  const state = useStore(agentStateStore);
  const connection = useStore(connectionStore);
  const since = useStore(stateSinceStore);
  const front = useStore(frontCardStore);
  const now = useStore(tickStore);
  const offline = connection.phase !== 'connected';
  const byCard = front !== null && state !== 'blocked';
  const blocked = !offline && (state === 'blocked' || byCard);
  const from = byCard ? front.at : since.at;
  return (
    <div ref={chipRef} className={`chip br${blocked ? ' blocked' : ''}${offline ? ' offline' : ''}`}>
      <i className="dot" />
      {offline ? (
        <span>OFFLINE</span>
      ) : blocked ? (
        <span>
          BLOCKED<span className="gap" />WAITING ON YOU
        </span>
      ) : (
        <span>{state.toUpperCase()}</span>
      )}
      <Val className="t" text={mmss((now - from) / 1000)} />
    </div>
  );
}

function wordAt(startedAt: number, words: readonly { offsetMs: number }[], now: number): number {
  const elapsed = now - startedAt;
  let idx = -1;
  for (let i = 0; i < words.length; i++) {
    if ((words[i]?.offsetMs ?? Number.POSITIVE_INFINITY) <= elapsed) idx = i;
    else break;
  }
  return idx;
}

export function CaptionLine({ capRef }: { capRef: RefObject<HTMLDivElement | null> }) {
  const state = useStore(agentStateStore);
  const lines = useStore(transcriptStore);
  const caption = useStore(captionStore);
  const typing = useComposeField('quickOpen');
  const lineRef = useRef<HTMLDivElement>(null);
  const [over, setOver] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  let herIdx = -1;
  let youIdx = -1;
  for (let i = lines.length - 1; i >= 0 && (herIdx < 0 || youIdx < 0); i--) {
    const l = lines[i];
    if (!l) continue;
    if (herIdx < 0 && l.role === 'assistant') herIdx = i;
    if (youIdx < 0 && l.role === 'user' && (l.via === 'voice' || l.via === 'typed')) youIdx = i;
  }
  const hearing = state === 'listening' || state === 'thinking';
  const you = hearing && youIdx >= 0 && youIdx > herIdx ? lines[youIdx] : null;
  const her = !hearing && herIdx >= 0 ? lines[herIdx] : null;
  const words = her && caption && caption.messageId === her.messageId && caption.words ? caption.words : null;
  const timed = words !== null && state === 'speaking';

  useEffect(() => {
    if (!timed) return;
    let raf = 0;
    const loop = (): void => {
      setNow(Date.now());
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [timed]);

  const text = you ? you.text : her ? her.text : '';

  useLayoutEffect(() => {
    const cap = capRef.current;
    const line = lineRef.current;
    if (!cap || !line) return;
    const measure = (): void => setOver(line.scrollWidth > cap.clientWidth + 0.5);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(cap);
    return () => ro.disconnect();
  }, [capRef, text, timed]);

  const current = timed && words ? wordAt(words.startedAt, words.words, now) : -1;

  useLayoutEffect(() => {
    const cap = capRef.current;
    const line = lineRef.current;
    if (!cap || !line || !timed) return;
    const cw = cap.clientWidth;
    const lw = line.scrollWidth;
    if (lw <= cw) {
      line.style.transform = '';
      return;
    }
    const el = line.querySelector<HTMLElement>(`[data-i="${Math.max(0, current)}"]`);
    const c = el ? el.offsetLeft + el.offsetWidth / 2 : 0;
    const x = Math.min(0, Math.max(cw - lw, cw * 0.62 - c));
    line.style.transform = `translateX(${x.toFixed(1)}px)`;
  }, [capRef, current, timed]);

  return (
    <div ref={capRef} className={`cap${over ? (timed ? ' over both' : ' over') : ''}${typing ? ' typing' : ''}`} aria-live="off">
      <div ref={lineRef} className={`line${over ? '' : ' fit'}`}>
        {you ? (
          <span className="you">
            <b>YOU</b>
            {you.text}
          </span>
        ) : timed && words ? (
          words.words.map((w, i) => (
            <span key={`${i}-${w.text}`} data-i={i} className={`w${i < current ? ' said' : i === current ? ' now' : ''}`}>
              {i < words.words.length - 1 ? `${w.text} ` : w.text}
            </span>
          ))
        ) : her ? (
          <span className="her">{her.text}</span>
        ) : null}
      </div>
    </div>
  );
}
