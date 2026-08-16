/**
 * THE LATENCY TRACE — item 9. Where the last turn's seconds went.
 *
 * ─── the question it answers ───
 * The owner can hear that a reply took four seconds. Nothing on this machine
 * tells him whether that was speech recognition, the model, a tool, speech
 * synthesis, or the audio device — and those have completely different fixes.
 * `core/server.py`'s `on_stage` already computes exactly this breakdown and
 * sends it to `log()`, so the numbers exist and are simply not addressed to a
 * surface. Session 1 approved `evt.turn.timing` to address them.
 *
 * ─── it is DARK, and dark is not the same as stubbed ───
 * Nothing emits the event yet, so `turnTimingStore` is null and this renders
 * NOTHING — no frame, no zero bars, no "waiting for data". The wire is live
 * end to end (ws-client validates, main forwards, App subscribes), so the trace
 * appears the first time a turn completes after the daemon ships its half, with
 * no further change on this side.
 *
 * ─── the stage vocabulary is CLOSED, and this file is why ───
 * `STAGE_ORDER` is the whole reason Session 1's approval carried that
 * condition. The bars are laid out left to right in pipeline order, because a
 * pipeline drawn out of order is worse than no picture at all — the reader
 * infers causation from position. An unrecognised stage name has no position,
 * so main drops it before it reaches here and logs that it did. If the daemon
 * grows a sixth stage, both sides change together, exactly like CONTRACT §7.4's
 * closed enums.
 */

import { useStore } from '../state/store.ts';
import { turnTimingStore } from '../state/store.ts';

/** Pipeline order. The layout IS the claim; see the note above. */
const STAGE_ORDER = ['stt', 'route', 'tool', 'tts', 'playback'] as const;

/** What each stage is, in words, for the row's title attribute. */
const STAGE_MEANING: Record<string, string> = {
  stt: 'speech to text',
  route: 'the model choosing what to do',
  tool: 'running the tool it chose',
  tts: 'text to speech',
  playback: 'audio out',
};

export function Latency() {
  const timing = useStore(turnTimingStore);
  if (!timing || timing.stages.length === 0) return null;

  const byName = new Map(timing.stages.map((s) => [s.name, s.ms]));
  const shown = STAGE_ORDER.filter((n) => byName.has(n));
  const total = shown.reduce((sum, n) => sum + (byName.get(n) ?? 0), 0);
  if (total <= 0) return null;

  return (
    <section className="lat" aria-label="last turn latency">
      <h3 className="col__title">
        last turn
        <span className="lat__total num">{(total / 1000).toFixed(2)}s</span>
      </h3>

      {/* One bar, segmented. Five separate bars would invite comparing each
          against its own scale; segments of one bar compare against the whole,
          which is the actual question — what fraction of the wait was this. */}
      <div className="lat__bar">
        {shown.map((name) => (
          <span
            key={name}
            className="lat__seg"
            data-stage={name}
            style={{ flexGrow: byName.get(name) ?? 0 }}
            title={`${name} — ${STAGE_MEANING[name] ?? ''}`}
          />
        ))}
      </div>

      <ul className="lat__rows">
        {shown.map((name) => {
          const ms = byName.get(name) ?? 0;
          return (
            <li key={name} className="lat__row">
              <span className="lat__swatch" data-stage={name} aria-hidden="true" />
              <span className="lat__name">{name}</span>
              <span className="lat__ms num">{Math.round(ms)}ms</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
