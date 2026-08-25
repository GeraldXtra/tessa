/**
 * The reference's chat panel: tabs, a message list, an input bar.
 *
 * ─── THE SHAPE IS BUILT. THE MESSAGES ARE NOT, AND IT SAYS SO ───
 * Typed chat with Tessa is a SEPARATE piece of work for Session 1 — nothing
 * wires the agent loop to a surface yet. CONTRACT §5.1 has `cmd.agent.message`
 * and the daemon does not answer it from here, so a message box that accepted
 * input would be a control that silently does nothing: the fabricated-UI rule
 * applied to behaviour rather than to a number.
 *
 * The input is therefore `disabled`, and it says why in its own placeholder
 * rather than in a tooltip nobody hovers. The reference's placeholder reads
 * "You're talking to Zoey through voice" — hers, and the old name; this carries
 * neither, because copying a string out of a screenshot is how a rename comes
 * undone six months later.
 *
 * ─── THE TABS ───
 * The reference tabs read ZOEY | KARMA | PULSE — one per companion. There is
 * one companion. Rendering three would be the companion switcher's problem in a
 * different costume, so the tab strip shows the one that exists and the rest
 * arrive with the roster (`evt.companion.roster`, §4.1).
 */

import { useStore } from '../state/store.ts';
import { transcriptStore } from '../state/store.ts';

export function ChatPanel() {
  // The voice transcript IS real and does arrive — `evt.transcript.*`. If any
  // has landed, the panel says how many rather than pretending it is empty.
  const lines = useStore(transcriptStore);

  return (
    <section className="chat" aria-label="conversation">
      <div className="chat__tabs" role="tablist">
        <button className="chat__tab" role="tab" aria-selected="true" type="button">
          Tessa
        </button>
        <span className="chat__tabnote">1 of 1</span>
      </div>

      <div className="chat__body">
        {lines.length === 0 ? (
          <p className="panel-sec__nodata">NO DATA</p>
        ) : (
          <p className="chat__count num">
            {lines.length} voice line{lines.length === 1 ? '' : 's'} — see TRACE
          </p>
        )}
        <p className="panel-sec__why">
          voice turns appear in TRACE with their provenance
        </p>
      </div>

      {/* Disabled, and the placeholder is the reason. An enabled box that
          swallowed his sentence would be worse than no box. */}
      <div className="chat__input">
        <input
          className="chat__field"
          type="text"
          disabled
          placeholder="typed chat is not connected yet"
          aria-label="typed chat, not connected"
        />
      </div>
    </section>
  );
}
