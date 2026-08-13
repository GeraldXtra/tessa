/**
 * The top bar. §R.2: "Companion name · state · connection + uptime · data used
 * today · API spend today."
 *
 * Also the window's drag region — the window is frameless (CONTRACT §9.1:
 * "centre stage floats over pure void", which a native title bar would
 * interrupt), so this strip carries both the drag handle and the controls.
 *
 * ─── no second formatting ───
 * Uptime and spend also appear in PULSE. Both are formatted here with the SAME
 * helpers PULSE uses (`formatUptime`, `formatMetric`) rather than re-derived,
 * because two formattings of one number is how a bar and a drawer end up
 * disagreeing about the same value. `formatMetric` in particular never prints a
 * false zero.
 *
 * Five regions, of which three have a source today:
 *   name        "ZOEY" — a real constant, not a placeholder
 *   state       evt.agent.state
 *   connection  the handshake, + uptime from the heartbeat
 *   data used   P5, no source -> NO DATA
 *   API spend   budgetSpent against the nightly cap
 */

import { useEffect, useState } from 'react';

import type { ConnectionPhase } from '../../shared/ipc-contract.ts';
import { formatMetric, formatUptime } from '../rails/format.ts';
import {
  agentStateStore,
  connectionStore,
  healthStore,
  micStore,
  useStore,
} from '../state/store.ts';

const CONNECTION_LABEL: Record<ConnectionPhase, string> = {
  offline: 'DAEMON OFFLINE',
  connecting: 'CONNECTING',
  connected: 'CONNECTED',
  authRejected: 'AUTH REJECTED',
  protocolMismatch: 'PROTOCOL MISMATCH',
  reconnecting: 'RECONNECTING',
};

/** The two phases no amount of waiting will clear. */
const TERMINAL: ReadonlySet<ConnectionPhase> = new Set(['authRejected', 'protocolMismatch']);

/**
 * The daemon promises a heartbeat every 5 s (core/server.py::heartbeat). Three
 * missed beats is a link that is up at the TCP level but not delivering —
 * "CONNECTED" beside a frozen uptime is the most misleading thing this bar
 * could show.
 */
const HEARTBEAT_STALE_MS = 15_000;

/**
 * How the microphone claim is shown, and why it is here and not on the sphere.
 *
 * §R.2 gives the top bar; §R.7 gives the rail and the sphere. The sphere was
 * the obvious candidate — it is the only thing on screen big enough to read
 * from across a room — and it is the wrong one. The sphere's colour and motion
 * are ALREADY a language: spec §5.1 assigns all six agent states to it, and
 * `listening` is literally "tighten and brighten". Painting a microphone claim
 * onto the same object makes "the daemon has the mic open" and "the agent is in
 * the listening state" the same picture, when the entire point of §5.1's
 * blocked-versus-working distinction is that two different facts must not look
 * alike. They are also genuinely independent: the chord can claim the mic while
 * the agent is still `idle`.
 *
 * So it goes in the bar, in two parts, and the second is what makes it carry:
 *
 *   1. A MIC LIVE pill in --status-error, at the same size as the CONNECTED
 *      pill that is already legible on this screen.
 *   2. A 3px --status-error rule along the full 1366px top edge of the window.
 *
 * The rule is the across-the-room part. It is not a new visual language — §R.7
 * already uses a 2px --accent bar on the rail's inner edge as the "this is
 * active" mark, and this is the same idiom at window scale. A saturated red
 * line spanning the entire top of the display is unmissable in peripheral
 * vision, and it costs one element that does not exist at all when the mic is
 * dark.
 */
