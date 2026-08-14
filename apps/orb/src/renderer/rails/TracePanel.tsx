/**
 * TRACE — "What did we say?" (§R.3)
 *
 * ─── the provenance gutter (§R.6, "colour-coded, always on") ───
 * CONTRACT §6.2 makes `human` the only trusted source, and the gutter exists so
 * that model-proposed text is never mistaken for something the owner wrote.
 * Only three of the six Provenance values have tokens today
 * (--prov-human, --prov-program, --prov-agent); `schedule`, `external` and
 * `system` fall back to the program tint via CSS rather than being invented
 * here. A tokens.json diff is proposed to Gerald in the report — `external` in
 * particular must not stay quiet, because it is the prompt-injection category.
 *
 * ─── built for one-liners, now holding paragraphs ───
 * This was a 200-entry list of short exchanges. A single answer from a local
 * model can be longer than everything it has ever held, which forces three
 * decisions:
 *
 * WRAPPING. At rest every entry is ONE line, clipped with an ellipsis, and
 * expansion is opt-in per entry. §R.7's 28px row encodes "one line per entry",
 * and that is what is preserved here: the resting list is still one row per
 * entry, so ten answers do not become a ten-screen wall that has to be scrolled
 * past to reach the eleventh. Expanded, the text wraps freely with
 * `overflow-wrap: anywhere` so a long path or URL cannot push the drawer wide.
 * Expansion is a transient act on one entry, not a new resting state — the list
 * collapses back to one-line rows the moment it is dismissed. I did not
 * re-derive the 28px figure; I kept the property it encodes and changed only
 * the wrap behaviour.
 *
 * SCROLL ON ARRIVAL. Follows the newest entry ONLY if the reader is already at
 * the bottom. Scrolled up reading an earlier answer, a new arrival must not
 * yank the view away — with one-line entries that was a minor annoyance, with
 * paragraph answers it would lose the reader's place entirely. The scroll
 * container is `.drawer__body`, not this list.
 *
 * IN-FLIGHT ANSWERS DO NOT APPEAR. Nothing renders until a line is complete:
 * the reassembler emits only on `done`, and `evt.transcript.message` is
 * whole-turn by construction. Showing a partial would mean TRACE displaying
 * text that may still change, with a provenance gutter asserting a source over
 * it — and the gutter's whole job is to be trustworthy. The sphere's `thinking`
 * state is the in-flight indicator; that is what it is for.
 */

import { useEffect, useRef, useState } from 'react';

import { excerpt } from './format.ts';
import { NoData, Section, formatTimestamp } from './primitives.tsx';
import { transcriptStore, useStore } from '../state/store.ts';

/** Within this many px of the bottom counts as "following the newest". */
const STICK_SLOP_PX = 24;

export function TracePanel() {
  const lines = useStore(transcriptStore);
  const now = Date.now();

  const [expanded, setExpanded] = useState<string | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  /** Whether the reader was pinned to the bottom before this arrival. */
  const following = useRef(true);

  // The drawer body scrolls, not the list. Resolved from the DOM rather than
  // assumed, so moving the list does not silently break the follow behaviour.
  const scroller = (): HTMLElement | null =>
    listRef.current?.closest<HTMLElement>('.drawer__body') ?? null;

  useEffect(() => {
    const el = scroller();
    if (!el) return;
    const onScroll = (): void => {
      following.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLOP_PX;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    const el = scroller();
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <>
      <Section title="Transcript">
        {lines.length === 0 ? (
          <NoData />
        ) : (
          <ol className="trace" ref={listRef}>
            {lines.map((line, i) => {
              const id = `${line.messageId}-${i}`;
              const open = expanded === id;
              const { head, remainingWords } = excerpt(line.text);
              const long = remainingWords > 0;

              return (
                <li key={id} className="trace__line" data-provenance={line.provenance}>
                  <span className="trace__meta">
                    <span className="trace__role">{line.role}</span>
                    {long ? (
                      <button
                        type="button"
                        className="trace__toggle"
                        aria-expanded={open}
                        onClick={() => setExpanded(open ? null : id)}
                      >
                        {open ? 'less' : `+${remainingWords} words`}
                      </button>
                    ) : null}
                    <span className="trace__ts num">{formatTimestamp(line.ts, now)}</span>
                  </span>
                  <span className="trace__text" data-expanded={open}>
                    {open || !long ? line.text : head}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </Section>

      <Section title="Search">
        <NoData />
      </Section>
    </>
  );
}
