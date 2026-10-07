import { useLayoutEffect, useRef } from 'react';

import { approvalsStore } from '../state/approval-store.ts';
import { jobsStore } from '../state/plasma-inputs.ts';
import { useStore } from '../state/store.ts';
import { tickStore } from '../state/tick.ts';
import { Bar, Loading, NoData, Spark, Val } from './bits.tsx';
import { ChatDock, DOCK_MIN_PX } from './Chat.tsx';
import { diskStore, jobsSeenStore, machineStore } from './stores.ts';

export const MACHINE_STALE_MS = 3000;

export function MachinePanel() {
  const machine = useStore(machineStore);
  const disk = useStore(diskStore);
  const now = useStore(tickStore);
  const live = machine !== null && now - machine.at <= MACHINE_STALE_MS;
  const cpu = machine?.cpu[machine.cpu.length - 1];
  const mem = machine?.mem[machine.mem.length - 1];
  return (
    <section className={`pnl br${machine && !live ? ' stale' : ''}`}>
      <div className="ph">
        <span className="lab">MACHINE</span>
        <span className="lab">{machine ? (live ? 'LIVE' : 'STALE') : ''}</span>
      </div>
      {machine === null || cpu === undefined || mem === undefined ? (
        <Loading rows={4} />
      ) : (
        <>
          <div className="vt">
            <span className="lab">CPU</span>
            <Spark values={machine.cpu} h={16} />
            <Val className="n" text={`${Math.round(cpu * 100)}%`} />
          </div>
          <div className="vt">
            <span className="lab">MEM</span>
            <Bar value={mem} />
            <Val className="n" text={`${Math.round(mem * 100)}%`} />
          </div>
          <div className="vt">
            <span className="lab">DISK</span>
            {disk ? <Bar value={disk.usedBytes / disk.totalBytes} /> : <span className="dim">NO DATA</span>}
            <span className="n">{disk ? `${Math.round((disk.usedBytes / disk.totalBytes) * 100)}%` : ''}</span>
          </div>
          <div className="vt">
            <span className="lab">NET</span>
            <span className="dim">NO DATA</span>
            <span className="n" />
          </div>
        </>
      )}
    </section>
  );
}

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'needsReview']);

export function JobsPanel() {
  const jobs = useStore(jobsStore);
  const seen = useStore(jobsSeenStore);
  const run = jobs.filter((j) => !TERMINAL.has(j.status) && j.status !== 'queued');
  return (
    <section className="pnl br">
      <div className="ph">
        <span className="lab">JOBS</span>
        <span className="lab">{seen.size > 0 ? `${run.length} RUNNING` : ''}</span>
      </div>
      {seen.size === 0 ? (
        <NoData why="No job events. The daemon does not send evt.job.* yet." />
      ) : run.length === 0 ? (
        <div className="dim">Nothing running.</div>
      ) : (
        run.slice(0, 3).map((j) => (
          <div key={j.jobId} className="jb">
            <div className="t">
              <span>{j.title || j.jobId}</span>
              <Val className="p" text={`${Math.round(j.progress * 100)}%`} />
            </div>
            <Bar value={j.progress} amber={j.status === 'blocked'} />
          </div>
        ))
      )}
    </section>
  );
}

export function WaitingPanel() {
  const entries = useStore(approvalsStore);
  const live = entries.filter((e) => e.invalidated === null);
  const first = live[0];
  return (
    <section className={`pnl br${first ? ' waitbox' : ''}`}>
      <div className="ph">
        <span className="lab">WAITING ON YOU</span>
        <span className="lab">{live.length}</span>
      </div>
      {first ? (
        <>
          <div className="mid amb">{first.request.tool}</div>
          <div className="dim">{live.length > 1 ? `${live.length} waiting. ` : ''}Answer on the card.</div>
        </>
      ) : (
        <div className="dim">Nothing is waiting on you.</div>
      )}
    </section>
  );
}

export function PinnedPanel() {
  return (
    <section className="pnl br pinbox">
      <div className="ph">
        <span className="lab">PINNED</span>
        <span className="lab" />
      </div>
      <NoData why="Pinned commands are not built yet." />
    </section>
  );
}

export function Band({ x, w, top, h }: { x: number; w: number; top: number; h: number }) {
  const bandRef = useRef<HTMLElement>(null);
  const topRef = useRef<HTMLDivElement>(null);
  const dockRef = useRef<HTMLElement>(null);
  const shown = w > 0;
  useLayoutEffect(() => {
    const band = bandRef.current;
    const head = topRef.current;
    const dock = dockRef.current;
    if (!shown || !band || !head || !dock) return;
    const fit = (): void => {
      head.classList.remove('nopin');
      if (dock.getBoundingClientRect().height < DOCK_MIN_PX) head.classList.add('nopin');
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(band);
    for (const el of Array.from(head.children)) if (!el.classList.contains('pinbox')) ro.observe(el);
    return () => ro.disconnect();
  }, [shown]);
  if (!shown) return null;
  return (
    <aside ref={bandRef} className="band" style={{ left: Math.round(x), top, width: Math.round(w), height: h }}>
      <div ref={topRef} className="bandtop">
        <MachinePanel />
        <JobsPanel />
        <WaitingPanel />
        <PinnedPanel />
      </div>
      <ChatDock dockRef={dockRef} />
    </aside>
  );
}
