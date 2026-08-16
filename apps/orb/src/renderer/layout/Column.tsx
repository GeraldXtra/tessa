/**
 * The right column. 300px, borderless, right-aligned mono on void.
 *
 * ─── the rule that governs every line in this file ───
 * EVERY REGION DEGRADES TO NOTHING, NOT TO A PLACEHOLDER. If a value is absent
 * its whole group returns `null` and no DOM node exists. Eighteen empty boxes
 * would look worse than the emptiness this composition is meant to fix, and a
 * dash where a number should be is a fabrication with a modest disguise.
 *
 * Two deliberate exceptions, both of which are true statements rather than
 * placeholders:
 *   • the session counters show zeros, LABELLED "this session" — zero turns so
 *     far is a fact, and the label is what makes it one rather than a guess
 *     about a daemon that has been up for hours;
 *   • a sparkline below ten samples is replaced by its own bare number, which
 *     is smaller and still true, instead of a two-point line pretending to be
 *     a history.
 *
 * ─── contrast, and why nothing here carries --accent ───
 * Values are `--text` (15.86:1 on black) and labels are `--text-muted`
 * (5.60:1). Both are theme-independent. Violet's `--accent` is 4.46:1 and would
 * fail AA at 11px, which is exactly the defect that was found on the approve
 * button — so anything here that must carry the accent uses `--accent-bright`,
 * the ladder's top step, never `--accent`.
 *
 * ─── it does not compete with the sphere ───
 * Nothing above 11px, nothing above ~8% fill, nothing updating faster than 1 Hz.
 * The sphere leads by peak luminance (~19:1 at its near side, unchanged by the
 * depth term), by area, and by being the only thing that moves.
 */

import {
  auditStore,
  connectionStore,
  healthHistoryStore,
  healthStore,
  micStore,
  ptySessionsStore,
  transcriptStore,
  useStore,
} from '../state/store.ts';
import { approvalsStore } from '../state/approval-store.ts';
import { formatUptime, tickStore } from '../state/tick.ts';
import { Latency } from './Latency.tsx';
import { MIN_SAMPLES, Sparkline } from './Sparkline.tsx';
import { Topology } from './Topology.tsx';

/** One label/value line. Renders nothing when the value is absent. */
function Row({ label, value, hint }: { label: string; value: string | null; hint?: string }) {
  if (value === null || value === '') return null;
  return (
    <div className="col__row">
      <span className="col__label">{label}</span>
      <span className="col__value">
        {value}
        {hint ? <span className="col__hint"> {hint}</span> : null}
      </span>
    </div>
  );
}

/**
 * A titled group. Renders NOTHING — heading included — unless `when` is true.
 *
 * `when` is explicit rather than inferred, and that is the whole point. The
 * first version inspected `children` and asked whether any were non-null, which
 * is always true: a `<Row>` that returns null internally is still a perfectly
 * truthy React element at this level. So PENDING drew its heading over an empty
 * space — the exact empty box this column is written to avoid, produced by the
 * guard meant to prevent it. React cannot tell a caller what its children will
 * render; only the caller knows.
 */
function Group({ title, when, children }: { title: string; when: boolean; children: React.ReactNode }) {
  if (!when) return null;
  return (
    <section className="col__group">
      <h3 className="col__title">{title}</h3>
      {children}
    </section>
  );
}

