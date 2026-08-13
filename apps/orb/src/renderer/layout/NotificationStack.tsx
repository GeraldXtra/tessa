/**
 * §R.2 top-right: "Notification stack — dismissible, stacked, auto-fading."
 *
 * ─── unlit, on purpose ───
 * `evt.notification` is a real contract event (§4.1, `level ∈ info|warn|error`)
 * and the mechanism below is complete: arrival, stacking, dismissal, and a
 * timed auto-fade. The daemon does not emit it yet, so the store stays empty
 * and this renders NOTHING — no demo toast, no sample notification. Same
 * discipline as the SENTINEL colour rule: build the mechanism, leave it dark,
 * and let the first thing it ever shows be true.
 *
 * ─── motion budget (§R.7) ───
 * "Nothing on the rails animates continuously. The sphere is the only thing
 * that moves at rest."
 * This obeys that literally. There is no keyframe animation and no transition
 * that loops: a notification transitions once on arrival and once on dismissal,
 * both driven by a state change, and between those it is a static element. With
 * an empty stack there is not even an element in the DOM, so the at-rest cost
 * is exactly zero.
 */

import { useEffect } from 'react';

import { notificationsStore, useStore, type OrbNotification } from '../state/store.ts';

/** How long an un-dismissed notification stays before it fades itself out. */
const AUTO_FADE_MS = 8000;

function NotificationCard({ item }: { item: OrbNotification }) {
  useEffect(() => {
    // One timer per card, cleared on unmount. Not an interval — a single
    // scheduled dismissal, so nothing is running while the card sits there.
    const id = window.setTimeout(() => {
      notificationsStore.set(notificationsStore.get().filter((n) => n.id !== item.id));
    }, AUTO_FADE_MS);
    return () => window.clearTimeout(id);
  }, [item.id]);

  return (
    <li className="note" data-level={item.level}>
      <span className="note__head">
        <span className="note__level">{item.level}</span>
        <button
          type="button"
          className="note__close"
          aria-label="Dismiss"
          onClick={() =>
            notificationsStore.set(notificationsStore.get().filter((n) => n.id !== item.id))
          }
        >
          ×
        </button>
      </span>
      <span className="note__title">{item.title}</span>
      {item.body ? <span className="note__body">{item.body}</span> : null}
    </li>
  );
}

export function NotificationStack() {
  const items = useStore(notificationsStore);

  // Nothing in the DOM when there is nothing to say.
  if (items.length === 0) return null;

  return (
    <ul className="notes" aria-live="polite" aria-label="Notifications">
      {items.map((item) => (
        <NotificationCard key={item.id} item={item} />
      ))}
    </ul>
  );
}
