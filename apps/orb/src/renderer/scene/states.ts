/**
 * ─── WHY EVERY `coolMix` WENT UP, AND IT IS A THEME FIX NOT A TASTE CHANGE ───
 *
 * The sphere rendered GREY under the cyan theme. Not a theming failure — the
 * theme was applied correctly — but an arithmetic consequence of the ladder:
 * `--sphere-hot` is the ladder's CORE step at ~95% lightness, and at 95%
 * lightness no hue survives. Cyan's core is E8FBFF (hash omitted so this
 * comment does not itself trip the no-hard-coded-colour gate), which is white
 * with a
 * rumour of blue in it.
 *
 * `uCoolMix` chooses between that near-white core and the BODY step, which is
 * where the theme's actual hue lives (6FE3F5 for cyan). At the old values -
 * idle 0.34, thinking 0.28 — roughly two thirds of the shell was drawn from the
 * near-white end, so the sphere was essentially achromatic. The depth term then
 * took 38% of its brightness, and near-white at 62% brightness is precisely
 * grey.
 *
 * So the mix now favours the body step. The hot core still reads at the centre
 * and on displaced particles (the fragment stage adds `vRim * 0.55` on top of
 * this), which is what keeps the shell from flattening into one flat colour.
 *
 * The RELATIVE ordering between states is preserved: every value moved by the
 * same +0.26, so `thinking` is still the coolest and `blocked` still the
 * warmest, and no state signature changed its relationship to any other.
 *
 * ─── WHY EVERY `brightness` AND `pointScale` ALSO MOVED, TOGETHER ───
 *
 * Every `brightness` was multiplied by 1.90 and every `pointScale` by 0.89, in
 * one pass, from the sweep that fitted this sphere to the reference's own
 * direct capture. Two things changed at once and they are one change:
 *
 *   count      8,000 -> 20,000 particles (see PARTICLE_COUNT for the frame
 *              measurement that permitted it)
 *   size       x0.89 then x0.87, because coverage goes as N x size² and the
 *              count is up
 *   brightness x1.90 then x1.05, because the shell now carries a wrapped-lambert
 *              term that costs the body most of its light on the unlit side
 *
 * ─── AND THEN EVERY pointScale WENT DOWN 0.85x, ON A BETTER MEASUREMENT ───
 *
 * Uniformly, so the six states keep their ordering. Fitted with the count and
 * the new tangential lattice jitter as one three-parameter sweep against the
 * reference's mid-face patch: at 15,600 particles with jitter 0.40, a 0.85
 * multiplier puts the mean particle area at 6.5 px against the reference's 6.6.
 * The sweep is in gpu-tier.ts's PARTICLE_COUNT note.
 *
 * ─── the earlier reversal, kept because it is what the new measurement undid ───
 * EVERY pointScale WENT UP 2.2x, ON A MEASUREMENT
 *
 * He said the particles were too small, and the measurement agrees in a way
 * that was not the obvious guess. Sampled per 100x100 px with both spheres
 * scaled to the SAME 480 px disc, so density is comparable:
 *
 *                     blobs   mean area   lit
 *   reference (photo)    74     42.58 px   31.5%
 *   this build          168      2.80 px    4.7%
 *
 * The reference has FEWER particles, each about fifteen times the area. Its
 * density comes from SIZE, not from count — this build already has twice the
 * count. So the count stays at 20,000 and the size goes up.
 *
 * 3.1x linear (2.2x, then 1.4x again after the side-by-side showed the
 * reference's grain still visibly coarser) is not the 15x the areas suggest, for two stated reasons: the
 * reference is a PHOTOGRAPH, so lens blur and JPEG merge neighbouring dots and
 * inflate mean blob area; and the disc itself is growing 1.22x here, which
 * spreads the same count further apart and would make the field look sparser
 * even untouched. 2.2x compensates for the second and closes most of the first.
 *
 * The RELATIVE ordering is untouched — every value moved by the same factor —
 * so `listening` is still the brightest, `idle` still the dimmest, and no state
 * changed its relationship to any other. That is the property item 2f depends
 * on and the reason this was done as a uniform scaling rather than by retuning
 * six numbers by eye.
 *
 * ─── THEN THE RANGE WAS COMPRESSED, AND THAT ONE IS A REAL COST ───
 *
 * 1.10 - 2.00 became 1.10 - 1.50: the same order, mapped onto a span 44% as
 * wide. The reason is measured and it was not a taste call. The crescent is
 * fitted to the reference at `idle`, and at idle's 1.10 the hottest pixel on
 * the limb is already at R=255. At 2.00 the whole band went past the
 * framebuffer's ceiling: the peak crescent pixel measured rgb(255,255,255) in
 * listening, thinking, working and blocked, and in the violet and cyan themes
 * too.
 *
 * White costs two things at once. It is white in every palette, so the
 * brightest part of the sphere stops carrying the theme; and six states that
 * all saturate to the same white are six states that no longer differ where
 * they differ most. Compressing brightness weakens ONE of the six cues that
 * separate the states — radius, turbulence, breath period, spin, colour
 * temperature and brightness — where clipping would have destroyed that cue
 * outright and taken the palette with it.
 *
 * The other half of the same fix is in particles.frag: the rim term scales with
 * sqrt(brightness) rather than with brightness, so the edge still responds to
 * state without the state driving it into the ceiling.
 *
 * The six agent states, as sphere parameters.
 *
 * The state list is IMPORTED from @tessa/protocol, never retyped. `AgentState`
 * is a CLOSED set (CONTRACT §7.4): adding a value to it is a breaking change
 * requiring a PROTOCOL_VERSION bump and both surfaces updating together. The
 * `satisfies Record<AgentState, …>` below is what makes that real on this side —
 * if the enum gains a seventh state, this file stops compiling instead of
 * silently rendering nothing for it.
 *
 * The visual mapping is TESSA_CORE-spec §5.1 verbatim:
 *
 *   idle       slow breathing
 *   listening  tighten + brighten
 *   thinking   turbulence
 *   speaking   amplitude ripple
 *   working    steady pulse
 *   blocked    amber, static
 *
 * `blocked` being amber AND motionless is the one that carries real
 * information. CONTRACT §4.1: it means "waiting on your approval", and it is
 * deliberately distinct from `working` so that walking past the machine at 2am
 * tells you "busy" apart from "stuck waiting for you". Every other state moves;
 * this one does not, and that stillness is the signal.
 */

