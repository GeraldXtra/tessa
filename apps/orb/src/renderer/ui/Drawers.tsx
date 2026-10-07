import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';

import { Composer } from '../layout/Composer.tsx';
import {
  auditStore,
  connectionStore,
  healthHistoryStore,
  transcriptStore,
  useStore,
  type RailId,
} from '../state/store.ts';
import { tickStore } from '../state/tick.ts';
import { jobsStore } from '../state/plasma-inputs.ts';
import { composeStore, useComposeField } from '../state/compose-store.ts';
import { Bar, ErrorLine, Loading, NoData, OfflineBanner, Sec, Spark, TickScale, Val } from './bits.tsx';
import { BeatStrip } from './TopBar.tsx';
import { MACHINE_STALE_MS } from './Band.tsx';
import { TIER_MEANING, bytesText, dur, gb, hm, hms, hmsOf, isHerAction, naira, pad2, resultOf, tierOf, type Tier } from './format.ts';
import {
  auditLoadedStore,
  beatsStore,
  diskStore,
  engineViewStore,
  jobsSeenStore,
  lastHealthStore,
  machineStore,
  memTotalStore,
  notesStore,
  pingStore,
  threatStore,
} from './stores.ts';
import { DRAWERS } from './Rails.tsx';
import { ChatMessage, EMPTY_CHAT, UnconfirmedLine } from './Chat.tsx';

interface DrawerDef {
  sub: string;
  src: string;
  daemon: boolean;
  body: () => ReactNode;
}

