/**
 * PULSE — "Is my machine healthy?" (§R.3)
 *
 * §R.3 lists far more than exists: disk and network sparklines, top processes,
 * what Zoey is touching this second, disk projection, metered data, battery,
 * thermal. None of those have a source in the daemon today. The six fields of
 * `evt.daemon.health` do, and they are all real as of Session 1's landing —
 * so those six render and everything else says NO DATA.
 */

import { useEffect } from 'react';

import { formatMetric, formatUptime } from './format.ts';
import { Bar, NoData, Row, Section, Sparkline } from './primitives.tsx';
import { healthHistoryStore, healthStore, useStore } from '../state/store.ts';

/** Fixed ceiling for the CPU sparkline, so the trace does not rescale itself. */
const CPU_SPARK_CEILING = 100;

export function PulsePanel() {
  const health = useStore(healthStore);
  const history = useStore(healthHistoryStore);

  /**
   * Dev-only pairing instrument.
   *
   * Reports the six strings THIS render produced, tagged with the uptimeS of
   * the frame they came from, so a rendered value can be matched to its source
   * frame exactly. The previous report paired a frame logged at 11:02 with a
   * screenshot taken minutes later and the two disagreed — which looked like a
   * formatter bug and was really a non-simultaneous capture. This removes the
   * ambiguity instead of arguing about it.
   */
  useEffect(() => {
    if (!health) return;
    window.zoey.reportMetrics(
      `PULSE-RENDER src_uptimeS=${health.uptimeS} src_cpuPct=${health.cpuPct} ` +
        `src_memMB=${health.memMB} src_apiReachable=${health.apiReachable} ` +
        `src_budgetSpent=${health.budgetSpent} src_budgetCap=${health.budgetCap} ` +
        `|| shown_uptime="${formatUptime(health.uptimeS)}" ` +
        `shown_api="${health.apiReachable ? 'reachable' : 'unreachable'}" ` +
        `shown_cpu="${formatMetric(health.cpuPct, 1)}%" ` +
        `shown_mem="${formatMetric(health.memMB, 0)} MB" ` +
        `shown_spend="₦${formatMetric(health.budgetSpent, 0)}" ` +
        `shown_cap="₦${formatMetric(health.budgetCap, 0)}"`,
    );
  }, [health]);

  if (!health) {
    return (
      <>
        <Section title="Daemon">
          <NoData />
        </Section>
        <Section title="API spend">
          <NoData />
        </Section>
        <Section title="Machine">
          <NoData />
        </Section>
      </>
    );
  }

  const cpuSeries = history.map((h) => h.cpuPct);
  const memSeries = history.map((h) => h.memMB);
  const memCeiling = Math.max(64, ...memSeries);

  // The six rendered strings, built once so the dev pairing below reports
  // EXACTLY what the DOM shows rather than a second formatting of the same
  // numbers that could drift from it.
  const rendered = {
    uptime: formatUptime(health.uptimeS),
    api: health.apiReachable ? 'reachable' : 'unreachable',
    cpu: `${formatMetric(health.cpuPct, 1)}%`,
    mem: `${formatMetric(health.memMB, 0)} MB`,
    spend: `₦${formatMetric(health.budgetSpent, 0)}`,
    cap: `₦${formatMetric(health.budgetCap, 0)}`,
  };

  return (
    <>
      <Section title="Daemon">
        <Row label="Uptime" value={<span className="num">{rendered.uptime}</span>} />
        <Row
          label="API"
          value={
            <span className="state" data-ok={health.apiReachable}>
              {rendered.api}
            </span>
          }
        />
        {/*
          Labelled "daemon CPU", not "CPU".
          Session 1 defines cpuPct as the daemon PROCESS's usage, normalised
          across cores — not the machine's. Rendering it under a bare "CPU"
          heading beside a RAM figure would read as system load, which would be
          a false reading of a true number.
        */}
        <Row
          label="Daemon CPU"
          value={<span className="num">{rendered.cpu}</span>}
          hint="process, normalised across cores"
        />
        <Sparkline points={cpuSeries} max={CPU_SPARK_CEILING} />
        <Row label="Daemon RAM" value={<span className="num">{rendered.mem}</span>} />
        <Sparkline points={memSeries} max={memCeiling} />
      </Section>

      <Section title="API spend">
        {/*
          Naira, and the cap is Gerald's nightly hard stop. Rendered as a bar
          per §R.7 because a cap you have to read as two numbers and subtract is
          a cap you will not notice at 2am.
        */}
        <Row
          label="Tonight"
          value={
            <span className="num">
              {rendered.spend} / {rendered.cap}
            </span>
          }
        />
        <Bar value={health.budgetSpent} max={health.budgetCap} />
      </Section>

      {/*
        Everything §R.3 lists for PULSE that has no source: disk, network, top
        processes, what Zoey is touching, disk projection, metered data,
        battery, thermal. One honest empty section rather than eight.
      */}
      <Section title="Machine">
        <NoData />
      </Section>
    </>
  );
}
