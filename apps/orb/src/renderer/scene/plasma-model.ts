import type { AgentState } from '@tessa/protocol';

export interface StateParams {
  energy: number;
  scale: number;
  spin: number;
  churn: number;
  detail: number;
  rim: number;
  core: number;
  breath: number;
  tight: number;
  spark: number;
  sweep: number;
  hollow: number;
  voiceGain: number;
}

export const PARAM_KEYS = [
  'energy',
  'scale',
  'spin',
  'churn',
  'detail',
  'rim',
  'core',
  'breath',
  'tight',
  'spark',
  'sweep',
  'hollow',
  'voiceGain',
] as const satisfies readonly (keyof StateParams)[];

export const STATE_PARAMS: Readonly<Record<AgentState, Readonly<StateParams>>> = {
  idle: { energy: 0.8, scale: 1.0, spin: 0.16, churn: 0.2, detail: 0.3, rim: 0.6, core: 0.7, breath: 1.0, tight: 0.0, spark: 0, sweep: 0, hollow: 0, voiceGain: 0 },
  listening: { energy: 1.14, scale: 0.935, spin: 0.26, churn: 0.36, detail: 0.42, rim: 1.05, core: 1.0, breath: 0.25, tight: 1.0, spark: 0, sweep: 0, hollow: 0, voiceGain: 0 },
  thinking: { energy: 0.95, scale: 0.975, spin: 0.85, churn: 1.7, detail: 1.0, rim: 0.7, core: 0.85, breath: 0.0, tight: 0.25, spark: 1, sweep: 0, hollow: 0, voiceGain: 0 },
  speaking: { energy: 0.62, scale: 0.985, spin: 0.3, churn: 0.42, detail: 0.55, rim: 0.65, core: 0.75, breath: 0.0, tight: 0.0, spark: 0, sweep: 0, hollow: 0, voiceGain: 1 },
  working: { energy: 0.88, scale: 1.0, spin: 0.45, churn: 0.3, detail: 0.5, rim: 0.65, core: 0.8, breath: 0.0, tight: 0.0, spark: 0, sweep: 1, hollow: 0, voiceGain: 0 },
  blocked: { energy: 0.8, scale: 1.0, spin: 0.0, churn: 0.0, detail: 0.35, rim: 1.25, core: 0.2, breath: 0.0, tight: 0.0, spark: 0, sweep: 0, hollow: 1, voiceGain: 0 },
};

export const STATE_ORDER: readonly AgentState[] = ['idle', 'listening', 'thinking', 'speaking', 'working', 'blocked'];

export const TAU_DEFAULT_S = 0.11;
export const TAU_BLOCKED_S = 0.06;
export const TAU_WAKE_S = 0.045;
export const TAU_INTERRUPT_S = 0.024;

export function transitionTau(prev: AgentState, next: AgentState): number {
  if (next === 'blocked') return TAU_BLOCKED_S;
  if (next === 'listening' && prev === 'idle') return TAU_WAKE_S;
  if (next === 'listening' && prev === 'speaking') return TAU_INTERRUPT_S;
  return TAU_DEFAULT_S;
}

export const CHANGE_JUMP = 0.3;
export const SETTLE_FRACTION = 0.95;
export const TICK_TAU_S = 0.085;
export const PALETTE_TAU_S = 0.13;
export const THEME_JUMP = 0.35;
export const THEME_TICK = 0.6;
export const DIM_TAU_S = 0.07;
export const MUTED_TAU_S = 0.06;
export const NIGHT_TAU_S = 0.1;
export const LOAD_TAU_S = 0.25;
export const CPU_TAU_S = 0.7;
export const MEM_TAU_S = 2.0;
export const LOAD_CPU_WEIGHT = 0.62;
export const LOAD_MEM_WEIGHT = 0.38;
export const VOICE_ATTACK_TAU_S = 0.022;
export const VOICE_RELEASE_TAU_S = 0.085;
export const VOICE_STALE_S = 0.25;
export const NIGHT_VOICE_CUT = 0.45;
export const RIBBON_IN_TAU_S = 0.07;
export const RIBBON_OUT_TAU_S = 0.16;
export const LAYOUT_TAU_S = 0.12;