export function Column() {
  const health = useStore(healthStore);
  const history = useStore(healthHistoryStore);
  const connection = useStore(connectionStore);
  const audit = useStore(auditStore);
  const transcript = useStore(transcriptStore);
  const approvals = useStore(approvalsStore);
  const pty = useStore(ptySessionsStore);
  const mic = useStore(micStore);
  const now = useStore(tickStore);

  /**
   * `uptimeS` is only true at the instant it arrived. Between beats the surface
   * adds its own elapsed time — which it genuinely knows — so the number ticks
   * every second instead of stepping every five. This is the whole "live"
   * argument in one expression, and it is not a fabrication: it is a measured
   * value plus a measured interval.
   */
  const uptime = health ? health.uptimeS + (now - health.receivedAt) / 1000 : null;

  const cpuSeries = history.map((h) => h.cpuPct);
  const memSeries = history.map((h) => h.memMB);

  const pendingApprovals = approvals.filter((a) => a.invalidated === null && a.sent === null).length;

  /* ── session counters. Zero is true; the label is what makes it true. ───── */
  const turns = transcript.filter((l) => l.provenance === 'human').length;
  const tools = audit.filter((e) => e.tool && e.tool !== 'protocol.unknownType').length;

  const spendKnown = health && health.budgetCap > 0;
  const spendPct = spendKnown ? Math.min(1, health.budgetSpent / health.budgetCap) : 0;

  /**
   * The projection, and it appears ONLY when there is a rate to project from.
   * With `budgetSpent` at zero there is no rate, and "never" would be a claim
   * about the future built from no evidence.
   */
  const burnPerHour =
    health && health.budgetSpent > 0 && uptime && uptime > 60
      ? health.budgetSpent / (uptime / 3600)
      : null;
  const hoursToCap =
    burnPerHour && spendKnown && burnPerHour > 0
      ? (health.budgetCap - health.budgetSpent) / burnPerHour
      : null;

  return (
    <aside className="col" aria-label="Telemetry">
      {/* h · her identity, with more presence than a 10px wordmark */}
      <section className="col__group col__group--identity">
        <span className="col__mark">ZOEY</span>
        <span className="col__sub">
          {connection.daemonVersion ? `core ${connection.daemonVersion}` : 'no daemon'}
        </span>
      </section>

      {/* j/k · the engine, and how hard it has been used */}
      <Group title="Engine" when={Boolean(health?.brainEngine) || typeof health?.brainCalls === 'number'}>
        <Row label="model" value={health?.brainEngine ?? null} />
        <Row
          label="calls"
          value={typeof health?.brainCalls === 'number' ? String(health.brainCalls) : null}
        />
      </Group>

      {/* r · which daemon this is, and how old. He has twice been confused
             about which build was live; this is the cheapest possible fix. */}
      <Group title="Daemon" when={Boolean(connection.sessionId) || uptime !== null}>
        <Row label="session" value={connection.sessionId ? connection.sessionId.slice(0, 8) : null} />
        <Row label="uptime" value={uptime !== null ? formatUptime(uptime) : null} />
      </Group>

      {/* a/c · the machine, as numbers that tick and traces that shape */}
      <Group title="Resource" when={health !== null}>
        {health ? (
          <div className="col__row col__row--trace">
            <span className="col__label">cpu</span>
            <span className="col__value">{health.cpuPct.toFixed(1)}%</span>
            {cpuSeries.length >= MIN_SAMPLES ? <Sparkline values={cpuSeries} /> : null}
          </div>
        ) : null}
        {health ? (
          <div className="col__row col__row--trace">
            <span className="col__label">mem</span>
            <span className="col__value">{Math.round(health.memMB)}MB</span>
            {memSeries.length >= MIN_SAMPLES ? <Sparkline values={memSeries} /> : null}
          </div>
        ) : null}
      </Group>

      {/* p · the budget as a shape, plus a projection only when one exists */}
      {spendKnown ? (
        <section className="col__group">
          <h3 className="col__title">Budget</h3>
          <div className="col__row">
            <span className="col__label">spend</span>
            <span className="col__value">
              {health.budgetSpent.toFixed(0)}
              <span className="col__hint"> of {health.budgetCap.toFixed(0)}</span>
            </span>
          </div>
          <div className="bar bar--inline" data-level={spendPct > 0.85 ? 'critical' : spendPct > 0.6 ? 'warning' : 'ok'}>
            <div className="bar__fill" style={{ width: `${(spendPct * 100).toFixed(1)}%` }} />
          </div>
          {hoursToCap !== null ? (
            <div className="col__row">
              <span className="col__label">cap in</span>
              <span className="col__value">{formatUptime(hoursToCap * 3600)}</span>
            </div>
          ) : null}
        </section>
      ) : null}

      {/* i · session counters. Labelled, so zero is a statement not a gap. */}
      <Group title="This session" when={true}>
        <Row label="turns" value={String(turns)} />
        <Row label="tools" value={String(tools)} />
        <Row label="audit" value={String(audit.length)} />
      </Group>

      {/* l · pending work. Absent when there is none, which is most of the time
             and is correct — an empty "PENDING 0" would be noise. */}
      <Group title="Pending" when={pendingApprovals > 0 || pty.length > 0 || mic.claimed}>
        <Row label="approvals" value={pendingApprovals > 0 ? String(pendingApprovals) : null} />
        <Row label="pty" value={pty.length > 0 ? String(pty.length) : null} />
        <Row label="mic" value={mic.claimed ? 'open' : null} />
      </Group>

      {/* Item 10 · what this process can honestly say it is connected to.
             Every row traces to a value already on this side of the bridge; the
             Console and the network beyond the daemon are absent because there
             is no source for either. See Topology.tsx. */}
      <Topology />

      {/* Item 9 · where the last turn's seconds went. Renders NOTHING until
             `evt.turn.timing` arrives, and the wire is live end to end so it
             lights the first time a turn completes. See Latency.tsx. */}
      <Latency />

      {/*
        n · HEARD / SAID lives UNDER THE SPHERE, not here.
        It was briefly in both places and the same sentence appeared twice on
        one screen. §R.2 puts the transcript under the sphere by name, and the
        words belong beside the thing that spoke them rather than in a stack
        next to the memory figure. See LastLine.tsx.
      */}
    </aside>
  );
}
