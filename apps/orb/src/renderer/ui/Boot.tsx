import { useCallback, useEffect, useRef, useState } from 'react';

import type { ConnectionStatus } from '../../shared/ipc-contract.ts';
import type { PlasmaEngine } from '../scene/plasma-engine.ts';
import { connectionStore, useStore } from '../state/store.ts';
import { currentTheme } from '../theme.ts';
import { TOP } from './layout.ts';
import { bootStore, linkSeenStore } from './stores.ts';

export const BOOT_GROW_MS = 650;
export const BOOT_FADE_AFTER_MS = 300;
export const BOOT_FADE_MS = 730;
export const BOOT_FRAME_WAIT_MS = 2500;

const report = (line: string): void => window.tessa.reportMetrics(line);

function linkLine(s: ConnectionStatus): string {
  const detail = s.detail ? `, ${s.detail.toUpperCase().slice(0, 48)}` : '';
  switch (s.phase) {
    case 'connected':
      return s.sessionId === 'fixture' ? 'FIXTURE DATA, NO DAEMON SOCKET' : `DAEMON CONNECTED${s.daemonVersion ? ` V${s.daemonVersion}` : ''}`;
    case 'connecting':
      return `CONNECTING${detail}`;
    case 'offline':
      return `DAEMON OFFLINE${detail}`;
    case 'reconnecting':
      return `RETRYING${detail}`;
    case 'authRejected':
      return 'AUTH REJECTED';
    case 'protocolMismatch':
      return 'PROTOCOL MISMATCH';
  }
}

export function Boot({ engine, R, cx, cy, cardUp }: { engine: PlasmaEngine | null; R: number; cx: number; cy: number; cardUp: boolean }) {
  const phase = useStore(bootStore);
  const [lines, setLines] = useState<string[]>([]);
  const seen = useRef({ gpu: false, sphere: false, beat: false, link: '' });
  const at = useRef({ left: Math.max(16, cx - 150), top: Math.max(TOP + 16, cy - 80) });
  const radius = useRef(R);
  radius.current = R;
  const timers = useRef<number[]>([]);

  const add = useCallback((k: string, v: string) => {
    setLines((prev) => [...prev, v ? `${k.padEnd(9)}${v}` : k]);
    report(`BOOT t=${performance.now().toFixed(1)} ${k}${v ? ` ${v}` : ''}`);
  }, []);

  const finish = useCallback((why: string) => {
    if (bootStore.get() !== 'log') return;
    bootStore.set('fade');
    report(`BOOT fade-in starts (${why}) t=${performance.now().toFixed(1)}`);
    timers.current.push(
      window.setTimeout(() => {
        bootStore.set('done');
        report(`BOOT done t=${performance.now().toFixed(1)}`);
      }, BOOT_FADE_MS),
    );
  }, []);

  const ready = useCallback(
    (sphere: string) => {
      if (seen.current.sphere) return;
      seen.current.sphere = true;
      add('SPHERE', sphere);
      engine?.grow();
      add('READY', '');
      report(`BOOT READY wall=${Date.now()} t=${performance.now().toFixed(1)}`);
      timers.current.push(window.setTimeout(() => finish('the sphere is growing in'), BOOT_FADE_AFTER_MS));
    },
    [add, engine, finish],
  );

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      bootStore.set('off');
      report('BOOT skipped: reduced motion');
      return;
    }
    add('TESSA ORB', '');
    add('WINDOW', `${window.innerWidth} X ${window.innerHeight}`);
    add('THEME', currentTheme().toUpperCase());
    const onLink = (): void => {
      if (!linkSeenStore.get() || bootStore.get() !== 'log') return;
      const text = linkLine(connectionStore.get());
      if (text === seen.current.link) return;
      seen.current.link = text;
      add('LINK', text);
    };
    onLink();
    const offLink = connectionStore.subscribe(onLink);
    const offSeen = linkSeenStore.subscribe(onLink);
    const offBeat = window.tessa.onHealth(() => {
      if (seen.current.beat || bootStore.get() !== 'log') return;
      seen.current.beat = true;
      add('BEAT', 'FIRST HEARTBEAT');
    });
    const list = timers.current;
    return () => {
      offLink();
      offSeen();
      offBeat();
      for (const id of list) window.clearTimeout(id);
    };
  }, [add]);

  useEffect(() => {
    if (!engine || bootStore.get() !== 'log') return;
    if (!seen.current.gpu) {
      seen.current.gpu = true;
      const s = engine.stats();
      add('GPU', s.path === 'webgl' ? (s.rendererShort || s.renderer || 'WEBGL').toUpperCase() : `2D FALLBACK, ${s.reason.toUpperCase().slice(0, 48)}`);
    }
    const started = performance.now();
    let raf = 0;
    const poll = (): void => {
      if (seen.current.sphere) return;
      const s = engine.stats();
      if (s.framesDrawn > 0) {
        ready(`${s.path === 'webgl' ? 'PLASMA' : 'PLASMA 2D'}, R ${Math.round(radius.current)} PX`);
        return;
      }
      raf = requestAnimationFrame(poll);
    };
    raf = requestAnimationFrame(poll);
    const wait = window.setTimeout(() => {
      if (!seen.current.sphere) ready(document.hidden ? 'NO FRAME YET, THE WINDOW IS HIDDEN' : 'NO FRAME YET');
    }, BOOT_FRAME_WAIT_MS);
    report(`BOOT engine ready t=${started.toFixed(1)}`);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(wait);
    };
  }, [engine, add, ready]);

  useEffect(() => {
    if (!cardUp || bootStore.get() !== 'log') return;
    engine?.grow();
    finish('an approval card is waiting');
  }, [cardUp, engine, finish]);

  if (phase !== 'log' || lines.length === 0) return null;
  return (
    <pre className="bootlog" style={{ left: at.current.left, top: at.current.top }} aria-hidden="true">
      {lines.join('\n')}
    </pre>
  );
}
