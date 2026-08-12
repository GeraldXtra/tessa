/**
 * The honesty marker.
 *
 * Every panel in Phase 1 renders invented data. Without saying so on screen,
 * the first screenshot of this surface looks like a working agent with three
 * jobs queued — and that is the kind of thing that gets believed later, by
 * someone reading a screenshot rather than the plan.
 */
export function Placeholder({ note }: { note: string }) {
  return (
    <p className="placeholder">
      <span className="placeholder__tag">static</span>
      {note}
    </p>
  );
}
