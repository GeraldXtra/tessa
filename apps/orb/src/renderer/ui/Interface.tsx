import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import type { BootstrapInfo } from '../../shared/ipc-contract.ts';
import type { PlasmaEngine } from '../scene/plasma-engine.ts';
import { approvalsStore } from '../state/approval-store.ts';
import { closeQuick, composeStore, requestComposeFocus } from '../state/compose-store.ts';
import { connectionStore, railStore, useStore } from '../state/store.ts';
import { Band } from './Band.tsx';
import { CalendarPanel } from './CalendarPanel.tsx';
import { Card, liveEntries } from './Card.tsx';
import { QuickLine } from './Chat.tsx';
import { Drawer } from './Drawers.tsx';
import { CaptionLine, Chip } from './Followers.tsx';
import { installKeys, toggleRail } from './keys.ts';
import { computeLayout, MARGIN, TOP } from './layout.ts';
import { Notes, TraceStack } from './LeftColumn.tsx';
import { Rails } from './Rails.tsx';
import { dockShownStore, engineViewStore } from './stores.ts';
import { TopBar } from './TopBar.tsx';

function useViewport(): { w: number; h: number } {
  const [v, setV] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }));
  useEffect(() => {
    const on = (): void => setV({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return v;
}

const report = (line: string): void => window.tessa.reportMetrics(line);

function focusedPlace(): string | null {
  const el = document.activeElement;
  return el instanceof HTMLTextAreaElement ? (el.dataset['place'] ?? null) : null;
}

export function Interface({ bootstrap, engine, sphere }: { bootstrap: BootstrapInfo; engine: PlasmaEngine | null; sphere: ReactNode }) {
  const viewport = useViewport();
  const rail = useStore(railStore);
  const entries = useStore(approvalsStore);
  const connection = useStore(connectionStore);
  const cardUp = liveEntries(entries).length > 0;
  const slot = rail !== null || cardUp;
  const layout = useMemo(() => computeLayout(viewport.w, viewport.h, slot), [viewport, slot]);
  const chipRef = useRef<HTMLDivElement>(null);
  const capRef = useRef<HTMLDivElement>(null);
  const quickRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLElement>(null);
  const hadCard = useRef(false);
  const focusCard = useRef(false);
  const placeAtRender = useRef<string | null>(null);
  placeAtRender.current = focusedPlace();
  const dockShown = layout.bandW > 0;

  useEffect(() => {
    engine?.setPlacement(layout.cx, layout.cy, layout.R);
  }, [engine, layout]);

  useEffect(() => {
    engine?.setFollowers({ chip: chipRef.current, caption: capRef.current, quick: quickRef.current });
  }, [engine]);

  useLayoutEffect(() => {
    dockShownStore.set(dockShown);
    if (!dockShown || !composeStore.get().quickOpen) return;
    const hadFocus = focusedPlace() === 'quick';
    closeQuick();
    if (hadFocus) requestComposeFocus('dock');
    report(`QUICK closed: the chat dock is showing again; the dock has the draft (${composeStore.get().draft.length} chars)${hadFocus ? ' and the focus' : ''}`);
  }, [dockShown]);

  useEffect(() => {
    if (!bootstrap.isDev) return;
    report(
      `LAYOUT window=${layout.W}x${layout.H} slot=${layout.slot} R=${layout.R.toFixed(1)} diameter=${(2 * layout.R).toFixed(1)} ` +
        `cx=${layout.cx.toFixed(1)} cy=${layout.cy.toFixed(1)} cal=${layout.cal} dw=${layout.dw} lcW=${layout.lcW.toFixed(1)} ` +
        `colH=${layout.colH} trace=${layout.traceShown} bandX=${layout.bandX.toFixed(1)} bandW=${layout.bandW.toFixed(1)} ` +
        `narrow=${layout.narrow} guarded=${layout.guarded} stalled=${layout.stalled}`,
    );
  }, [bootstrap.isDev, layout]);

  useLayoutEffect(() => {
    if (cardUp && !hadCard.current) {
      const was = placeAtRender.current;
      const inQuick = was === 'quick' || focusedPlace() === 'quick';
      if (railStore.get() !== null) {
        railStore.set(null);
        focusCard.current = !inQuick;
        report('CARD closed the open drawer; it stays closed');
      } else if (was === 'dock') {
        focusCard.current = true;
        report(`CARD hid the chat dock while he was typing in it; the draft is kept (${composeStore.get().draft.length} chars)`);
      }
      if (inQuick) report('CARD arrived while the quick line has focus; it keeps its focus and text and cannot answer the card');
    }
    hadCard.current = cardUp;
  }, [cardUp]);

  useEffect(() => {
    if (!focusCard.current || !cardUp) return;
    focusCard.current = false;
    cardRef.current?.focus();
    report(`CARD focus=${document.activeElement === cardRef.current ? 'card' : (document.activeElement?.tagName ?? 'none')}`);
  });

  useEffect(() => installKeys(report), []);

  useEffect(() => {
    if (!engine) return;
    const id = window.setInterval(() => {
      const s = engine.stats();
      engineViewStore.set({
        fps: s.fps,
        p95: s.intervalP95,
        scale: s.scale,
        changeP95: s.changeP95,
        changeCount: s.changeCount,
        gpu: s.rendererShort,
        path: s.path,
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [engine]);

  const offline = connection.phase !== 'connected';
  return (
    <div className={`orb${layout.narrow ? ' narrow' : ''}${offline ? ' offline' : ''}`}>
      {sphere}
      <TopBar narrow={layout.narrow} fixture={bootstrap.fixture} />
      <Chip chipRef={chipRef} />
      <CaptionLine capRef={capRef} />
      <QuickLine quickRef={quickRef} />
      {layout.colShown ? (
        <div className="col" style={{ left: MARGIN, top: layout.colTop, width: Math.round(layout.lcW), height: layout.colH }}>
          <Notes />
          {layout.traceShown ? <TraceStack /> : null}
        </div>
      ) : null}
      <Band x={layout.bandX} w={layout.bandW} top={TOP + 12} h={layout.H - TOP - 24} />
      <CalendarPanel mode={layout.cal} />
      <Rails cardWaiting={cardUp} onToggle={(id) => toggleRail(id, report, `click:${id}`)} />
      {!cardUp && rail ? <Drawer key={rail} id={rail} dw={layout.dw} /> : null}
      <Card ref={cardRef} width={layout.dw || 372} />
    </div>
  );
}
