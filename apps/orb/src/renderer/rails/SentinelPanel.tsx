/**
 * SENTINEL — "Is it safe?" (§R.3)
 *
 * Two of its sources are real today and both are wired: the audit log
 * (cmd.audit.query for history, evt.audit.appended for the live stream) and the
 * PTY grant roster (evt.pty.sessions, which CONTRACT §4.2 explicitly permits
 * the Orb to subscribe to). Defender status, definition age, the quarantine
 * vault and pending approvals are P6 and have no source — they say NO DATA.
 *
 * On the status colour: §R.7 makes SENTINEL the only rail that carries one, and
 * this panel deliberately never asserts a healthy state. See sentinel-status.ts.
 */

import { NoData, Row, Section, TierBadge, formatTimestamp } from './primitives.tsx';
import { auditStore, ptySessionsStore, useStore } from '../state/store.ts';

export function SentinelPanel() {
  const audit = useStore(auditStore);
  const sessions = useStore(ptySessionsStore);
  const now = Date.now();

  return (
    <>
      {/* P6. No Defender integration exists, so no claim is made either way. */}
      <Section title="Defender">
        <NoData />
      </Section>

      <Section title="Quarantine">
        <NoData />
      </Section>

      <Section title="Pending approvals">
        <NoData />
      </Section>

      <Section title="Active PTY grants">
        {sessions.length === 0 ? (
          <NoData />
        ) : (
          sessions.map((s) => (
            <Row
              key={s.sessionId}
              label={s.profileId}
              value={
                <span className="state" data-ok={!s.busy}>
                  {s.busy ? 'busy' : 'idle'}
                </span>
              }
              hint={s.cwd}
            />
          ))
        )}
      </Section>

      <Section title="Audit stream">
        {audit.length === 0 ? (
          <NoData />
        ) : (
          audit.map((e) => (
            <div key={e.id} className="audit">
              <span className="audit__top">
                <span className="audit__tool">{e.tool}</span>
                <TierBadge tier={e.tier} />
                <span className="audit__ts num">{formatTimestamp(e.ts, now)}</span>
              </span>
              <span className="audit__summary">{e.summary}</span>
              <span className="audit__actor">
                {e.actor}
                {e.provenance ? ` · ${e.provenance}` : ''}
              </span>
            </div>
          ))
        )}
      </Section>
    </>
  );
}
