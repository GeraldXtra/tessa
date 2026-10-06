import { useEffect, useRef, useState } from 'react';

import { captionStore } from '../state/plasma-inputs.ts';
import { agentStateStore, useStore } from '../state/store.ts';

const LEAD_MS = 1500;

function wordIndex(startedAt: number, offsets: readonly { offsetMs: number }[], now: number): number {
  const elapsed = now - startedAt;
  let idx = -1;
  for (let i = 0; i < offsets.length; i++) {
    if ((offsets[i]?.offsetMs ?? Number.POSITIVE_INFINITY) <= elapsed) idx = i;
    else break;
  }
  return idx;
}

export function Caption() {
  const state = useStore(agentStateStore);
  const caption = useStore(captionStore);
  const speakingSince = useRef(0);
  const [now, setNow] = useState(() => Date.now());

  if (state === 'speaking' && speakingSince.current === 0) speakingSince.current = Date.now();
  if (state !== 'speaking' && speakingSince.current !== 0) speakingSince.current = 0;

  const visible =
    state === 'speaking' && caption !== null && caption.text.length > 0 && caption.at >= speakingSince.current - LEAD_MS;
  const timed = visible && caption.words !== null && caption.words.words.length > 0;

  useEffect(() => {
    if (!timed) return;
    let raf = 0;
    const tick = (): void => {
      setNow(Date.now());
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [timed]);

  if (!visible) return null;

  if (!timed || !caption.words) {
    return (
      <p className="caption" aria-live="polite">
        {caption.text}
      </p>
    );
  }

  const words = caption.words.words;
  const current = wordIndex(caption.words.startedAt, words, now);
  return (
    <p className="caption" aria-live="polite">
      {words.map((w, i) => (
        <span
          key={`${i}-${w.text}`}
          className="caption__w"
          data-said={i < current || undefined}
          data-now={i === current || undefined}
        >
          {i < words.length - 1 ? `${w.text} ` : w.text}
        </span>
      ))}
    </p>
  );
}