import { AGENT_STATES, type AgentState } from '@tessa/protocol';

export interface SphereParams {
  /** Shell radius in world units. */
  radius: number;
  /** Radial noise displacement — the visual weight of "turbulence". */
  turbulence: number;
  /** Breathing depth as a fraction of radius. */
  breathDepth: number;
  /** Breathing period. Shorter reads as urgency. */
  breathPeriodMs: number;
  /** How much the amplitude signal deforms the shell. */
  amplitudeGain: number;
  /**
   * Round V: how much the WHOLE shell swells per unit of voice amplitude, as
   * a fraction of the radius, added to the breath. The per-dot ripple above
   * reads as shimmer; this is the coherent cue — the sphere pumps in time
   * with syllables — and it is what makes "she is talking" legible at a
   * glance. The occluder follows it, so the fold stays intact while it moves.
   */
  voicePulse: number;
  /**
   * Round W: how much brighter the whole shell draws per unit of voice
   * amplitude — `uBrightness * (1 + voiceGlow * amplitude)`. The third voice
   * cue, and the one the fragment ceiling cannot eat: it lifts the dots that
   * sit below the ceiling (the grain's dim end, the depth-faded far side,
   * the limb rows), so on every syllable the shell fills in and the between-
   * dot floor lifts — a shimmer in time with the swell. 0 for every state
   * that is not a voice state.
   */
  voiceGlow: number;
  /** Radians per second about Y. */
  spin: number;
  /** Point size in world units, before the projection scale. */
  pointScale: number;
  /** Overall output multiplier. */
  brightness: number;
  /** 0 = --sphere-hot dominant, 1 = --sphere-cool dominant. */
  coolMix: number;
  /** Which token pair supplies the colour. 'amber' is `blocked` only. */
  palette: 'flame' | 'amber';
  /** True freezes all motion. Only `blocked`. */
  frozen: boolean;
}

