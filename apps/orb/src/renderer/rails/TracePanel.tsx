/**
 * TRACE — "What did we say?" (§R.3)
 *
 * Empty today: no transcript events fire, because nothing produces them until
 * the voice pipeline lands in core/. The subscription and the provenance gutter
 * are built now so the rail lights up on its own the moment they do.
 *
 * ─── the provenance gutter (§R.6, "colour-coded, always on") ───
 * CONTRACT §6.2 makes `human` the only trusted source, and the gutter exists so
 * that model-proposed text is never mistaken for something the owner wrote.
 * Only three of the six Provenance values have tokens today
 * (--prov-human, --prov-program, --prov-agent); `schedule`, `external` and
 * `system` fall back to the program tint via CSS rather than being invented
 * here. A tokens.json diff is proposed to Gerald in the report — `external` in
 * particular must not stay quiet, because it is the prompt-injection category.
 */

import { NoData, Section, formatTimestamp } from './primitives.tsx';
import { transcriptStore, useStore } from '../state/store.ts';

export function TracePanel() {
  const lines = useStore(transcriptStore);
  const now = Date.now();

  return (
    <>
      <Section title="Transcript">
        {lines.length === 0 ? (
          <NoData />
        ) : (
          <ol className="trace">
            {lines.map((line, i) => (
              <li
                key={`${line.messageId}-${i}`}
                className="trace__line"
                data-provenance={line.provenance}
              >
                <span className="trace__meta">
                  <span className="trace__role">{line.role}</span>
                  <span className="trace__ts num">{formatTimestamp(line.ts, now)}</span>
                </span>
                <span className="trace__text">{line.text}</span>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section title="Search">
        <NoData />
      </Section>
    </>
  );
}