export function StatusBar() {
  const agentState = useStore(agentStateStore);
  const connection = useStore(connectionStore);
  const health = useStore(healthStore);
  const mic = useStore(micStore);

  // Ticks once a second so the staleness check re-evaluates. The heartbeat only
  // arrives every 5 s; without this the bar would keep calling a dead beat fresh.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const beatStale = health !== null && now - health.receivedAt > HEARTBEAT_STALE_MS;
  const connected = connection.phase === 'connected';

  return (
    <header className="status-bar" data-mic={mic.claimed}>
      {/* The across-the-room signal. No element at all when the mic is dark —
          the same discipline as the notification stack. */}
      {mic.claimed ? <span className="status-bar__mic-rule" aria-hidden="true" /> : null}

      <div className="status-bar__drag">
        <span className="status-bar__mark">ZOEY</span>

        <span className="status-bar__sep" aria-hidden="true" />

        <span className="status-bar__state" data-state={agentState}>
          <span className="status-bar__dot" aria-hidden="true" />
          {agentState}
        </span>

        <span className="status-bar__sep" aria-hidden="true" />

        {/* MIC LIVE, or the trigger's readiness when it is not.
            §R.7's "a threat is never hidden behind a closed panel" applied to
            the microphone: this is never in a drawer. */}
        <span
          className="status-bar__mic"
          data-claimed={mic.claimed}
          data-mode={mic.mode}
          title={
            mic.claimed
              ? 'The daemon has confirmed a voice segment is open'
              : mic.mode === 'hold'
                ? `Hold ${mic.chord} while the Orb has focus. The global chord is released in hold mode — Electron gives no key-release event to a global shortcut, so hold cannot work when the Orb is behind another window.`
                : mic.chordRegistered
                  ? `Press ${mic.chord} anywhere to talk`
                  : `${mic.chord} is held by another application — push-to-talk works only while the Orb has focus`
          }
        >
          <span className="status-bar__mic-dot" aria-hidden="true" />
          {mic.claimed ? 'MIC LIVE' : 'mic'}
          <span className="status-bar__mic-mode">
            {mic.mode}
            {/* Under the constraint found while building this: hold releases
                the global chord, so the trigger is focus-only. Gerald must be
                able to see that without reading source. */}
            {mic.mode === 'hold'
              ? ' · focus only'
              : mic.chordRegistered
                ? ` · ${mic.chord}`
                : ' · chord unavailable'}
          </span>
        </span>

        <span className="status-bar__sep" aria-hidden="true" />

        <span className="status-bar__conn" data-phase={connection.phase}>
          {CONNECTION_LABEL[connection.phase]}
          {connection.daemonVersion ? ` ${connection.daemonVersion}` : ''}
        </span>

        {/* Uptime rides the connection label because it only means anything
            while connected. Same formatter as PULSE. */}
        {connected && health ? (
          <span className="status-bar__beat num" data-stale={beatStale}>
            {beatStale ? 'no heartbeat' : `up ${formatUptime(health.uptimeS)}`}
          </span>
        ) : null}

        <span className="status-bar__sep" aria-hidden="true" />

        {/* §R.2 "data used today" — metered-data accounting is P5 and nothing
            produces it. NO DATA rather than a zero that would read as
            "you have used none today". */}
        <span className="status-bar__metric">
          <span className="status-bar__metric-label">data</span>
          <span className="status-bar__nodata">NO DATA</span>
        </span>

        {/* §R.2 "API spend today". Same source and same formatter as PULSE. */}
        <span className="status-bar__metric">
          <span className="status-bar__metric-label">spend</span>
          {connected && health ? (
            <span className="num">
              ₦{formatMetric(health.budgetSpent, 0)} / ₦{formatMetric(health.budgetCap, 0)}
            </span>
          ) : (
            <span className="status-bar__nodata">NO DATA</span>
          )}
        </span>

        {connection.detail ? <span className="status-bar__detail">{connection.detail}</span> : null}

        {TERMINAL.has(connection.phase) ? (
          <button
            type="button"
            className="status-bar__retry"
            onClick={() => window.zoey.retryConnection()}
          >
            retry
          </button>
        ) : null}
      </div>

      <div className="status-bar__controls">
        <button
          type="button"
          className="status-bar__control"
          onClick={() => window.zoey.minimizeWindow()}
          aria-label="Minimise"
          title="Minimise"
        >
          –
        </button>
        <button
          type="button"
          className="status-bar__control status-bar__control--close"
          onClick={() => window.zoey.closeWindow()}
          aria-label="Close"
          title="Close"
        >
          ×
        </button>
      </div>
    </header>
  );
}
