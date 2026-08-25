/**
 * Her name, under the sphere. The reference's identity block, minus its lies.
 *
 * ─── what the reference has here, and what of it is real ───
 * The reference shows four things under the sphere: the word TESSA, a small
 * indicator strip beneath it, a left and right arrow flanking it, and a pill
 * button reading KNOWLEDGE VIEW. Two are built and two are not.
 *
 * BUILT — the wordmark and the indicator. The wordmark is her identity and
 * costs nothing to be true. The indicator is three marks, of which exactly one
 * is lit, and it is wired to the LIVE CONNECTION rather than being decoration:
 * offline, reaching, present. That makes it the one piece of the reference's
 * identity block that carries information, and it carries the piece a
 * glanceable surface most needs — whether the thing under the name is actually
 * there.
 *
 * NOT BUILT — the arrows. They are the companion switcher (spec P7). There is
 * ONE companion. `evt.companion.roster` exists in CONTRACT §4.1 and the daemon
 * does not emit it, so a switcher would be two controls that either do nothing
 * or switch between a list of one. A control that does nothing is fabricated
 * UI in the same way an invented number is fabricated data, and it is worse,
 * because a number is only read while a control invites a click.
 *
 * NOT BUILT — the pill. The reference labels it KNOWLEDGE VIEW, which does not
 * exist: there is no knowledge index in `core/`, no command for one in CONTRACT
 * §5, and no producer to fill it. The instruction was to give the shape
 * something real or leave it unbuilt, argued — and the argument is that
 * everything real that could go in it is already reachable and better placed.
 * ARSENAL answers "what can she do", RECALL answers "what does she remember",
 * SENTINEL answers "what has she done"; all three are one click away on the
 * rail, all three are typographic rather than a floating pill, and §R.7's rail
 * is the aesthetic. A pill under the wordmark duplicating a rail item would add
 * a second navigation idiom to save nobody a keystroke. So the shape stays
 * unbuilt, and the space it occupied is what gives the wordmark its air.
 */

import { useStore } from '../state/store.ts';
import { connectionStore } from '../state/store.ts';

/**
 * Three rungs, one lit. Derived from the connection phase rather than stored,
 * so it cannot disagree with the status bar about the same fact.
 */
function rungFor(phase: string): 0 | 1 | 2 {
  if (phase === 'connected') return 2;
  if (phase === 'connecting' || phase === 'reconnecting') return 1;
  return 0;
}

const RUNG_LABEL = ['offline', 'reaching', 'present'] as const;

export function Wordmark() {
  const connection = useStore(connectionStore);
  const lit = rungFor(connection.phase);

  return (
    <div className="wordmark">
      <span className="wordmark__name">TESSA</span>
      <span className="wordmark__rungs" role="img" aria-label={RUNG_LABEL[lit]}>
        {[0, 1, 2].map((i) => (
          <span key={i} className="wordmark__rung" data-lit={i <= lit ? 'true' : undefined} />
        ))}
      </span>
    </div>
  );
}