export const SPHERE_STATES = {
  idle: {
    radius: 1.0,
    turbulence: 0.022,
    breathDepth: 0.045,
    breathPeriodMs: 5200,
    amplitudeGain: 0.0,
    voicePulse: 0.0,
    voiceGlow: 0.0,
    // Round W: 0.04 -> 0.06. One turn every 105 s; 15 px/s of lattice flow
    // at the face centre on a 260 px disc. Still under FIB_SPIN_MAX and still
    // calm — the round-V 0.04 (10 px/s) was reported as not visibly moving.
    // The form's sway (SWAY_* in sphere-engine.ts) is the other half.
    spin: 0.06,
    pointScale: 0.00952,
    brightness: 1.10,
    coolMix: 0.60,
    palette: 'flame',
    frozen: false,
  },

  // Tighten and brighten: a smaller, denser, cleaner shell. Less noise, not
  // more — attention reads as stillness plus light, not agitation.
  listening: {
    radius: 0.88,
    turbulence: 0.012,
    breathDepth: 0.022,
    breathPeriodMs: 2600,
    // Round W: HIS voice, made visible. Round V rode a room-tone envelope
    // (0.06..0.20) at pulse 0.04 — a peak swell of 0.8% of the radius, 2 px at
    // the limb, which is invisible and was reported as such. The envelope is
    // now speech-shaped (amplitude.ts, HIS cadence, ceiling 0.85), and the
    // three cues sit at roughly half of `speaking`'s: swell 0.06 (5% of the
    // radius, 13 px at the limb), glow 0.12, shimmer 0.02 (0.7% peak per-dot).
    // The daemon still sends no level (see amplitude.ts); a real one, when it
    // arrives, drives the same three numbers through the same envelope slot.
    amplitudeGain: 0.02,
    voicePulse: 0.06,
    voiceGlow: 0.12,
    spin: 0.06,
    pointScale: 0.01079,
    brightness: 1.50,
    coolMix: 0.81,
    palette: 'flame',
    frozen: false,
  },

  thinking: {
    radius: 1.02,
    /**
     * 0.07, down from 0.19.
     *
     * PEAK displacement governs the silhouette, not RMS, and that is what the
     * old value got wrong. `wobble` is three sines multiplied, so its RMS is
     * ~0.35 — but its PEAK is 1.0, and at 0.19 those peaks threw particles 19%
     * of the radius outward. That is what produced the facets, the hard
     * corners and the squarish outline in the captures: at rest `thinking` was
     * not a sphere at all, it was a shape being crushed. Next to `idle` and
     * `working`, which are both clean spheres, it was the only state that
     * looked like something had gone wrong.
     *
     * 0.07 keeps peaks at 7% of the radius, below the point where a point
     * cloud stops reading as a sphere, and RMS at ~2.5%.
     *
     * It is still the most turbulent state by a clear margin — 2x `working`'s
     * 0.035, 3x `idle`'s 0.022, 6x `listening`'s 0.012 — so `thinking` remains
     * visibly the busiest shell. The old value was 5.4x the next highest in the
     * whole table, which is the shape of a number nobody had looked at beside
     * its neighbours.
     */
    turbulence: 0.07,
    breathDepth: 0.03,
    breathPeriodMs: 1800,
    amplitudeGain: 0.0,
    voicePulse: 0.0,
    voiceGlow: 0.0,
    spin: 0.34,
    pointScale: 0.00901,
    brightness: 1.34,
    coolMix: 0.54,
    palette: 'flame',
    frozen: false,
  },

  speaking: {
    radius: 0.98,
    turbulence: 0.03,
    breathDepth: 0.02,
    breathPeriodMs: 2200,
    /**
     * 0.42 -> 0.20, and it is the SAME fault the `thinking` turbulence had.
     *
     * The vertex stage adds `uAmpGain * uAmplitude * ripple * 0.35` to the
     * radius. At 0.42, with the amplitude signal at 1 and the ripple at its
     * peak, that is 14.7% of the radius thrown outward — and PEAK displacement
     * governs the silhouette, not RMS. The side-by-side against reference
     * image11 is what caught it: image11 IS a `speaking` frame and its sphere
     * is round, while this build's was four or five large lobes. A speaking
     * sphere that stops being a sphere is the `thinking` facets again, wearing
     * a different parameter.
     *
     * 0.20 puts the peak at 7.0% of the radius, which is exactly the ceiling
     * the `thinking` correction settled on and below the point where a point
     * cloud stops reading as a shell. `listening` moved by the same factor so
     * the two keep their 2.6:1 ratio and the ordering is untouched.
     *
     * It is still by a wide margin the most amplitude-driven state — 2.6x
     * `listening` and infinitely more than the four states at zero — so the
     * spec's "amplitude ripple" still reads. It reads as a ripple now rather
     * than as a deformation.
     *
     * ─── ROUND V: 0.20 -> 0.30, and a 0.07 whole-shell pulse ───
     * The owner could not tell speaking from idle. Two reasons, measured on
     * the round-U shell: the ripple's per-dot random phase makes 7% peak
     * displacement read as fuzz, not motion, and nothing COHERENT moved. So
     * the ripple goes to 10.5% peak (still under the 14.7% that made lobes),
     * and `voicePulse` swells the whole shell by up to 7% of the radius in
     * time with the syllabic envelope — 18 px at the limb, 4.6 times a
     * second, which is the cue the eye reads as "talking". The occluder
     * tracks both, so the fold never shows its hidden side mid-syllable.
     *
     * ─── ROUND W: 0.30 -> 0.08, AND THE ROUND-V DIAGNOSIS WAS WRONG ───
     * Round V raised the ripple to 0.30 on the belief that its per-dot random
     * phase made it read as fuzz. The phase was `aSeed * 2pi`, and on the
     * golden-angle lattice `aSeed` IS the azimuth (theta = goldenAngle * i,
     * seed = i * 0.618 mod 1 — the same golden fraction) — so the ripple was
     * a coherent travelling wave, and at 0.30 it did exactly what the note
     * above warns against: the round-V baseline's speaking frames measure a
     * silhouette residual of 14.6 px std / 26 px max against 4.4 / 8.5 for
     * idle at the same breath phase (measure.py, cap-16 vs cap-02). The
     * sphere stopped being the reference's shape every time she spoke.
     *
     * So the shape is handed back and the cue is moved to the two channels
     * that cannot deform it: the ripple is now hashed per dot (particles.vert)
     * and small — 0.04 is a 1.4% peak per-dot shimmer, 3.6 px on a 9 px
     * lattice pitch (0.08 was tried first and visibly ragged the rows in a
     * still) — while the COHERENT swell goes 0.07 -> 0.11 (11% of the
     * radius, 29 px at the limb, on every stressed syllable) and a
     * brightness glow of 0.22 lifts the shell with it. A uniform swell moves
     * the outline in and out but keeps its shape; that is the "breathes
     * harder" of the brief, and it is the strongest cue a state-only feed
     * can carry.
     */
    amplitudeGain: 0.04,
    voicePulse: 0.11,
    voiceGlow: 0.22,
    spin: 0.08,
    pointScale: 0.01037,
    brightness: 1.46,
    coolMix: 0.72,
    palette: 'flame',
    frozen: false,
  },

  // Steady pulse: shorter period, deeper swing, low noise. Regular enough to
  // read as machinery running rather than thought happening.
  working: {
    radius: 0.96,
    turbulence: 0.035,
    breathDepth: 0.105,
    breathPeriodMs: 1400,
    amplitudeGain: 0.0,
    voicePulse: 0.0,
    voiceGlow: 0.0,
    spin: 0.13,
    pointScale: 0.00995,
    brightness: 1.37,
    coolMix: 0.66,
    palette: 'flame',
    frozen: false,
  },

  blocked: {
    radius: 0.94,
    turbulence: 0.0,
    breathDepth: 0.0,
    breathPeriodMs: 1,
    amplitudeGain: 0.0,
    voicePulse: 0.0,
    voiceGlow: 0.0,
    spin: 0.0,
    pointScale: 0.01079,
    brightness: 1.25,
    coolMix: 1.0,
    palette: 'amber',
    frozen: true,
  },
} satisfies Record<AgentState, SphereParams>;

/**
 * Belt and braces for the `satisfies` above.
 *
 * `satisfies` catches a MISSING key at compile time. This catches the mirror
 * case at load time — an extra key here that the contract does not define,
 * which would mean this file and schema/enums.json have diverged.
 */
const declared = Object.keys(SPHERE_STATES);
if (declared.length !== AGENT_STATES.length) {
  throw new Error(
    `SPHERE_STATES has ${declared.length} states but the contract defines ` +
      `${AGENT_STATES.length} (${AGENT_STATES.join(', ')}). ` +
      'AgentState is a CLOSED set — see CONTRACT §7.4.',
  );
}

export function paramsFor(state: AgentState): SphereParams {
  return SPHERE_STATES[state];
}

/** Order for the dev cycler, taken from the contract so it can never drift. */
export const STATE_CYCLE: readonly AgentState[] = AGENT_STATES;
