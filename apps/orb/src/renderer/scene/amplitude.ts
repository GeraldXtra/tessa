/**
 * The voice envelope, synthesised.
 *
 * ─── what the daemon actually sends, and what it does not ───
 * The Orb is driven by `evt.agent.state` (CONTRACT §4.1): one frame per
 * transition, `speaking` set immediately before playback and `idle` after it
 * (core/voice/loop.py, `_state(AgentState.SPEAKING)` then `bus.speak(...)`).
 * `evt.voice.amplitude` is a RESERVED name in §4.3 with no payload defined and
 * no broadcast site anywhere in core/ — grep it; there are zero. So the Orb
 * knows WHEN she is talking, to within the state dwell, and does not know
 * HOW LOUD. This module stands in for the level: a deterministic, speech-shaped
 * envelope that runs while a voice state is held.
 *
 * ─── the interface is the point ───
 * The signature is `(tMs, state) => number in [0,1]`, which is exactly the shape
 * a live level would deliver. When a sender lands, the swap is: read the last
 * value off the event stream instead of calling this. Nothing in
 * sphere-engine.ts changes, and nothing in states.ts changes.
 *
 * ─── ROUND W: SHAPED LIKE SPEECH, NOT LIKE A HUM ───
 * The previous envelope was `phrase * |sin(4.6 Hz)| * texture` with a 15 s
 * phrase sine. Two faults, both visible: a 15 s phrase cycle means the sphere
 * can sit through most of a real sentence in the CLOSED part of the cycle and
 * do nothing — which is the "wasn't sure it worked" report — and a pure |sin|
 * syllable train is a metronome, so what motion there was read as a machine
 * humming rather than someone talking.
 *
 * This one is built from the statistics of speech instead:
 *   - PHRASES of 1.6–2.8 s separated by PAUSES of 0.25–1.1 s (breath groups);
 *   - SYLLABLES at ~4.4 Hz with a fast attack and slower decay;
 *   - per-syllable STRESS varying 0.55–1.0, and a short dip every 2–4
 *     syllables where a word boundary would fall;
 *   - onset and offset ramps of ~120 ms so a phrase does not click on.
 * All of it is hashed off integer phrase and syllable indices, so it is a
 * function of time alone — two runs look identical, and there is no RNG.
 *
 * `listening` runs the same generator on a different seed with his cadence
 * (a little slower, longer pauses) and a lower ceiling, so the sphere reacting
 * to HIM is visibly the quieter of the two, as the spec's tighten-and-brighten
 * asks for.
 */

import type { AgentState } from '@tessa/protocol';

function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

/** Deterministic 0..1 from an integer. The classic sin-hash; no RNG anywhere. */
function hash(n: number): number {
  const s = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return s - Math.floor(s);
}

interface Cadence {
  /** Length of one phrase-plus-pause cell, seconds. */
  cellS: number;
  /** Shortest and longest phrase inside a cell, seconds. The rest is pause. */
  phraseMinS: number;
  phraseMaxS: number;
  /** Syllable rate, Hz. */
  syllableHz: number;
  /** Output ceiling — the loudest syllable reaches exactly this. */
  ceiling: number;
  /** Hash seed so her voice and his are different sequences. */
  seed: number;
}

/** Her voice: fluent, phrases close together, full ceiling. */
const HER: Cadence = { cellS: 3.0, phraseMinS: 1.6, phraseMaxS: 2.8, syllableHz: 4.4, ceiling: 1.0, seed: 11 };
/** His voice, as heard: shorter bursts, longer gaps, quieter — it is the room. */
const HIS: Cadence = { cellS: 3.6, phraseMinS: 1.2, phraseMaxS: 2.4, syllableHz: 3.9, ceiling: 0.85, seed: 37 };

function speech(t: number, c: Cadence): number {
  const cell = Math.floor(t / c.cellS);
  const inCell = t - cell * c.cellS;
  const phraseLen = c.phraseMinS + (c.phraseMaxS - c.phraseMinS) * hash(cell * 3 + c.seed);
  if (inCell >= phraseLen) return 0;

  // Onset and offset ramps, ~120 ms each.
  const ramp = Math.min(1, inCell / 0.12, (phraseLen - inCell) / 0.12);

  // Syllables: a sawtooth-shaped pulse — fast attack, slower decay — so the
  // train reads as articulated rather than as a sine.
  const sylPos = inCell * c.syllableHz;
  const sylIndex = Math.floor(sylPos);
  const frac = sylPos - sylIndex;
  const attack = 0.22;
  const pulse = frac < attack ? frac / attack : 1 - (frac - attack) / (1 - attack);
  const shaped = Math.pow(Math.max(pulse, 0), 0.8);

  // Stress per syllable, and a word gap every 2–4 syllables.
  const key = cell * 97 + sylIndex * 7 + c.seed;
  const stress = 0.55 + 0.45 * hash(key);
  const gap = hash(key * 13 + 5) < 0.26 ? 0.18 : 1.0;

  return clamp01(ramp * shaped * stress * gap * c.ceiling);
}

export function fakeAmplitude(tMs: number, state: AgentState): number {
  const t = tMs / 1000;

  switch (state) {
    case 'speaking':
      return speech(t, HER);

    case 'listening':
      // His voice, on a different sequence. The state is only held while the
      // mic is claimed, so this runs exactly as long as he is being heard.
      return speech(t + 1000, HIS);

    // idle, thinking, working, blocked: nothing is being heard or said. Their
    // gains are 0 anyway, but returning 0 keeps the signal honest rather than
    // relying on the gain to hide it.
    default:
      return 0;
  }
}
