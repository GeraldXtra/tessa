/**
 * §R.2 under the sphere: "Live transcript, one line, fading. The last thing
 * said."
 *
 * ─── one line, when the answer is three paragraphs ───
 * The spec is explicit that this is ONE line, and it was written when every
 * answer was "It is 6:57 PM." A local model produces summaries and worked
 * explanations, so the question is what one line should contain when the answer
 * is three hundred words.
 *
 * Three candidates, and why this one:
 *
 *   CSS ellipsis on the raw text. What was here already, and the worst of the
 *   three — it cuts mid-word at whatever pixel the box ends, so the line reads
 *   as damage rather than as a summary, and it gives no clue how much was lost.
 *
 *   First sentence alone. Better: a sentence is a complete thought. But shown
 *   by itself it is a lie by omission — it looks like the whole answer when
 *   eight paragraphs follow. Same class as a false zero.
 *
 *   First sentence PLUS the count of what is left, PLUS a way to open it.
 *   Chosen. The sentence carries the meaning, the count is the honesty, and the
 *   affordance means "there is more" is not a dead end. Glanceable in one
 *   fixation, and it cannot mislead about completeness.
 *
 * This is still one line, and still not a log. It shows the last completed
 * line and nothing else; nothing accumulates here, and an answer still in
 * flight shows nothing at all, because the sphere's `thinking` state is what
 * says "she is working" and duplicating that in text would be a second, worse
 * spinner.
 *
 * ─── motion budget (§R.7) ───
 * The fade is a single transition keyed on the message, not a loop. At rest
 * this element is either absent or static.
 */

import { excerpt } from '../rails/format.ts';
import { railStore, transcriptStore, useStore } from '../state/store.ts';

export function LastLine() {
  const lines = useStore(transcriptStore);
  const last = lines.length > 0 ? lines[lines.length - 1] : null;

  if (!last) return null;

  const { head, remainingWords } = excerpt(last.text);
  const truncated = remainingWords > 0;

  return (
    <p className="lastline" data-provenance={last.provenance} key={last.messageId}>
      <span className="lastline__role">{last.role}</span>
      <span className="lastline__text">{head}</span>
      {truncated ? (
        <button
          type="button"
          className="lastline__more"
          onClick={() => railStore.set('trace')}
          title="Open TRACE to read the whole answer"
        >
          +{remainingWords} words
        </button>
      ) : null}
    </p>
  );
}
