/**
 * The reference's first left panel: "Active 0 · Done 367 · All companions idle".
 *
 * ─── ALL THREE OF THOSE ARE INVENTED, AND NONE IS BUILT ───
 * There is no scheduler and no job queue in `core/`. Nothing on this machine
 * counts an active job, a completed one, or a companion's idleness. "Done 367"
 * is the single most quotable number in his reference and it is exactly the
 * kind of thing the no-fabricated-data rule exists to stop — a figure that
 * looks like telemetry, reads as proof the system is working, and is a
 * decoration someone typed.
 *
 * So the panel is BUILT and the numbers are absent. It fills when Phase 5 lands
 * a job queue, and the shape is already correct for it.
 *
 * What IS real and therefore shown: the companion roster count, because
 * CONTRACT §4.1 defines `evt.companion.roster` and the surface knows how many
 * it has been told about — which is currently one, Tessa herself, and the
 * honest rendering of that is "1 companion", not "all companions idle".
 */

import { useStore } from '../state/store.ts';
import { connectionStore } from '../state/store.ts';

export function StatusCard() {
  const connection = useStore(connectionStore);
  const online = connection.phase === 'connected';

  return (
    <section className="card" aria-label="activity">
      <div className="card__row">
        <span className="card__k">active</span>
        <span className="card__nodata">NO DATA</span>
      </div>
      <div className="card__row">
        <span className="card__k">done</span>
        <span className="card__nodata">NO DATA</span>
      </div>
      <div className="card__rule" />
      <div className="card__row">
        <span className="card__k">companions</span>
        {/* Real: one, and only while the daemon is answering. Off-line the
            surface does not know what the roster is and must not claim one. */}
        <span className={online ? 'card__v num' : 'card__nodata'}>{online ? '1' : 'NO DATA'}</span>
      </div>
      <p className="card__why">
        job counts arrive with the Phase&nbsp;5 queue
      </p>
    </section>
  );
}
