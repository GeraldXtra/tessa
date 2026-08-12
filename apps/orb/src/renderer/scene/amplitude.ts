/**
 * The fake amplitude signal.
 *
 * Phase 1 has no microphone, but the sphere's `speaking` and `listening` states
 * are defined by how they react to one. This stands in for it.
 *
 * ─── the interface is the point ───
 * The signature is `(tMs, state) => number in [0,1]`, which is exactly the shape
 * `evt.voice.amplitude` will deliver (CONTRACT §4.3). When the voice layer lands
 * in Phase 2, the swap is: read the last value off the event stream instead of
 * calling this. Nothing in sphere-engine.ts changes, and nothing in states.ts
 * changes. That is the whole reason this is a separate module rather than a few
 * sine calls inlined in the render loop.
 *
 * Deterministic — a function of time only, no RNG. Two runs look identical,
 * which matters when comparing frame-time measurements between tiers.
 */

import type { AgentState } from '@zoey/protocol';

function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

/** Three detuned sines. Cheaper than real noise and visually indistinguishable here. */
function grain(t: number): number {
  return (Math.sin(t * 1.7) + Math.sin(t * 2.93 + 1.3) + Math.sin(t * 5.31 + 2.7)) / 3;
}

export function fakeAmplitude(tMs: number, state: AgentState): number {
  const t = tMs / 1000;

  switch (state) {
    case 'speaking': {
      // Two nested envelopes. The slow one opens and closes phrases so there
      // are real pauses — a continuous wobble reads as a machine humming, not
      // someone talking. The fast one is syllabic, around 4.6 Hz.
      const phrase = clamp01(Math.sin(t * 0.42) * 0.75 + 0.45);
      const syllable = Math.abs(Math.sin(t * Math.PI * 4.6));
      const texture = 0.72 + 0.28 * grain(t * 8.5);
      return clamp01(phrase * syllable * texture);
    }

    case 'listening': {
      // Room tone with the occasional swell. Low enough that the shell only
      // shimmers — `listening` is defined by tightening, not by movement.
      const base = 0.06 + 0.05 * grain(t * 2.2);
      const swell = 0.09 * clamp01(Math.sin(t * 0.31));
      return clamp01(base + swell);
    }

    // idle, thinking, working, blocked: nothing is being heard or said. Their
    // amplitudeGain is 0 anyway, but returning 0 keeps the signal honest rather
    // than relying on the gain to hide it.
    default:
      return 0;
  }
}
