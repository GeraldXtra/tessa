/**
 * The bottom rung: a sphere with no WebGL at all.
 *
 * Reached when there is no WebGL2 context, when the context is lost, or via
 * `--force-tier=dom`. Spec §10 makes a DOM fallback mandatory on this hardware
 * — the HD 620's driver is on a legacy branch, and "the sphere didn't render"
 * cannot be allowed to mean "the surface is blank".
 *
 * It has to carry the same six states, because those states are the actual
 * information (CONTRACT §4.1). It does that with three concentric rings and a
 * core, animated entirely in CSS keyframes — no rAF, no JS per frame, so it
 * costs the compositor and nothing else. The state lands as a data attribute
 * and app.css does the rest.
 */

import { useStore, agentStateStore } from '../state/store.ts';

export function DomSphere({ offsetPx }: { offsetPx: number }) {
  const state = useStore(agentStateStore);

  return (
    <div
      className="dom-sphere"
      data-state={state}
      style={{ transform: `translateX(${-offsetPx * 0.5}px)` }}
      aria-hidden="true"
    >
      <span className="dom-sphere__ring dom-sphere__ring--outer" />
      <span className="dom-sphere__ring dom-sphere__ring--mid" />
      <span className="dom-sphere__ring dom-sphere__ring--inner" />
      <span className="dom-sphere__core" />
    </div>
  );
}