function sameDay(iso: string, now: Date): boolean {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return false;
  const d = new Date(t);
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

function ActionRow({ a }: { a: { id: string; ts: string; tier: string; summary: string } }) {
  const tier = tierOf(a);
  const res = resultOf(a.summary);
  return (
    <div className="r">
      <time>{hmsOf(a.ts)}</time>
      <span>
        <span className={`tier${tier ? ` t-${tier}` : ''}`}>{tier ? tier.toUpperCase() : ''}</span>{' '}
        <span className={`x-${tier ?? 'none'}`}>{a.summary}</span>
      </span>
      <i className={`res ${res ? res.kind : ''}`}>{res ? res.word : ''}</i>
    </div>
  );
}

type Filter = 'all' | Tier;
let traceFilter: Filter = 'all';
const TRACE_PAGE = 60;

function TraceBody() {
  const audit = useStore(auditStore);
  const loaded = useStore(auditLoadedStore);
  const linked = useStore(connectionStore).phase === 'connected';
  const now = useStore(tickStore);
  const [filter, setFilterState] = useState<Filter>(traceFilter);
  const [shown, setShown] = useState(TRACE_PAGE);
  const setFilter = (f: Filter): void => {
    traceFilter = f;
    setFilterState(f);
    setShown(TRACE_PAGE);
  };
  if (!loaded && audit.length === 0) {
    return linked ? <Loading rows={9} /> : <NoData why="No daemon link. Her actions come from the audit stream: cmd.audit.query for history, evt.audit.appended live." />;
  }
  const mine = audit.filter(isHerAction);
  const hourMs = 3_600_000;
  const startOfHour = Math.floor(now / hourMs) * hourMs;
  const bins = Array.from({ length: 14 }, () => 0);
  for (const a of mine) {
    const t = Date.parse(a.ts);
    if (!Number.isFinite(t)) continue;
    const k = 13 - Math.floor((startOfHour + hourMs - 1 - t) / hourMs);
    if (k >= 0 && k < 14) bins[k] = (bins[k] ?? 0) + 1;
  }
  const max = Math.max(1, ...bins);
  const oldest = audit[audit.length - 1];
  const oldestAt = oldest ? Date.parse(oldest.ts) : Number.NaN;
  const partial = loaded !== null && loaded.rows >= loaded.limit && Number.isFinite(oldestAt) && oldestAt > now - 14 * hourMs;
  const rows = filter === 'all' ? mine : mine.filter((a) => tierOf(a) === filter);
  const today = new Date(now);
  return (
    <>
      <Sec label="ACTIONS PER HOUR" right={partial ? `PARTIAL, FROM ${hmsOf(oldest?.ts ?? '')}` : 'LAST 14 H'}>
        <div className="hours">
          {bins.map((v, i) => (
            <i key={i} className={i === 13 ? 'now' : ''} style={{ height: `${Math.max(3, (v / max) * 100)}%` }} title={`${v}`} />
          ))}
        </div>
      </Sec>
      <div className="chips" role="group" aria-label="Filter by tier">
        {(['all', 'green', 'amber', 'red'] as const).map((f) => (
          <button key={f} type="button" className={f === filter ? 'on' : ''} aria-pressed={f === filter} onClick={() => setFilter(f)}>
            {f.toUpperCase()}
          </button>
        ))}
      </div>
      <Sec label="LATEST" right={`${mine.filter((a) => sameDay(a.ts, today)).length} TODAY`}>
        {rows.length === 0 ? <div className="dim">No actions{filter === 'all' ? '' : ` at ${filter.toUpperCase()}`} in the audit stream.</div> : rows.slice(0, shown).map((a) => <ActionRow key={a.id} a={a} />)}
        {rows.length > shown ? (
          <button type="button" className="more" onClick={() => setShown(shown + TRACE_PAGE)}>
            SHOW {Math.min(TRACE_PAGE, rows.length - shown)} MORE, {rows.length - shown} OLDER IN THE STREAM
          </button>
        ) : null}
      </Sec>
      <Sec label="TIERS" right="CORE/CONFIG/PERMISSIONS.YAML">
        {(['green', 'amber', 'red'] as const).map((t) => (
          <div key={t} className="r">
            <span className={`tier t-${t}`}>{t.toUpperCase()}</span>
            <span className="dim">{TIER_MEANING[t]}</span>
            <em />
          </div>
        ))}
        <div className="r">
          <span className="tier" />
          <span className="dim">No tier word: the daemon sent the row without a tier.</span>
          <em />
        </div>
      </Sec>
    </>
  );
}

const REFUSAL = /^(REFUSED|DENIED|INJECTION-SEEN|STRIPPED-FORGEABLE|APPROVAL REFUSED|Rejected |\d+ failed auth)/;

function SentinelBody() {
  const threat = useStore(threatStore);
  const audit = useStore(auditStore);
  const now = new Date(useStore(tickStore));
  const caught = audit.filter((a) => REFUSAL.test(a.summary) && sameDay(a.ts, now));
  return (
    <>
      <Sec label="STATUS">
        {threat ? (
          <>
            <div className="big thr">THREAT</div>
            <div className="dim">Raised at {hms(new Date(threat.at))} by the dev T key. Opening this drawer marked it seen.</div>
          </>
        ) : (
          <NoData why="Sentinel events need the daemon. Nothing on the wire says what is watched or caught." />
        )}
      </Sec>
      <Sec label="REFUSALS IN THE AUDIT, TODAY" right={String(caught.length)}>
        {caught.length === 0 ? <div className="dim">None today.</div> : caught.map((a) => <ActionRow key={a.id} a={a} />)}
      </Sec>
      <Sec label="WATCHING">
        <NoData why="The daemon does not send a list of what it watches." />
      </Sec>
    </>
  );
}

function PulseBody() {
  const health = useStore(lastHealthStore);
  const history = useStore(healthHistoryStore);
  const machine = useStore(machineStore);
  const disk = useStore(diskStore);
  const ping = useStore(pingStore);
  const view = useStore(engineViewStore);
  const beats = useStore(beatsStore);
  const now = useStore(tickStore);
  const linked = useStore(connectionStore).phase === 'connected';

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const run = (): void => {
      void window.tessa.ping().then((r) => {
        if (!alive) return;
        if (r.ok || !r.error.startsWith('rate limited')) pingStore.set(r);
        timer = window.setTimeout(run, 5000);
      });
    };
    run();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  const cpu = machine?.cpu[machine.cpu.length - 1];
  const mem = machine?.mem[machine.mem.length - 1];
  const machineLive = machine !== null && now - machine.at <= MACHINE_STALE_MS;
  const lastBeat = beats[beats.length - 1];
  const cap = health?.budgetCap ?? 0;
  const memTotal = useStore(memTotalStore);
  return (
    <>
      <Sec label="HEARTBEAT" right="EVERY 5 S">
        <div className="beatrow">
          <BeatStrip n={24} tall />
          <span className="dim">LAST {lastBeat === undefined ? '--' : hms(new Date(lastBeat))}</span>
        </div>
      </Sec>
      <Sec label="CPU, THIS MACHINE" right={cpu === undefined ? '' : <Val className="lab" text={`${Math.round(cpu * 100)}%`} />}>
        {machine === null ? (
          <Loading rows={2} />
        ) : (
          <div className={machineLive ? '' : 'stale'}>
            <TickScale value={cpu ?? null} />
            <Spark values={machine.cpu} h={32} />
          </div>
        )}
      </Sec>
      <Sec label="MEMORY, THIS MACHINE" right={mem === undefined ? '' : <Val className="lab" text={memTotal > 0 ? `${((mem * memTotal) / 1024).toFixed(1)} OF ${(memTotal / 1024).toFixed(1)} GB` : `${Math.round(mem * 100)}%`} />}>
        {machine === null || mem === undefined ? (
          <Loading rows={2} />
        ) : (
          <div className={machineLive ? '' : 'stale'}>
            <Bar value={mem} />
            <div className="gap6" />
            <Spark values={machine.mem} h={24} />
          </div>
        )}
      </Sec>
      <Sec label={`DISK ${disk ? disk.drive : ''}`} right={disk ? `${gb(disk.usedBytes)} OF ${gb(disk.totalBytes)} GB` : ''}>
        {disk ? <Bar value={disk.usedBytes / disk.totalBytes} /> : <NoData why="fs.statfs on the system drive has not answered." />}
      </Sec>
      <Sec label="NETWORK">
        <NoData why="Nothing measures network speed or data used yet." />
      </Sec>
      <Sec label="ROUND TRIP" right="CMD.PING EVERY 5 S">
        {ping === null ? (
          <Loading rows={1} />
        ) : ping.ok ? (
          <div className="mid">
            <Val className="v" text={`${ping.ms.toFixed(1)} MS`} />
          </div>
        ) : (
          <ErrorLine text={ping.error} />
        )}
      </Sec>
      <div className={linked ? '' : 'stale'}>
        <Sec label="DAEMON PROCESS" right="FROM EVT.DAEMON.HEALTH">
          {health ? (
            <div className="kv">
              <div>
                <span className="lab">CPU</span>
                <div className="mid">{health.cpuPct.toFixed(1)}%</div>
              </div>
              <div>
                <span className="lab">MEMORY</span>
                <div className="mid">{bytesText(health.memMB * 1024 * 1024)}</div>
              </div>
            </div>
          ) : linked ? (
            <Loading rows={1} />
          ) : (
            <NoData why="No heartbeat yet." />
          )}
        </Sec>
        <Sec label="SPEND" right={health && cap > 0 ? `${naira(health.budgetSpent)} OF ${naira(cap)}` : ''}>
          {health && cap > 0 ? (
            <>
              <Bar value={health.budgetSpent / cap} />
              <div className="gap6" />
              <Spark values={history.map((h) => h.budgetSpent)} h={24} min={0} max={cap} />
            </>
          ) : health ? (
            <NoData why="The daemon sent no spending cap, so spend against the cap cannot be shown." />
          ) : linked ? (
            <Loading rows={1} />
          ) : (
            <NoData why="No heartbeat yet." />
          )}
        </Sec>
      </div>
      <Sec label="RENDER" right="THE ORB MEASURES THIS">
        <div className="kv">
          <div>
            <span className="lab">FRAME RATE</span>
            <div className="mid">{view && view.fps > 0 ? `${Math.round(view.fps)} FPS` : '--'}</div>
          </div>
          <div>
            <span className="lab">FRAME P95</span>
            <div className="mid">{view && view.p95 > 0 ? `${view.p95.toFixed(1)} MS` : '--'}</div>
          </div>
          <div>
            <span className="lab">STATE TO FRAME</span>
            <div className="mid">{view && view.changeCount > 0 ? `${Math.round(view.changeP95)} MS` : '--'}</div>
          </div>
          <div>
            <span className="lab">SCALE</span>
            <div className="mid">{view ? `${Math.round(view.scale * 100)}%` : '--'}</div>
          </div>
          <div className="wide">
            <span className="lab">GPU</span>
            <div className="small">{view ? `${view.gpu} (${view.path})` : '--'}</div>
          </div>
        </div>
      </Sec>
    </>
  );
}

function JobsBody() {
  const jobs = useStore(jobsStore);
  const seen = useStore(jobsSeenStore);
  const now = useStore(tickStore);
  if (seen.size === 0) return <NoData why="No jobs yet. The daemon does not send evt.job.* today, so nothing here can be real." />;
  const all = [...seen.values()];
  const running = jobs.filter((j) => j.status === 'running' || j.status === 'blocked');
  const queued = jobs.filter((j) => j.status === 'queued');
  const finished = all.filter((s) => s.endedAt !== null).sort((a, b) => (b.endedAt ?? 0) - (a.endedAt ?? 0));
  const span = 3_600_000;
  return (
    <>
      <Sec label="RUNNING" right={String(running.length)}>
        {running.length === 0 ? (
          <div className="dim">Nothing running.</div>
        ) : (
          running.map((j) => {
            const s = seen.get(j.jobId);
            return (
              <div key={j.jobId} className="jb">
                <div className="t">
                  <span>{j.title || j.jobId}</span>
                  <Val className="p" text={`${Math.round(j.progress * 100)}%`} />
                </div>
                <Bar value={j.progress} amber={j.status === 'blocked'} />
                <div className="t sub">
                  <span className={`lab${j.status === 'blocked' ? ' amb' : ''}`}>{j.status === 'blocked' ? 'WAITING ON YOU' : 'RUNNING'}</span>
                  <Val className="lab" text={s ? dur((now - s.firstAt) / 1000).slice(3) : '--'} />
                </div>
              </div>
            );
          })
        )}
      </Sec>
      <Sec label="TIMELINE" right="LAST 60 MIN, AS THE ORB SAW IT">
        <svg width="100%" height={12 + all.length * 13 + 6} viewBox={`0 0 300 ${12 + all.length * 13 + 6}`} preserveAspectRatio="none" aria-hidden="true">
          {Array.from({ length: 7 }, (_, i) => (
            <line key={i} className="tl" x1={(i / 6) * 299 + 0.5} y1="0" x2={(i / 6) * 299 + 0.5} y2={i % 3 === 0 ? 6 : 3} />
          ))}
          {all.map((s, i) => {
            const a = Math.min(span, now - s.firstAt);
            const b = s.endedAt === null ? 0 : Math.min(span, now - s.endedAt);
            const x0 = (1 - a / span) * 300;
            const x1 = Math.max(x0 + 2, (1 - b / span) * 300);
            const yy = 12 + i * 13;
            return (
              <g key={s.view.jobId}>
                <rect className={s.endedAt === null ? 'tl-on' : 'tl-off'} x={x0.toFixed(1)} y={yy} width={(x1 - x0).toFixed(1)} height="3" />
                <text className="tl-t" x={Math.min(x0, 190).toFixed(1)} y={yy + 11}>
                  {s.view.title || s.view.jobId}
                </text>
              </g>
            );
          })}
        </svg>
        <div className="ends">
          <span className="lab">60 MIN AGO</span>
          <span className="lab">NOW</span>
        </div>
      </Sec>
      <Sec label="QUEUED" right={String(queued.length)}>
        {queued.length === 0 ? <div className="dim">Nothing queued.</div> : queued.map((j) => (
          <div key={j.jobId} className="r">
            <span className="lab">NEXT</span>
            <span>{j.title || j.jobId}</span>
            <em>QUEUED</em>
          </div>
        ))}
      </Sec>
      <Sec label="FINISHED THIS SESSION" right={String(finished.length)}>
        {finished.length === 0 ? <div className="dim">Nothing finished yet.</div> : finished.map((s) => (
          <div key={s.view.jobId} className="r">
            <time>{hm(new Date(s.endedAt ?? 0))}</time>
            <span>{s.view.title || s.view.jobId}</span>
            <i className={`res ${s.view.status === 'succeeded' ? 'ok' : 'bad'}`}>{s.view.status === 'succeeded' ? 'OK' : s.view.status.toUpperCase()}</i>
          </div>
        ))}
      </Sec>
    </>
  );
}

const STICK_SLOP_PX = 24;

function ChatBody({ scroller }: { scroller: () => HTMLElement | null }) {
  const lines = useStore(transcriptStore);
  const unconfirmed = useComposeField('unconfirmed');
  const following = useRef(true);
  useEffect(() => {
    const el = scroller();
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    const onScroll = (): void => {
      following.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLOP_PX;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, [scroller]);
  useLayoutEffect(() => {
    const el = scroller();
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [lines.length, lines[lines.length - 1]?.messageId, unconfirmed, scroller]);
  return (
    <>
      {lines.length === 0 ? <div className="dim">{EMPTY_CHAT}</div> : lines.map((l, i) => <ChatMessage key={`${l.messageId}-${i}`} line={l} />)}
      {unconfirmed ? <UnconfirmedLine u={unconfirmed} /> : null}
    </>
  );
}

function ArsenalBody() {
  const audit = useStore(auditStore);
  const now = new Date(useStore(tickStore));
  const uses = new Map<string, { n: number; tier: string }>();
  for (const a of audit) {
    if (!isHerAction(a) || !sameDay(a.ts, now)) continue;
    const u = uses.get(a.tool);
    uses.set(a.tool, { n: (u?.n ?? 0) + 1, tier: u?.tier ?? a.tier });
  }
  const list = [...uses.entries()].sort((x, y) => y[1].n - x[1].n);
  return (
    <>
      <NoData why="Her tool list needs the daemon. Nothing on the wire says which tools exist, their tiers, or whether each is on." />
      <div className="gap12" />
      <Sec label="AUDIT ROWS PER TOOL, TODAY" right={String(list.length)}>
        {list.length === 0 ? <div className="dim">None today.</div> : list.map(([tool, u]) => {
          const tier = tierOf(u);
          return (
            <div key={tool} className="r">
              <span className={`tier${tier ? ` t-${tier}` : ''}`}>{tier ? tier.toUpperCase() : ''}</span>
              <span>{tool}</span>
              <em>{u.n} TODAY</em>
            </div>
          );
        })}
      </Sec>
    </>
  );
}

function RecallBody() {
  return <NoData why="Her memory feed needs the daemon. The learning system exists, but nothing reaches the Orb yet." />;
}

function SignalBody() {
  const notes = useStore(notesStore);
  return (
    <>
      <Sec label="NOTIFICATIONS" right="THIS SESSION">
        {notes.length === 0 ? <div className="dim">No notifications yet this session.</div> : notes.map((n) => (
          <div key={n.id} className="r">
            <time>{hms(new Date(n.at))}</time>
            <span>
              <span className={`lab${n.kind === 'threat' ? ' thr' : n.kind === 'wait' ? ' amb' : ''}`}>{n.src}</span> {n.msg}
            </span>
            <em />
          </div>
        ))}
      </Sec>
      <NoData why="Channels and direct messages need the daemon. Telegram and email are not built yet." />
    </>
  );
}

const DEFS: Readonly<Record<RailId, DrawerDef>> = {
  trace: { sub: 'Every action she takes, newest first', src: 'CMD.AUDIT.QUERY (LAST 800 ROWS) + EVT.AUDIT.APPENDED LIVE. TIERS: CORE/CONFIG/PERMISSIONS.YAML', daemon: true, body: () => <TraceBody /> },
  sentinel: { sub: 'What is watched, and what was caught', src: 'NO SENTINEL FEED YET. REFUSALS: THE AUDIT STREAM', daemon: true, body: () => <SentinelBody /> },
  pulse: { sub: 'Heartbeat, machine, spend and render', src: 'EVT.DAEMON.HEALTH EVERY 5 S. MACHINE: THE ORB, EVERY 1 S. DISK: FS.STATFS EVERY 10 S. ROUND TRIP: CMD.PING', daemon: true, body: () => <PulseBody /> },
  jobs: { sub: 'Running, queued and finished', src: 'EVT.JOB.* (THE DAEMON DOES NOT SEND THEM YET)', daemon: true, body: () => <JobsBody /> },
  chat: { sub: 'Type to her when you cannot talk', src: 'EVT.TRANSCRIPT.MESSAGE. YOUR LINE: CMD.AGENT.MESSAGE', daemon: false, body: () => null },
  arsenal: { sub: 'Her tools and what each one may do', src: 'NO TOOL LIST ON THE WIRE. ROWS PER TOOL: THE AUDIT STREAM', daemon: true, body: () => <ArsenalBody /> },
  recall: { sub: 'What she remembers and has learned', src: 'NO MEMORY FEED ON THE WIRE YET', daemon: true, body: () => <RecallBody /> },
  signal: { sub: 'Channels, messages and notifications', src: 'NOTIFICATIONS: THIS ORB, THIS SESSION. CHANNELS: NOTHING ON THE WIRE', daemon: false, body: () => <SignalBody /> },
};

export function Drawer({ id, dw }: { id: RailId; dw: number }) {
  const ref = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const linked = useStore(connectionStore).phase === 'connected';
  const beats = useStore(beatsStore);
  const compose = useStore(composeStore);
  const def = DEFS[id];
  const index = DRAWERS.findIndex((d) => d.id === id) + 1;
  const name = DRAWERS[index - 1]?.name ?? id.toUpperCase();
  const scroller = useRef(() => bodyRef.current).current;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.setProperty('--sh', `${el.clientHeight}px`);
    el.classList.remove('scan');
    void el.offsetWidth;
    el.classList.add('scan');
  }, [id]);

  const threat = useStore(threatStore);
  useEffect(() => {
    if (id === 'sentinel' && threat && !threat.seen) threatStore.set({ ...threat, seen: true });
  }, [id, threat]);

  const offline = def.daemon && !linked;
  return (
    <section ref={ref} className="slot drawer br" style={{ width: dw }} aria-label={name}>
      <i className="scanline" />
      <div className="dh">
        <div>
          <div className="nm">{name}</div>
          <div className="sub">{def.sub}</div>
        </div>
        <div className="ix">
          {pad2(index)} / 08
          <br />
          ESC CLOSES
        </div>
      </div>
      <div className="db" ref={bodyRef}>
        {offline ? <OfflineBanner lastBeatAt={beats[beats.length - 1] ?? null} /> : null}
        <div className={offline ? 'stale' : ''}>{id === 'chat' ? <ChatBody scroller={scroller} /> : def.body()}</div>
      </div>
      {id === 'chat' && compose.error ? <ErrorLine text={compose.error} /> : null}
      {id === 'chat' ? <Composer place="drawer" /> : null}
      <div className="df">SOURCE  {def.src}</div>
    </section>
  );
}
