/**
 * Edge detailing. Corner brackets, a hairline tick scale, an inset frame,
 * registration marks. The visual language of instrumentation.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THIS IS ORNAMENT, AND IT IS THE ONLY ORNAMENT IN THE PROJECT
 *
 * Everything else on this surface asserts something measured. This asserts
 * nothing, and that is allowed here on two conditions that are structural
 * rather than stylistic:
 *
 *   1. IT IS BELOW AA CONTRAST. ~2.2:1 against the void, derived as
 *      `color-mix(--text-muted 40%, --theme-void)`. Text that cannot be read
 *      cannot be misread as a measurement. Being under the readability
 *      threshold is not a compromise here — it is the safety mechanism.
 *   2. IT CONTAINS NO NUMERAL. Not one digit, anywhere, ever. The tick scale is
 *      unlabelled marks; the brackets are corners. A dim thing with numbers in
 *      it is a readout nobody can read, which is worse than no readout.
 *
 * If either condition is ever relaxed, this stops being ornament and becomes
 * fabricated data with a low opacity.
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * Cost: eleven absolutely-positioned elements with 1px borders, painted once.
 * No state, no timer, no re-render — it is outside every store subscription in
 * the app and React never touches it again after mount.
 */

/** Unlabelled marks down the inside of the rail. Count is arbitrary by design. */
const TICKS = 24;

export function EdgeDetail() {
  return (
    <div className="edge" aria-hidden="true">
      {/* A 1px frame inset from the window edge. §R.7 caps borders at 1px. */}
      <div className="edge__frame" />

      {/* Corner brackets at the stage corners. Two 1px rules each, not a glyph:
          a bracket character would be a font-dependent shape at a size where
          hinting decides what it looks like. */}
      <span className="edge__bracket edge__bracket--tl" />
      <span className="edge__bracket edge__bracket--tr" />
      <span className="edge__bracket edge__bracket--bl" />
      <span className="edge__bracket edge__bracket--br" />

      {/* The tick scale, inside the rail. Every fifth mark is longer, which is
          what a scale looks like — but none of them is labelled and none of
          them measures anything. */}
      <div className="edge__scale">
        {Array.from({ length: TICKS }, (_, i) => (
          <span key={i} className="edge__tick" data-major={i % 5 === 0} />
        ))}
      </div>

      {/* Registration marks where the axis meets the column. Crosshairs of the
          kind a plate would carry — they mark a junction, they do not report
          one. */}
      <span className="edge__reg edge__reg--axis" />
      <span className="edge__reg edge__reg--column" />
    </div>
  );
}
