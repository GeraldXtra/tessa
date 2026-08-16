/**
 * CONNECTION TOPOLOGY — item 10. What this surface can honestly say it knows.
 *
 * ─── why this was cut once, and what changed ───
 * It was cut because a topology implies a map of the system, and this process
 * can see exactly one socket: its own. Drawing the Console as a peer, or the
 * model provider as a node, would have been a diagram of an architecture rather
 * than a report of a state — the surface asserting facts it has no source for,
 * which is the same failure as an invented number in a nicer shape.
 *
 * He wants it, so it is built — from the four things this process genuinely
 * knows, and nothing else. Every row below traces to a value already on this
 * side of the bridge:
 *
 *   this surface  -> the daemon    ConnectionStatus.phase, and the daemon
 *                                  version out of `res.hello`
 *   the daemon    -> its heartbeat healthStore.receivedAt, so the link is shown
 *                                  as live or stale from the ARRIVAL of beats
 *                                  rather than from a flag inside one
 *   the daemon    -> a terminal    evt.pty.sessions, which CONTRACT §4.2 says
 *                                  the Orb may subscribe to. Count only.
 *   the daemon    -> the model     the engine fields the column already shows,
 *                                  when they are present
 *
 * ─── what is deliberately ABSENT, and stays absent ───
 * The Console. There is no `evt.surface.roster` in CONTRACT §4, so this process
 * cannot tell whether the other surface is attached — and a node drawn grey
 * because "we do not know" is indistinguishable from one drawn grey because
 * "it is down". The honest rendering of an unknown is no row at all.
 *
 * The network beyond the daemon is rendered ONLY WHEN TRUE. `apiReachable`
 * exists in `evt.daemon.health` (§4.1) and was hardcoded to False when this was
 * first reasoned about, which is why an earlier round refused to draw it at
 * all — a permanent false "API UNREACHABLE" is worse than silence. Session 1
 * has since made it real: it measured true against the live daemon in the
 * capture for this round. The gate stays as it is regardless, because
 * "unreachable" and "the daemon has not got round to filling this in" are still
 * the same value, and only the positive case is a claim worth making.
 */

import { useStore } from '../state/store.ts';
import { connectionStore, healthStore, ptySessionsStore } from '../state/store.ts';
import { tickStore, formatAgo } from '../state/tick.ts';

/** Three missed beats. The daemon sends one every 5 s (CONTRACT §4.1). */
const BEAT_STALE_MS = 15_000;

type Hop = {
  to: string;
  /** 'live' | 'weak' | 'dead' — drives the connector, never a colour literal. */
  grade: 'live' | 'weak' | 'dead';
  note: string;
};

export function Topology() {
  const connection = useStore(connectionStore);
  const health = useStore(healthStore);
  const ptys = useStore(ptySessionsStore);
  const now = useStore(tickStore);

  const connected = connection.phase === 'connected';
  const beatAge = health ? now - health.receivedAt : null;
  const beatStale = beatAge !== null && beatAge > BEAT_STALE_MS;

  const hops: Hop[] = [
    {
      to: 'daemon',
      grade: connected ? 'live' : connection.phase === 'reconnecting' ? 'weak' : 'dead',
      note: connection.phase,
    },
  ];

  // Only once a beat has actually arrived. "Connected but no beat yet" is a
  // real state for up to five seconds and must not be drawn as a stalled one.
  if (connected && beatAge !== null) {
    hops.push({
      to: 'heartbeat',
      grade: beatStale ? 'weak' : 'live',
      note: formatAgo(beatAge),
    });
  }

  // CONTRACT §4.2 — the roster the daemon assembles from Console reports. The
  // COUNT is the fact; the session list belongs to SENTINEL, which shows it.
  if (ptys.length > 0) {
    hops.push({
      to: 'terminals',
      grade: 'live',
      note: `${ptys.length} session${ptys.length === 1 ? '' : 's'}`,
    });
  }

  // Only when the daemon says so, and only in the affirmative — see the header
  // for why false and absent are deliberately drawn the same way.
  if (connected && health?.apiReachable) {
    hops.push({ to: 'model api', grade: 'live', note: 'reachable' });
  }

  return (
    <section className="topo" aria-label="connection topology">
      <h3 className="col__title">topology</h3>
      <ul className="topo__list">
        <li className="topo__node topo__node--self">
          {/* An empty connector cell, not a missing one. The row is a grid and
              the head node has nothing above it to connect to — dropping the
              element instead of emptying it shifts every remaining cell one
              column left, which put the name on top of its own note. */}
          <span className="topo__link" aria-hidden="true" />
          <span className="topo__name">orb</span>
          <span className="topo__note">this window</span>
        </li>
        {hops.map((h) => (
          <li key={h.to} className="topo__node" data-grade={h.grade}>
            <span className="topo__link" aria-hidden="true" />
            <span className="topo__name">{h.to}</span>
            <span className="topo__note num">{h.note}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
