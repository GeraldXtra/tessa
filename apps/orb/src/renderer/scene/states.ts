/**
 * The six agent states, as sphere parameters.
 *
 * The state list is IMPORTED from @zoey/protocol, never retyped. `AgentState`
 * is a CLOSED set (CONTRACT §7.4): adding a value to it is a breaking change
 * requiring a PROTOCOL_VERSION bump and both surfaces updating together. The
 * `satisfies Record<AgentState, …>` below is what makes that real on this side —
 * if the enum gains a seventh state, this file stops compiling instead of
 * silently rendering nothing for it.
 *
 * The visual mapping is ZOEY_OS-spec §5.1 verbatim:
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

import { AGENT_STATES, type AgentState } from '@zoey/protocol';

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
    spin: 0.04,
    pointScale: 0.011,
    brightness: 0.55,
    coolMix: 0.34,
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
    amplitudeGain: 0.16,
    spin: 0.06,
    pointScale: 0.0125,
    brightness: 1.0,
    coolMix: 0.55,
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
    spin: 0.34,
    pointScale: 0.0105,
    brightness: 0.82,
    coolMix: 0.28,
    palette: 'flame',
    frozen: false,
  },

  speaking: {
    radius: 0.98,
    turbulence: 0.03,
    breathDepth: 0.02,
    breathPeriodMs: 2200,
    amplitudeGain: 0.42,
    spin: 0.08,
    pointScale: 0.012,
    brightness: 0.95,
    coolMix: 0.46,
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
    spin: 0.13,
    pointScale: 0.0115,
    brightness: 0.85,
    coolMix: 0.4,
    palette: 'flame',
    frozen: false,
  },

  blocked: {
    radius: 0.94,
    turbulence: 0.0,
    breathDepth: 0.0,
    breathPeriodMs: 1,
    amplitudeGain: 0.0,
    spin: 0.0,
    pointScale: 0.0125,
    brightness: 0.72,
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
