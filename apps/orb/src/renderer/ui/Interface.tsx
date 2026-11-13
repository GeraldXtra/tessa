import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import type { BootstrapInfo } from '../../shared/ipc-contract.ts';
import type { PlasmaEngine } from '../scene/plasma-engine.ts';
import { approvalsStore } from '../state/approval-store.ts';
import { closeQuick, composeStore, requestComposeFocus } from '../state/compose-store.ts';
import { agentStateStore, connectionStore, RAIL_IDS, railStore, useStore } from '../state/store.ts';
import { stageStore } from '../state/scene-inputs.ts';
import { rawStateStore, stopHer, stoppable, turnStore } from '../state/voice.ts';
import { Band } from './Band.tsx';
import { Boot } from './Boot.tsx';
import { CalendarPanel } from './CalendarPanel.tsx';
import { Card, liveEntries } from './Card.tsx';
import { QuickLine } from './Chat.tsx';
import { Drawer } from './Drawers.tsx';
import { CaptionLine, Chip } from './Followers.tsx';
import { installKeys, openRail, toggleRail } from './keys.ts';
import { CAL_COMPACT, CAL_FULL, computeCompact, computeLayout, MARGIN, RAILS, TOP } from './layout.ts';
import { Notes, TraceStack } from './LeftColumn.tsx';
import { closePalette, modeStore, paletteStore, setMode } from './modes.ts';
import { Palette } from './Palette.tsx';
import { Rails } from './Rails.tsx';
import { bootStore, dockShownStore, engineViewStore, pushNote, themeStampStore } from './stores.ts';
import { TopBar } from './TopBar.tsx';

