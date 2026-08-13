/**
 * §R.2 under the sphere: "Live transcript, one line, fading. The last thing
 * said."
 *
 * One line, never a scrollback — TRACE is where the history lives. This is the
 * thing you glance at without opening anything.
 *
 * Empty today: it consumes the same reassembled transcript lines TRACE does,
 * and no transcript events fire until the voice pipeline lands. The slot and
 * the fade exist now so that when the first line arrives it simply appears,
 * with no further work here.
 *
 * The fade is a single transition driven by which message is current, not a
 * loop — §R.7's motion budget again. At rest this element is either absent or
 * static.
 */

import { transcriptStore, useStore } from '../state/store.ts';

export function LastLine() {
  const lines = useStore(transcriptStore);
  const last = lines.length > 0 ? lines[lines.length - 1] : null;

  if (!last) return null;

  return (
    <p className="lastline" data-provenance={last.provenance} key={last.messageId}>
      <span className="lastline__role">{last.role}</span>
      <span className="lastline__text">{last.text}</span>
    </p>
  );
}
