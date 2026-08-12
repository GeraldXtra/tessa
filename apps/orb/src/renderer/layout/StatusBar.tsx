/**
 * The top strip: identity, agent state, daemon connection, window controls.
 *
 * Also the window's drag region — the window is frameless (CONTRACT §9.1:
 * "centre stage floats over pure void", which a native title bar would
 * interrupt), so this bar has to carry both the drag handle and the controls.
 *
 * The connection line is deliberately specific. "DISCONNECTED" would collapse
 * five genuinely different situations into one word; each of these needs a
 * different response from the owner, and two of them are terminal.
 */

import type { ConnectionPhase } from '../../shared/ipc-contract.ts';
import { agentStateStore, connectionStore, useStore } from '../state/store.ts';

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

export function StatusBar() {
  const agentState = useStore(agentStateStore);
  const connection = useStore(connectionStore);

  return (
    <header className="status-bar">
      <div className="status-bar__drag">
        <span className="status-bar__mark">ZOEY</span>

        <span className="status-bar__sep" aria-hidden="true" />

        <span className="status-bar__state" data-state={agentState}>
          <span className="status-bar__dot" aria-hidden="true" />
          {agentState}
        </span>

        <span className="status-bar__sep" aria-hidden="true" />

        <span className="status-bar__conn" data-phase={connection.phase}>
          {CONNECTION_LABEL[connection.phase]}
          {connection.daemonVersion ? ` ${connection.daemonVersion}` : ''}
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
