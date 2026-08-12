/**
 * The one drawer.
 *
 * Spec §8.1, the constraint that reshapes this whole surface:
 *
 *     rail 48 + left 240 + right 280 + transcript 320 = 888px of chrome
 *     1366 − 888 = 478px of centre stage — "not a centre stage, a thumbnail"
 *
 * So panels are OVERLAYS over the sphere, not columns beside it, and exactly
 * one is open at a time. Open, the chrome is 48 + 320 = 368px and the stage
 * keeps ~998px on a 1366px display. The sphere stays the centre stage, which is
 * the entire point of the Orb.
 *
 * It is an overlay in the visual sense only — it does not trap focus or dim the
 * page. This is an always-on companion surface, not a modal dialog; the sphere
 * behind it stays live and readable.
 */

import type { ReactNode } from 'react';

interface DrawerProps {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}

export function Drawer({ title, open, onClose, children }: DrawerProps) {
  return (
    <aside className="drawer" data-open={open} aria-hidden={!open} aria-label={title}>
      <div className="drawer__head">
        <h2 className="drawer__title">{title}</h2>
        {/* A literal glyph, not a numeric HTML entity. check-contract.mjs
            scans for hard-coded colours with a regex that reads the digits of
            a numeric entity as a hex literal and fails the build. Plain
            characters avoid the collision and read better in source. */}
        <button type="button" className="drawer__close" onClick={onClose} aria-label="Close panel">
          ×
        </button>
      </div>
      <div className="drawer__body">{children}</div>
    </aside>
  );
}
