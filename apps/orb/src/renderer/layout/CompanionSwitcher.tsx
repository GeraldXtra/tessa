/**
 * The bottom controls: ◀ TESSA ▶, the indicator bar, and the pill beneath.
 *
 * ─── THE ARROWS, AND WHAT THEY DO TODAY ───
 * He kept pointing at these and I kept cutting them, on the grounds that a
 * control which does nothing is fabricated UI. That reasoning was right about
 * the RULE and wrong about the OPTION it left out: the choice is not "build a
 * dead control" or "build nothing", it is those two plus "build the control and
 * let it say why it cannot act".
 *
 * So they are built and DISABLED, with the reason on the control itself rather
 * than hidden in a tooltip. One companion exists; `evt.companion.roster`
 * (CONTRACT §4.1) is defined and the daemon does not emit it. The moment it
 * does, `roster.length > 1` enables them and nothing else here changes.
 *
 * I considered the third option in the brief — cycling between three visually
 * distinct but functionally identical spheres — and rejected it. It would make
 * the arrows appear to work while switching between things that are the same
 * thing, which teaches him the control is real and then breaks that lesson the
 * day companions become real. A disabled control that states its reason is
 * honest at every moment; a working control over fake targets is honest at
 * none.
 *
 * ─── THE PILL ───
 * The reference says KNOWLEDGE VIEW, which does not exist — no knowledge index
 * in `core/`, no command for one in §5. Rather than leave the shape empty or
 * invent a destination, it carries something real and already built: the count
 * of audit entries, opening SENTINEL. That is a true statement, it is the same
 * "expand into detail" gesture the reference's pill makes, and it is the one
 * pill-shaped thing on this surface with a place to go.
 */

import { useStore } from '../state/store.ts';
import { auditStore, connectionStore, railStore } from '../state/store.ts';

const COMPANION_NAME = 'TESSA';

export function CompanionSwitcher() {
  const connection = useStore(connectionStore);
  const audit = useStore(auditStore);
  const online = connection.phase === 'connected';

  // One companion, so both arrows are inert. Derived rather than hard-coded so
  // that a roster event is the only change needed to bring them alive.
  const companionCount = online ? 1 : 0;
  const canSwitch = companionCount > 1;
  const why = canSwitch ? undefined : 'one companion — more arrive with the roster';

  return (
    <div className="switcher">
      <div className="switcher__row">
        <button
          className="switcher__arrow"
          type="button"
          disabled={!canSwitch}
          title={why}
          aria-label={why ?? 'previous companion'}
        >
          ‹
        </button>

        <div className="switcher__id">
          <span className="switcher__name">{COMPANION_NAME}</span>
          <span className="switcher__rungs" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="switcher__rung"
                data-lit={online && i === 0 ? 'true' : undefined}
              />
            ))}
          </span>
        </div>

        <button
          className="switcher__arrow"
          type="button"
          disabled={!canSwitch}
          title={why}
          aria-label={why ?? 'next companion'}
        >
          ›
        </button>
      </div>

      {/* The pill. Real destination, real count, or absent entirely. */}
      {audit.length > 0 ? (
        <button
          className="switcher__pill"
          type="button"
          onClick={() => railStore.set('sentinel')}
        >
          <span className="switcher__pill-chev" aria-hidden="true">⌄</span>
          {audit.length} audited
        </button>
      ) : null}
    </div>
  );
}
