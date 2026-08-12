/**
 * The 48px rail. Three drawer toggles, and nothing else.
 *
 * CONTRACT §9.1 forbids emoji and "icon soup", so these are words — uppercase
 * mono at 10px with 0.14em tracking, set vertically because 48px is the token
 * width (`--rail-w`) and a horizontal word does not fit. The active item gets an
 * accent bar rather than a filled background, so the rail stays a hairline
 * against the void instead of becoming a second panel.
 */

import { drawerStore, useStore, type DrawerId } from '../state/store.ts';

const ITEMS: readonly { id: DrawerId; label: string }[] = [
  { id: 'agenda', label: 'Agenda' },
  { id: 'jobs', label: 'Jobs' },
  { id: 'transcript', label: 'Transcript' },
];

export function Rail() {
  const open = useStore(drawerStore);

  return (
    <nav className="rail" aria-label="Panels">
      {ITEMS.map((item) => {
        const active = open === item.id;
        return (
          <button
            key={item.id}
            type="button"
            className="rail__item"
            data-active={active}
            aria-pressed={active}
            // Toggle, not select: clicking the open drawer closes it, so the
            // sphere can always be given the whole stage in one click.
            onClick={() => drawerStore.set(active ? null : item.id)}
          >
            <span className="rail__label">{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