export const COMPACT_SETTLE_MS = 1200;

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
  const mode = useStore(modeStore);
  const palette = useStore(paletteStore);
  const state = useStore(agentStateStore);
  const themeStamp = useStore(themeStampStore);
  const boot = useStore(bootStore);
  useStore(rawStateStore);
  useStore(turnStore);
  const cardUp = liveEntries(entries).length > 0;
  const compact = mode === 'compact';
  const paletteShown = palette.open && !cardUp && !compact;
  const drawerShown = rail !== null && !cardUp && !paletteShown && mode === 'full';
  const slot = drawerShown || cardUp || paletteShown;
  const layout = useMemo(
    () => (compact ? computeCompact(viewport.w, viewport.h, cardUp) : computeLayout(viewport.w, viewport.h, slot)),
    [viewport, slot, compact, cardUp],
  );
  const chipRef = useRef<HTMLDivElement>(null);
  const capRef = useRef<HTMLDivElement>(null);
  const quickRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLElement>(null);
  const hadCard = useRef(false);
  const focusCard = useRef(false);
  const placeAtRender = useRef<string | null>(null);
  const placedCompact = useRef(compact);
  placeAtRender.current = focusedPlace();
  const dockShown = mode === 'full' && layout.bandW > 0;

  useEffect(() => {
    engine?.setCompact(compact);
  }, [engine, compact]);

  useEffect(() => {
    if (!engine) return;
    const snap = placedCompact.current !== compact;
    placedCompact.current = compact;
    engine.setPlacement(layout.cx, layout.cy, layout.R, snap);
  }, [engine, layout, compact]);

  useEffect(() => {
    engine?.setFollowers({ chip: chipRef.current, caption: capRef.current, quick: quickRef.current });
  }, [engine]);

  useEffect(() => {
    if (compact) {
      stageStore.set(null);
      return;
    }
    const colRight = layout.colShown ? MARGIN + layout.lcW + 14 : MARGIN + 8;
    const calRight = layout.cal === 'full' ? MARGIN + CAL_FULL.w + 14 : layout.cal === 'compact' ? MARGIN + CAL_COMPACT.w + 14 : 0;
    const x1 = layout.bandW > 0 ? layout.bandX - 12 : layout.W - RAILS - layout.dw - 12;
    stageStore.set({ x0: Math.max(colRight, calRight), y0: TOP + 10, x1, y1: layout.cy + layout.R * 0.9 });
  }, [layout, compact]);

  const canStop = stoppable(connection.phase === 'connected', cardUp);
  useEffect(() => {
    window.tessa.setWindowMode(mode, compact && cardUp, cardUp, canStop);
  }, [mode, compact, cardUp, canStop]);

  useEffect(
    () =>
      window.tessa.onTrayCommand((command) => {
        if (command.kind === 'mode') {
          setMode(command.mode, report, 'tray');
          return;
        }
        if (command.kind === 'rail') {
          const id = RAIL_IDS.find((r) => r === command.id);
          if (!id) {
            report(`TRAY open refused: "${command.id}" is not a rail`);
            return;
          }
          if (!openRail(id, report, `tray:${id}`)) pushNote('ORB', 'Answer the approval first.', 'wait');
          return;
        }
        if (command.kind === 'stop') {
          if (stoppable(connectionStore.get().phase === 'connected', liveEntries(approvalsStore.get()).length > 0)) stopHer('tray');
          else report('TRAY stop: nothing to stop now (she is not speaking, or thinking on a message you sent), nothing sent');
          return;
        }
        report(`CLOSE refused (${command.via}): an approval card is waiting, the Orb stays open`);
        pushNote('ORB', 'Answer the approval first.', 'wait');
      }),
    [],
  );

  useEffect(() => {
    if (!bootstrap.isDev || !engine) return;
    let raf = 0;
    const onVisibility = (): void => {
      cancelAnimationFrame(raf);
      if (document.hidden) {
        report(`VIS hidden wall=${Date.now()} frames=${engine.stats().framesDrawn}`);
        return;
      }
      const t0 = performance.now();
      const f0 = engine.stats().framesDrawn;
      report(`VIS visible wall=${Date.now()} frames=${f0}`);
      const poll = (): void => {
        const f = engine.stats().framesDrawn;
        if (f > f0) {
          report(`VIS first frame drawn ${(performance.now() - t0).toFixed(1)} ms after visible, wall=${Date.now()} frames=${f}`);
          return;
        }
        if (performance.now() - t0 < 5000) raf = requestAnimationFrame(poll);
        else report('VIS no frame drawn within 5 s of visible');
      };
      raf = requestAnimationFrame(poll);
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [bootstrap.isDev, engine]);

  useEffect(() => {
    if (!engine) return;
    engine.setAnimating(true);
    if (!compact || cardUp || state !== 'idle') return;
    const id = window.setTimeout(() => {
      engine.setAnimating(false);
      report(`COMPACT idle: the sphere freezes, no animation frames t=${performance.now().toFixed(1)}`);
    }, COMPACT_SETTLE_MS);
    return () => window.clearTimeout(id);
  }, [engine, compact, cardUp, state, themeStamp, layout, connection.phase]);

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
      `LAYOUT mode=${mode} window=${layout.W}x${layout.H} slot=${layout.slot} R=${layout.R.toFixed(1)} diameter=${(2 * layout.R).toFixed(1)} ` +
        `cx=${layout.cx.toFixed(1)} cy=${layout.cy.toFixed(1)} cal=${layout.cal} dw=${layout.dw} lcW=${layout.lcW.toFixed(1)} ` +
        `colH=${layout.colH} trace=${layout.traceShown} bandX=${layout.bandX.toFixed(1)} bandW=${layout.bandW.toFixed(1)} ` +
        `narrow=${layout.narrow} guarded=${layout.guarded} stalled=${layout.stalled}` +
        `${layout.box ? ` box=${layout.box.x},${layout.box.y} ${layout.box.w}x${layout.box.h}` : ''}`,
    );
  }, [bootstrap.isDev, layout, mode]);

  useLayoutEffect(() => {
    if (cardUp && !hadCard.current) {
      const was = placeAtRender.current;
      const inQuick = was === 'quick' || focusedPlace() === 'quick';
      const paletteWasOpen = paletteStore.get().open;
      if (paletteWasOpen) {
        closePalette(report, 'card');
        focusCard.current = !inQuick;
        report('CARD closed the palette; it stays closed');
      }
      if (railStore.get() !== null) {
        const hidden = modeStore.get() !== 'full' || paletteWasOpen;
        railStore.set(null);
        focusCard.current = !inQuick;
        report(`CARD closed the ${hidden ? 'drawer hidden under the palette or the mode' : 'open drawer'}; it stays closed`);
      } else if (was === 'dock') {
        focusCard.current = true;
        report(`CARD hid the chat dock while he was typing in it; the draft is kept (${composeStore.get().draft.length} chars)`);
      }
      if (inQuick) report('CARD arrived while the quick line has focus; it keeps its focus and text and cannot answer the card');
      report(`CARD arrived mode=${modeStore.get()} hasFocus=${document.hasFocus()}`);
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
    <div className={`orb${layout.narrow ? ' narrow' : ''}${offline ? ' offline' : ''}${mode === 'full' ? '' : ` ${mode}`}${boot === 'log' ? ' boot' : ''}`}>
      {sphere}
      {compact && layout.box ? (
        <div className="cwin" style={{ left: layout.box.x, top: layout.box.y, width: layout.box.w, height: layout.box.h }}>
          <div className="ch">
            TESSA<span>F TO EXPAND</span>
          </div>
        </div>
      ) : (
        <TopBar narrow={layout.narrow} fixture={bootstrap.fixture} />
      )}
      <Chip chipRef={chipRef} />
      <CaptionLine capRef={capRef} />
      <QuickLine quickRef={quickRef} />
      {!compact && layout.colShown ? (
        <div className="col" style={{ left: MARGIN, top: layout.colTop, width: Math.round(layout.lcW), height: layout.colH }}>
          <Notes />
          {layout.traceShown ? <TraceStack /> : null}
        </div>
      ) : null}
      {compact ? null : <Band x={layout.bandX} w={layout.bandW} top={TOP + 12} h={layout.H - TOP - 24} />}
      {compact ? null : <CalendarPanel mode={layout.cal} />}
      {compact ? null : <Rails cardWaiting={cardUp} lit={drawerShown ? rail : null} onToggle={(id) => toggleRail(id, report, `click:${id}`)} />}
      {drawerShown && rail ? <Drawer key={rail} id={rail} dw={layout.dw} /> : null}
      {paletteShown ? <Palette width={layout.dw || 372} /> : null}
      <Card ref={cardRef} width={layout.dw || 372} />
      <Boot engine={engine} R={layout.R} cx={layout.cx} cy={layout.cy} cardUp={cardUp} />
    </div>
  );
}
