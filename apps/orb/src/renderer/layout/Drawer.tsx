/**
 * The drawer. §R.7, "Drawer".
 *
 * 320px, full height, and — the part that changed from the previous build —
 * immediately RIGHT OF THE RAIL rather than docked to the far right of the
 * window. §R.7 is explicit: "320px wide, full height, right of the rail",
 * "1px --panel-border on the inner edge only", "--panel-radius 12px on the
 * outer corners, square against the rail". With the drawer sitting against the
 * rail, its left edge is the square one and its right edge — the one facing the
 * sphere — carries both the border and the rounded corners.
 *
 * That in turn flips which way the sphere moves: it now shifts RIGHT to stay
 * centred in what is left of the stage, where before it shifted left.
 *
 * Slide timing, curve, blur and the reduced-motion snap live in app.css and are
 * unchanged from the values already measured clean (§R.7: 180ms
 * cubic-bezier(.2,.8,.2,1), backdrop-filter blur(12px), slides never fades).
 *
 * It is a visual overlay only — it does not trap focus or dim the page. This is
 * an always-on companion surface, not a modal.
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
        {/* A literal glyph, not a numeric HTML entity: check-contract.mjs reads
            the digits of an entity as a hex colour and fails the build. */}
        <button type="button" className="drawer__close" onClick={onClose} aria-label="Close rail">
          ×
        </button>
      </div>
      <div className="drawer__body">{children}</div>
    </aside>
  );
}
