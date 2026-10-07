import type { ConnectionPhase, DaemonHealth } from '../../shared/ipc-contract.ts';
import { connectionStore, healthStore, micStore, useStore } from '../state/store.ts';
import { tickStore } from '../state/tick.ts';
import { Bar, Val } from './bits.tsx';
import { DAYS, MONTHS, dur, hms, naira, pad2 } from './format.ts';
import { beatsStore } from './stores.ts';

export const LINK_WORD: Record<ConnectionPhase, string> = {
  offline: 'OFFLINE',
  connecting: 'CONNECTING',
  connected: 'CONNECTED',
  authRejected: 'AUTH REJECTED',
  protocolMismatch: 'PROTOCOL MISMATCH',
  reconnecting: 'OFFLINE',
};

export const BEAT_STALE_MS = 15_000;

export type Tick = 'on' | 'miss' | 'none';

export function beatTicks(beats: readonly number[], now: number, n: number, live: boolean): Tick[] {
  const out: Tick[] = [];
  for (let i = 0; i < beats.length; i++) {
    const prev = beats[i - 1];
    const cur = beats[i] as number;
    if (prev !== undefined) {
      const missed = Math.min(n, Math.max(0, Math.round((cur - prev) / 5000) - 1));
      for (let k = 0; k < missed; k++) out.push('miss');
    }
    out.push('on');
  }
  const last = beats[beats.length - 1];
  if (last !== undefined) {
    const gap = now - last;
    if (gap > 7500 || !live) {
      const missed = Math.min(n, Math.max(0, Math.floor(gap / 5000)));
      for (let k = 0; k < missed; k++) out.push('miss');
    }
  }
  const tail = out.slice(-n);
  while (tail.length < n) tail.unshift('none');
  return tail;
}

export function BeatStrip({ n, tall = false }: { n: number; tall?: boolean }) {
  const beats = useStore(beatsStore);
  const now = useStore(tickStore);
  const live = useStore(connectionStore).phase === 'connected';
  const ticks = beatTicks(beats, now, n, live);
  return (
    <span className={`hb${tall ? ' tall' : ''}`} aria-label="Last heartbeats">
      {ticks.map((t, i) => (
        <i key={i} className={t === 'on' ? 'on' : t === 'miss' ? 'miss' : ''} />
      ))}
    </span>
  );
}

export function uptimeNow(health: DaemonHealth | null, now: number): number | null {
  if (!health) return null;
  const age = now - health.receivedAt;
  if (age > BEAT_STALE_MS) return null;
  return health.uptimeS + Math.max(0, age) / 1000;
}

function chordWords(chord: string): string {
  return chord
    .split('+')
    .map((p) => (p === 'Control' || p === 'CommandOrControl' ? 'CTRL' : p.toUpperCase()))
    .join(' ');
}

export function TopBar({ narrow, fixture }: { narrow: boolean; fixture: string | null }) {
  const connection = useStore(connectionStore);
  const health = useStore(healthStore);
  const mic = useStore(micStore);
  const now = useStore(tickStore);
  const connected = connection.phase === 'connected';
  const terminal = connection.phase === 'authRejected' || connection.phase === 'protocolMismatch';

  const micText = mic.claimed
    ? 'PTT OPEN'
    : mic.mode === 'hold'
      ? 'PTT HOLD, FOCUS ONLY'
      : mic.chord && !mic.chordRegistered
        ? 'PTT CHORD TAKEN'
        : mic.chord
          ? 'PTT READY'
          : 'PTT NOT SET';

  const up = connected ? uptimeNow(health, now) : null;
  const cap = health?.budgetCap ?? 0;
  const spend = connected && health && cap > 0 ? health : null;

  const d = new Date(now);
  const date = `${DAYS[d.getDay()]} ${pad2(d.getDate())} ${MONTHS[d.getMonth()]?.slice(0, 3) ?? ''}`;

  return (
    <header className="tb">
      <div className="c brand">TESSA</div>
      <div className={`c${mic.claimed ? ' warn' : ''}`}>
        <span className="k">MIC</span>
        <Val text={micText} />
        {!narrow && mic.chord ? <span className="k">{chordWords(mic.chord)}</span> : null}
      </div>
      <div className={`c live${connected ? '' : ' off'}`}>
        <span className="k">LINK</span>
        <Val text={LINK_WORD[connection.phase]} />
        {!narrow && connected && connection.daemonVersion ? <span className="k">v{connection.daemonVersion}</span> : null}
        <BeatStrip n={12} />
        {terminal ? (
          <button type="button" className="retry" onClick={() => window.tessa.retryConnection()}>
            RETRY
          </button>
        ) : null}
      </div>
      <div className={`c${up === null ? ' off' : ''}`}>
        <span className="k">UP</span>
        <Val text={up === null ? '--' : dur(up)} />
      </div>
      <div className="c off">
        <span className="k">DATA</span>
        <span className="v">NO DATA</span>
      </div>
      <div className={`c${spend ? '' : ' off'}`}>
        <span className="k">SPEND</span>
        <Val text={spend ? `${naira(spend.budgetSpent)} / ${naira(spend.budgetCap)}` : 'NO DATA'} />
        {spend ? <Bar value={spend.budgetSpent / spend.budgetCap} /> : null}
      </div>
      <div className="fill" />
      {fixture ? <div className="c demo">FIXTURE DATA</div> : null}
      <div className="c clock">
        {narrow ? null : <span className="k">{date}</span>}
        <Val text={hms(d)} />
      </div>
      <div className="win">
        <button type="button" aria-label="Minimise" onClick={() => window.tessa.minimizeWindow()}>
          <i className="gmin" />
        </button>
        <button type="button" aria-label="Close" onClick={() => window.tessa.closeWindow()}>
          <i className="gx" />
        </button>
      </div>
    </header>
  );
}
