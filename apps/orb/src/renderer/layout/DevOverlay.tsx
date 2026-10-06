import { useEffect, useState } from 'react';

import type { PlasmaStats } from '../scene/plasma-engine.ts';

interface DevOverlayProps {
  readStats: (() => PlasmaStats) | null;
  devKeys: boolean;
}

function ms(v: number): string {
  return v.toFixed(v < 10 ? 2 : 1);
}

export function DevOverlay({ readStats, devKeys }: DevOverlayProps) {
  const [stats, setStats] = useState<PlasmaStats | null>(null);

  useEffect(() => {
    if (!readStats) {
      setStats(null);
      return;
    }
    const id = window.setInterval(() => setStats(readStats()), 500);
    return () => window.clearInterval(id);
  }, [readStats]);

  if (!stats) return <div className="dev-overlay">no engine</div>;

  return (
    <div className="dev-overlay" data-stale={!stats.focused}>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">fps</span>
        <span>{`${stats.fps.toFixed(1)} · target ${stats.targetFps.toFixed(0)} · refresh ${ms(stats.refreshMs)}ms`}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">frame</span>
        <span>{`p50 ${ms(stats.intervalP50)} · p95 ${ms(stats.intervalP95)}ms · dropped ${stats.dropped}/${stats.samples}`}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">gpu</span>
        <span>{stats.timerQuery ? `p50 ${ms(stats.gpuP50)} · p90 ${ms(stats.gpuP90)}ms` : 'no timer query'}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">scale</span>
        <span>{`${stats.scale.toFixed(2)} · ${stats.canvas.bufW}x${stats.canvas.bufH} · R ${stats.radius.toFixed(0)}px`}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">change</span>
        <span>
          {`p95 ${stats.changeCount > 0 ? `${stats.changeP95.toFixed(0)}ms` : '--'}`}
          {stats.lastSettle
            ? ` · ${stats.lastSettle.label} settled ${stats.lastSettle.ms.toFixed(0)} of ${stats.lastSettle.budget}ms`
            : ''}
        </span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">path</span>
        <span className="dev-overlay__wrap">{`${stats.path} — ${stats.reason}`}</span>
      </div>
      <div className="dev-overlay__row">
        <span className="dev-overlay__key">gpu</span>
        <span className="dev-overlay__wrap">{stats.rendererShort}</span>
      </div>
      <div className="dev-overlay__row dev-overlay__row--hint">
        <span className="dev-overlay__key">keys</span>
        <span>
          {devKeys
            ? '1-6 states · W wake · I interrupt · T threat · D daemon · J job · M mute · S summon · N night · K 30fps · P reel · H hide'
            : 'alt+1…6 states · alt+0 hides this · esc closes drawer'}
        </span>
      </div>
    </div>
  );
}