export const BREATH_PERIOD_S = 5.2;
export const BREATH_SIZE = 0.022;
export const BREATH_ENERGY = 0.1;
export const SWEEP_PERIOD_S = 1.25;
export const VOICE_SIZE = 0.075;
export const VOICE_ENERGY = 0.85;
export const VOICE_RIM = 0.5;
export const HEART_SIZE = 0.012;
export const HEART_RIM = 0.35;
export const DIM_SPEED = 0.7;
export const SURFACE_TILT_A = 0.38;
export const SURFACE_TILT_B = -0.55;
export const SURFACE_ROLL_B = 0.4;
export const SURFACE_SPIN_B = 0.55;
export const IDLE_FLOW = 0.06;

export const NOISE_PERIOD = 867;
export const FLOW_RATE_A = 0.45;
export const FLOW_RATE_B = 0.4;
export const FLOW_RATE_C = 0.3;
export const FLOW_RATE_D = 0.55 * 0.6;
export const SPARK_RATE_X = 1.9;
export const SPARK_RATE_Y = -1.3;
export const SPARK_RATE_Z = 1.0;
export const TIGHT_FREQ = 6.5;
export const VOICE_FREQ = 9;

export const FLARE_ALERT = 0.12;
export const FLARE_WHITE_RED = 1;
export const FLARE_WHITE_OTHER = 0.35;
export const HEART_WINDOW_S = 0.95;
export const FLARE_WINDOW_S = 1.9;
export const BEAT_STALE_MS = 15_000;

export const ORB_QUAD = 1.45;
export const OVERLAY_QUAD = 1.8;

export const MAX_RINGS = 4;
export const RING_SLOTS: readonly { r: number; phi: number; c: number }[] = [
  { r: 1.3, phi: 0.42, c: 0.38 },
  { r: 1.45, phi: 0.3, c: -0.62 },
  { r: 1.6, phi: 0.4, c: 0.85 },
  { r: 1.22, phi: 0.26, c: -1.28 },
];
export const RING_FADE_IN_TAU_S = 0.08;
export const RING_DONE_HOLD_S = 0.5;
export const RING_DONE_TAU_S = 0.16;
export const RING_CANCEL_TAU_S = 0.1;
export const RING_REMOVE_S = 1.6;
export const RING_AMBER_TAU_S = 0.06;
export const RING_PROGRESS_TAU_S = 0.25;
export const RING_HEAD_BASE = 0.7;
export const RING_HEAD_STEP = 0.22;

export const RIBBON_TOP_R = 1.24;
export const RIBBON_HEIGHT_R = 0.38;
export const RIBBON_WIDTH_R = 2.4;
export const RIBBON_SAMPLES = 112;
export const RIBBON_RATE_HZ = 60;

export const TAU = Math.PI * 2;

export function approach(current: number, target: number, dt: number, tau: number): number {
  return target + (current - target) * Math.exp(-dt / Math.max(tau, 1e-4));
}

export function wrap(value: number, period: number): number {
  return value - Math.floor(value / period) * period;
}

function attackDecay(x: number, attack: number, decay: number): number {
  if (x < 0) return 0;
  if (x < attack) return x / attack;
  return Math.exp(-(x - attack) / decay);
}

export function heartEnvelope(t: number): number {
  if (t < 0 || t > HEART_WINDOW_S) return 0;
  return Math.min(1, attackDecay(t, 0.04, 0.13) + 0.7 * attackDecay(t - 0.24, 0.04, 0.13));
}

export function flareEnvelope(t: number): number {
  if (t < 0 || t > FLARE_WINDOW_S) return 0;
  return Math.min(1.2, attackDecay(t, 0.03, 0.16) + 0.9 * attackDecay(t - 0.36, 0.03, 0.42));
}
