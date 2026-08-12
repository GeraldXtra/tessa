/**
 * The collapsed layout — spec §8.1's "design the collapsed layout first".
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │ status bar                                          28px │
 *   ├────┬───────────────────────────────┬─────────────────────┤
 *   │rail│         sphere stage          │  drawer (overlay)   │
 *   │ 48 │      floats over the void     │        320          │
 *   └────┴───────────────────────────────┴─────────────────────┘
 *
 * At 1366×768 with a drawer open that is 368px of chrome and ~998px of stage.
 * The four-panel arrangement would leave 478px, which spec §8.1 calls "not a
 * centre stage — a thumbnail". The drawer is an overlay, so the stage never
 * actually shrinks; the sphere is offset inside the scene instead.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { AGENT_STATES } from '@zoey/protocol';

import type { BootstrapInfo, SphereTier } from '../shared/ipc-contract.ts';
import { tokenPx } from './design-tokens.ts';
import { Drawer } from './layout/Drawer.tsx';
import { DevOverlay } from './layout/DevOverlay.tsx';
import { Rail } from './layout/Rail.tsx';
import { StatusBar } from './layout/StatusBar.tsx';
import { AgendaPanel } from './panels/AgendaPanel.tsx';
import { JobsPanel } from './panels/JobsPanel.tsx';
import { TranscriptPanel } from './panels/TranscriptPanel.tsx';
import { DomSphere } from './scene/DomSphere.tsx';
import { Sphere } from './scene/Sphere.tsx';
import { probeSphereTier } from './scene/gpu-tier.ts';
import type { SphereStats } from './scene/sphere-engine.ts';
import {
  agentStateStore,
  connectionStore,
  drawerStore,
  tierStore,
  useStore,
  type DrawerId,
} from './state/store.ts';

const DRAWER_TITLE: Record<DrawerId, string> = {
  agenda: 'Agenda',
  jobs: 'Jobs',
  transcript: 'Transcript',
};

function panelFor(id: DrawerId) {
  switch (id) {
    case 'agenda':
      return <AgendaPanel />;
    case 'jobs':
      return <JobsPanel />;
    case 'transcript':
      return <TranscriptPanel />;
  }
}

export function App() {
  const tier = useStore(tierStore);
  const drawer = useStore(drawerStore);

  const [bootstrap, setBootstrap] = useState<BootstrapInfo | null>(null);
  const [tierReason, setTierReason] = useState('probing…');
  const [rendererName, setRendererName] = useState('probing…');
  const [readStats, setReadStats] = useState<(() => SphereStats) | null>(null);

  /* ── bootstrap: GPU tier, then the connection feed ─────────────────────── */

  useEffect(() => {
    let alive = true;

    void window.zoey.bootstrap().then((info) => {
      if (!alive) return;
      setBootstrap(info);

      const probe = probeSphereTier(info.gpu);
      tierStore.set(probe.tier);
      setTierReason(probe.reason);
      setRendererName(probe.renderer);
      console.log(`[orb] sphere tier=${probe.tier} — ${probe.reason} (${probe.renderer})`);
    });

    // Ask once for the current status so first paint is accurate, then follow
    // the push channel. Without the initial read the status bar would show
    // 'offline' until the next change, which on a healthy system might be never.
    void window.zoey.getConnection().then((status) => {
      if (alive) connectionStore.set(status);
    });
    const unsubscribe = window.zoey.onConnection((status) => connectionStore.set(status));

    return () => {
      alive = false;
      unsubscribe();
    };
  }, []);

  /* ── keyboard ──────────────────────────────────────────────────────────── */

  const isDev = bootstrap?.isDev ?? false;

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        drawerStore.set(null);
        return;
      }

      // The dev state cycler. Phase 1 subscribes to no events, so this is the
      // only way to exercise all six states — and exercising all six is the
      // deliverable, not a convenience.
      if (!isDev || !event.altKey) return;

      // `code` first (layout-independent physical key), then `key` as a
      // fallback. The fallback is not redundant: `code` is derived from the
      // hardware scancode, and synthetic input — on-screen keyboards, remote
      // desktop, accessibility tools, and the keybd_event injection used to
      // verify this build — arrives with scancode 0 and therefore no usable
      // `code`. Matching only `code` makes the shortcut silently dead for all
      // of them.
      const digit =
        /^Digit([1-6])$/.exec(event.code)?.[1] ?? (/^[1-6]$/.test(event.key) ? event.key : null);
      if (!digit) return;

      const index = Number.parseInt(digit, 10) - 1;
      const next = AGENT_STATES[index];
      if (next) {
        agentStateStore.set(next);
        event.preventDefault();
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isDev]);

  /* ── the drawer, and what it does to the sphere ────────────────────────── */

  // Keep the last panel mounted while the drawer slides shut, so the content
  // does not vanish a beat before the panel does.
  const lastDrawer = useRef<DrawerId>('agenda');
  if (drawer) lastDrawer.current = drawer;

  const drawerWidth = tokenPx('--transcript-w', 320);
  const offsetPx = drawer ? drawerWidth : 0;

  const onTierChange = useCallback((next: SphereTier, reason: string) => {
    tierStore.set(next);
    setTierReason(reason);
    console.warn(`[orb] sphere demoted to ${next}: ${reason}`);
  }, []);

  const onEngineReady = useCallback((read: () => SphereStats) => {
    // Wrapped in a thunk — React would otherwise call the function as a lazy
    // state initialiser and store its return value instead of the function.
    setReadStats(() => read);
  }, []);

  return (
    <div className="app">
      <StatusBar />

      <div className="app__body">
        <Rail />

        <main className="stage">
          {/* Nothing is drawn until bootstrap resolves and the tier is known.
              Rendering <Sphere> on the default 'med' first would create a WebGL
              context and allocate particle buffers, only to tear both down a
              frame later when the probe answers 'dom' — the exact machine where
              that answer is likeliest is the one least able to afford it. */}
          {!bootstrap ? null : tier === 'dom' ? (
            <DomSphere offsetPx={offsetPx} />
          ) : (
            <Sphere
              tier={tier}
              offsetPx={offsetPx}
              onTierChange={onTierChange}
              onEngineReady={onEngineReady}
            />
          )}
        </main>

        <Drawer
          title={DRAWER_TITLE[lastDrawer.current]}
          open={drawer !== null}
          onClose={() => drawerStore.set(null)}
        >
          {panelFor(lastDrawer.current)}
        </Drawer>
      </div>

      {isDev ? (
        <DevOverlay
          tier={tier}
          // The DOM rung has no engine; passing the disposed one's closure would
          // keep the overlay quoting frame times that stopped being measured.
          readStats={tier === 'dom' ? null : readStats}
          tierReason={tierReason}
          rendererName={rendererName}
        />
      ) : null}
    </div>
  );
}
