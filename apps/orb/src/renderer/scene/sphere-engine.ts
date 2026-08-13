/**
 * The particle sphere. Imperative, outside React, one draw call.
 *
 * ─── why this is not a component ───
 * React never renders this. The engine owns a canvas, a rAF loop, and a handful
 * of uniforms; it reads agent state through a getter each frame. Spec §10: two
 * physical cores, shared with the daemon. Driving 8,000 particles through a
 * reconciler at 30 Hz would spend one of them on bookkeeping.
 *
 * ─── the frame budget ───
 * Capped at 30 fps while focused, 10 fps while visible-but-unfocused, and the
 * loop stops entirely when the window is hidden. CONTRACT §4 asks for a state
 * change to be visible within 80 ms p95; one frame at 30 fps is 33 ms, and
 * parameter smoothing starts moving on the very next frame, so the cap costs
 * nothing against that target while leaving the CPU to the daemon.
 *
 * A frozen state (`blocked`) skips the draw entirely once it has settled. The
 * one state that means "I am waiting for you" costs no GPU at all.
 *
 * ─── the governor ───
 * Demotes on sustained frame overrun; never promotes. An oscillating tier looks
 * worse than a slightly conservative one, and a wrong guess upward is taken out
 * of the daemon's share of a 2-core part.
 */

import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  PerspectiveCamera,
  Points,
  Scene,
  ShaderMaterial,
  WebGLRenderer,
} from 'three';

import type { AgentState } from '@zoey/protocol';

import type { SphereTier } from '../../shared/ipc-contract.ts';
import { tokenValue } from '../design-tokens.ts';
import { fakeAmplitude } from './amplitude.ts';
import { PARTICLE_COUNT } from './gpu-tier.ts';
import { paramsFor, type SphereParams } from './states.ts';

import vertexShader from './shaders/particles.vert.glsl?raw';
import fragmentShader from './shaders/particles.frag.glsl?raw';

const FOV_DEGREES = 42;
const CAMERA_Z = 3.2;

const FPS_FOCUSED = 30;
const FPS_BACKGROUND = 10;

/**
 * Assumed refresh until the first window measures the real one. 60 Hz.
 */
const INITIAL_RAF_ESTIMATE_MS = 1000 / 60;

/** Samples per published window. 120 at 30 fps ≈ 4 s of evidence. */
const GOVERNOR_WINDOW = 120;

/**
 * The governor's budget, in milliseconds of OUR OWN work per frame.
 *
 * This is deliberately a cost budget, not an interval budget. The previous
 * version judged the interval between rendered frames and was wrong in a way
 * that mattered: that interval is produced by the pacer and is bounded below by
 * the frame target, so it could never report a healthy frame and it demoted the
 * tier for conditions the tier cannot influence.
 *
 * 12 ms of a 33 ms frame leaves the compositor two thirds of the budget. If our
 * own step() exceeds that at p95, fewer particles genuinely helps. If it does
 * not, nothing about the tier is the problem.
 */
const COST_BUDGET_MS = 12;

/** Two consecutive bad windows, not one — a single GC pause is not a verdict. */
const BREACHES_BEFORE_DEMOTION = 2;

/** A published window older than this is stale and must be shown as such. */
export const STATS_STALE_AFTER_MS = 6_000;

/**
 * Width of the centre column the pulse probe reads, in buffer pixels.
 *
 * The heartbeat band travels in LATITUDE — down the screen from the equator to
 * both poles — so a probe has to see the sphere's full vertical extent or it
 * only catches the instant the band crosses its strip. Full height is therefore
 * mandatory; full width is not, and a column is ~5× cheaper to read back, which
 * is what makes a 60 ms sampling cadence affordable on an HD 620.
 */
const PROBE_COLUMN_PX = 240;

/**
 * Luminance below this is treated as unlit. Low on purpose: the weight IS the
 * luminance, so a near-black pixel contributes near-nothing to the centroid
 * either way and the threshold is only there to skip the arithmetic on the
 * transparent majority of the buffer.
 */
const PROBE_THRESHOLD = 8;

/**
 * The governor manages PARTICLE COUNT. It deliberately stops at 'low' and never
 * demotes to 'dom'.
 *
 * Abandoning WebGL is a different kind of decision, and it belongs to evidence
 * that WebGL itself is unavailable: no context at the probe, or a lost context
 * at runtime. Frame time does not justify it — measurement on this machine
 * showed frame cost barely moves between 8,000 and 3,000 particles, because
 * Chromium composites this window in software (`gpu_compositing:
 * disabled_software`) and that cost is fixed. Demoting further would drop the
 * particle sphere for a fallback that is not measurably faster.
 */
const DEMOTION: Partial<Record<SphereTier, SphereTier>> = {
  high: 'med',
  med: 'low',
};

/** One metric's percentiles over the last published window. */
export interface Percentiles {
  p50: number;
  p95: number;
}

/**
 * Three separate measurements, because conflating them is what produced a
 * number nobody could act on.
 *
 *   cost     what WE spend. The only thing the tier can change, and the only
 *            thing the governor is allowed to judge.
 *   raf      how often the browser hands us a frame at all, sampled before any
 *            pacing. This is the compositor's ceiling, not our cost.
 *   present  the gap between frames we actually drew. Cadence as seen on
 *            screen. Bounded below by the frame target by construction, so it
 *            is a pacing readout and never a verdict.
 */
export interface SphereStats {
  tier: SphereTier;
  particles: number;
  cost: Percentiles;
  raf: Percentiles;
  present: Percentiles;
  /** Effective frames per second, derived from the presented cadence. */
  fps: number;
  /** performance.now() when this window was published. */
  publishedAt: number;
  /**
   * Whether the window was collected while the window had focus. An
   * unfocused window is paced to 10 fps ON PURPOSE, so its numbers must never
   * be read as a performance result.
   */
  focused: boolean;
  /** Frames measured in the window. */
  samples: number;
  /**
   * The canvas geometry the last resize() actually applied.
   *
   * Present because a measured 12px clip at the bottom of the sphere could not
   * be attributed without knowing whether the CSS box, the drawing buffer, or
   * the projection was the one out of step.
   */
  canvas: { cssW: number; cssH: number; bufW: number; bufH: number };
}

/**
 * One read-back of the drawing buffer, reduced to numbers. DEV ONLY.
 *
 * ─── why this exists ───
 * Every geometric claim about the sphere so far was measured by screenshotting
 * the window with GDI and analysing the PNG. That instrument failed twice, in
 * opposite directions: five captures of a motionless sphere came back spread
 * over 39 px (torn frames — the BitBlt racing the compositor), and twenty
 * captures during a live animation came back byte-identical (a stale region
 * that DWM never repainted). Both are properties of screen capture, not of the
 * sphere, and no amount of masking or averaging fixes either.
 *
 * Reading `gl.readPixels` from inside the renderer removes the whole class:
 *
 *   • No compositor. The pixels come from the drawing buffer immediately after
 *     the draw call that produced them, in the same JS task, before anything
 *     can present or tear them.
 *   • No occlusion, no focus, no window title lookup, no DPI, no chrome.
 *   • NO EXCLUSION MASK. The dev overlay is `position: fixed` DOM and the
 *     status bar and rail are siblings of the canvas — none of them exist in
 *     the drawing buffer. The symmetric-mask bias that made dx move when only
 *     the window HEIGHT changed cannot occur, because there is nothing to mask.
 *   • The canvas is `inset: 0` in `.stage`, so the buffer IS the stage. "Is the
 *     sphere centred in its stage" is answered directly rather than inferred
 *     from a screen rectangle that has to be reconstructed from window metrics.
 */
export interface ProbeReading {
  /** Drawing-buffer size, and the CSS box it is meant to match. */
  bufW: number;
  bufH: number;
  cssW: number;
  cssH: number;
  /** Horizontal extent actually read back, in buffer pixels. */
  x0: number;
  x1: number;
  /**
   * Brightness-weighted centroid, in CSS orientation (y down).
   *
   * The first moment, not a bounding box: it is invariant to a global change in
   * brightness, which a box is not — on an additive falloff a box creeps
   * outward as the shell brightens.
   */
  cx: number;
  cy: number;
  /**
   * Offset from the buffer's geometric centre. Expected 0 on both axes.
   *
   * Compared against `(buf - 1) / 2`, not `buf / 2`: the viewport maps NDC zero
   * to the boundary between the two middle pixels, which in pixel INDICES is
   * `(n - 1) / 2`. Using `n / 2` would bake in a half-pixel bias.
   */
  dx: number;
  dy: number;
  /** Total luminance over the region — the pulse's signal. */
  sum: number;
  /** Pixels above threshold. */
  lit: number;
  /** The pulse uniform at the instant of the read. Ground truth for §R.1. */
  uPulse: number;
  /**
   * What last brought the drawing buffer into step with the CSS box —
   * `observer`, `frame`, `probe`, `init` or `reprobe`.
   *
   * Carried because the renderer's `console` does not reach the process log in
   * a preview build (that is what `IPC.devMetrics` is for), and "did the
   * ResizeObserver deliver, or did the frame-loop guard have to catch it"
   * is not a question that should be answered by inference.
   */
  resizeReason: string;
}

export interface SphereEngineOptions {
  canvas: HTMLCanvasElement;
  initialTier: SphereTier;
  /** Read each frame. Never a subscription — no React involvement. */
  getState: () => AgentState;
  /** Fired when the governor or a context loss changes the tier. */
  onTierChange: (tier: SphereTier, reason: string) => void;
  /**
   * Fired on the first frame DRAWN with a new agent state, with the
   * `performance.now()` at which that frame's draw call was submitted.
   *
   * This is the surface half of spec §4's "sphere state change → visible:
   * p95 80 ms". Measured here rather than in React because React is not in the
   * animation path at all — the engine reads the store directly every frame, so
   * the only place that knows when a state first reached the screen is the
   * frame that put it there.
   */
  onStateRendered?: (state: AgentState, at: number) => void;
}

export interface SphereEngine {
  setTier(tier: SphereTier): void;
  /** Shift the sphere left when a drawer opens. Pixels, animated internally. */
  setCentreOffsetPx(pixels: number): void;
  /**
   * Discard the measured refresh rate and re-derive the frame divider.
   *
   * Called when the display layout changes. Without this a move to a panel with
   * a different refresh keeps dividing by the old number and silently paces to
   * the wrong frame rate.
   */
  reprobeRefresh(): void;
  /**
   * §R.1 — fire one equatorial pulse. Called on each `evt.daemon.health`.
   *
   * Driven by arrivals, never by a timer: if the beats stop, nothing calls
   * this, the in-flight pulse completes its travel and the equator goes still.
   * A self-running animation would keep pulsing a dead daemon, which is the
   * exact failure this instrument exists to make visible.
   */
  beat(): void;
  /**
   * §R.1 colour temperature — "cool at rest → hot under load".
   *
   * `load` is 0..1, normalised by the caller. This is ZOEY's exertion, not the
   * machine's: §R.1 lists the machine's CPU and RAM separately as the P6
   * "resource aura". Feeding machine load in here would collapse two distinct
   * instruments into one and make the sphere claim Zoey is busy when it is
   * something else on the box that is.
   */
  setLoad(load: number): void;
  stats(): SphereStats;
  /**
   * DEV ONLY — redraw the current state and read the buffer back. See
   * `ProbeReading`.
   *
   * `'full'` reads the whole buffer, for geometry. `'column'` reads a centred
   * full-height strip, for the pulse.
   *
   * This is deliberately NOT a passive sampler of whatever the loop last drew.
   * It calls `step(0)`, and a zero delta is a no-op for every piece of animated
   * state in this engine — every `approach()` gets rate `1 - e^0 = 0`, and
   * every clock advances by `deltaMs`. So the probe re-renders exactly what is
   * on screen without becoming part of the animation it is measuring, and two
   * probes with no frame between them are identical by construction.
   */
  probeFrame(mode: 'full' | 'column'): ProbeReading | null;
  dispose(): void;
}

/* ────────────────────────────────────────────────────────────────── helpers */

/**
 * Read a design token off the document.
 *
 * CONTRACT §9 forbids a hard-coded hex anywhere in surface code, and that has
 * to include the shader uniforms. The generated custom properties are the
 * single source, so retuning packages/tokens/tokens.json retints the sphere
 * with no code change. The fallback is a numeric Color, not a literal.
 */
function tokenColor(property: string): Color {
  const raw = tokenValue(property);
  const color = new Color(1, 1, 1);
  if (raw) {
    try {
      color.set(raw);
    } catch {
      // A malformed token should dim the sphere, never crash the surface.
    }
  }
  return color;
}

/**
 * Fibonacci lattice — even coverage without the pole clustering of a naive
 * lat/long grid, which on a particle shell shows up as two bright caps.
 */
function buildGeometry(count: number): BufferGeometry {
  const positions = new Float32Array(count * 3);
  const seeds = new Float32Array(count);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const denominator = Math.max(count - 1, 1);

  for (let i = 0; i < count; i++) {
    const y = 1 - (i / denominator) * 2;
    const ring = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = goldenAngle * i;

    positions[i * 3] = Math.cos(theta) * ring;
    positions[i * 3 + 1] = y;
    positions[i * 3 + 2] = Math.sin(theta) * ring;

    // Golden-ratio stride: decorrelated per particle, deterministic per index.
    seeds[i] = (i * 0.618033988749895) % 1;
  }

  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(positions, 3));
  geometry.setAttribute('aSeed', new BufferAttribute(seeds, 1));
  return geometry;
}

function percentile(sorted: readonly number[], fraction: number): number {
  if (sorted.length === 0) return 0;
  const index = Math.min(sorted.length - 1, Math.floor(sorted.length * fraction));
  return sorted[index] ?? 0;
}

function approach(current: number, target: number, rate: number): number {
  return current + (target - current) * rate;
}

/* ─────────────────────────────────────────────────────────────────── engine */

export function createSphereEngine(options: SphereEngineOptions): SphereEngine {
  const { canvas, getState, onTierChange } = options;

  let tier: SphereTier = options.initialTier;
  let disposed = false;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const renderer = new WebGLRenderer({
    canvas,
    // The page paints --bg-void and an --bg-ambient radial behind this canvas.
    // An opaque clear would cover them, so the sphere composites over the CSS.
    alpha: true,
    antialias: false,
    depth: false,
    stencil: false,
    powerPreference: 'low-power',
  });
  // Clamped to 1 deliberately. The display is 1366×768 at DPR 1; letting a
  // future scaled display quadruple the fragment count is not a trade the HD
  // 620 can afford.
  renderer.setPixelRatio(1);
  renderer.setClearColor(new Color(0, 0, 0), 0);

  const scene = new Scene();
  const camera = new PerspectiveCamera(FOV_DEGREES, 1, 0.1, 100);
  camera.position.z = CAMERA_Z;

  const uniforms = {
    uTime: { value: 0 },
    uAmplitude: { value: 0 },
    uRadius: { value: 1 },
    uTurbulence: { value: 0 },
    uBreath: { value: 0 },
    uAmpGain: { value: 0 },
    uPointScale: { value: 0.011 },
    uSizeScale: { value: 600 },
    uPulse: { value: 0 },
    uPulseGain: { value: 0 },
    uColorHot: { value: tokenColor('--sphere-hot') },
    uColorCool: { value: tokenColor('--sphere-cool') },
    uCoolMix: { value: 0.35 },
    uBrightness: { value: 0.6 },
  };

  const material = new ShaderMaterial({
    uniforms,
    vertexShader,
    fragmentShader,
    transparent: true,
    depthWrite: false,
    depthTest: false,
    blending: AdditiveBlending,
  });

  let geometry = buildGeometry(PARTICLE_COUNT[tier]);
  const points = new Points(geometry, material);
  scene.add(points);

  // Palette targets, resolved once from tokens. `amber` is the `blocked` state:
  // flat --status-warn, no gradient, because stillness plus a single hue is the
  // signal (CONTRACT §4.1).
  const palette = {
    flame: { hot: tokenColor('--sphere-hot'), cool: tokenColor('--sphere-cool') },
    amber: { hot: tokenColor('--status-warn'), cool: tokenColor('--status-warn') },
  };

  /* ── smoothed parameter state ──────────────────────────────────────────── */

  const smooth = { ...paramsFor('idle') } as SphereParams;
  /** Last state a frame was actually drawn with. Null until the first draw. */
  let renderedState: AgentState | null = null;
  let sceneTimeMs = 0;
  let breathPhase = 0;
  let spinAngle = 0;

  /**
   * §R.1 heartbeat pulse.
   *
   * `pulseElapsedMs` counts up only while a pulse is travelling, and is set to
   * -1 when idle. Nothing advances it except a real beat() call, so the pulse
   * cannot outlive the heartbeat that started it.
   */
  const PULSE_TRAVEL_MS = 1100;
  let pulseElapsedMs = -1;

  /**
   * §R.1 colour temperature. 0 = at rest (the state's own coolMix), 1 = hot.
   * Smoothed toward its target like every other visual parameter, so a spiky
   * CPU reading does not strobe the sphere.
   */
  let loadTarget = 0;
  let loadCurrent = 0;

  let offsetTargetPx = 0;
  let offsetCurrentPx = 0;
  let worldPerPixel = 0.002;

  /* ── frame accounting ──────────────────────────────────────────────────── */

  let rafId = 0;
  let lastStepAt = 0;
  let lastRafAt = 0;

  /**
   * Pacing by counting callbacks, not by accumulating milliseconds.
   *
   * The accumulator version compared elapsed time against a 33.33 ms target on
   * a display that can only deliver multiples of 16.67 ms. Those two grids beat
   * against each other: the gate alternated between admitting every 2nd and
   * every 3rd callback, which measured 36.5 ms / 27.4 fps instead of the 33.3 /
   * 30 the hardware was perfectly capable of. The renderer was never the
   * problem — measured cost was 0.20 ms against a 16.7 ms rAF interval.
   *
   * Rendering every Nth callback locks to the display exactly. No accumulator,
   * no remainder, no drift, and the cadence is a whole fraction of the refresh
   * rate by construction.
   */
  let rafEstimateMs = INITIAL_RAF_ESTIMATE_MS;
  let tickCounter = 0;

  // Three parallel sample buffers, all filled from the same window.
  const costSamples: number[] = [];
  const rafSamples: number[] = [];
  const presentSamples: number[] = [];
  /** False if the window was ever unfocused while collecting. */
  let windowFocused = true;

  let breaches = 0;
  const ZERO: Percentiles = { p50: 0, p95: 0 };
  let stats: SphereStats = {
    tier,
    particles: PARTICLE_COUNT[tier],
    cost: ZERO,
    raf: ZERO,
    present: ZERO,
    fps: 0,
    publishedAt: 0,
    focused: true,
    samples: 0,
    canvas: { cssW: 0, cssH: 0, bufW: 0, bufH: 0 },
  };

  function targetFps(): number {
    if (document.hidden) return 0;
    return document.hasFocus() ? FPS_FOCUSED : FPS_BACKGROUND;
  }

  /**
   * How many rAF callbacks per rendered frame, derived from the measured
   * refresh rate rather than assuming 60 Hz. At 60 Hz targeting 30 fps this is
   * 2; at 144 Hz it would be 5 (28.8 fps — the nearest whole fraction, which is
   * the right answer, because a non-whole one is what caused the beat).
   */
  function frameDivider(fps: number): number {
    const target = 1000 / fps;
    return Math.max(1, Math.min(20, Math.round(target / Math.max(rafEstimateMs, 1))));
  }

  /** Last size actually applied to the renderer. Drives the self-heal check. */
  let appliedW = 0;
  let appliedH = 0;
  let resizeReason = 'none';

  function resize(reason: string): void {
    const width = canvas.clientWidth || 1;
    const height = canvas.clientHeight || 1;
    if (width === appliedW && height === appliedH) return;

    resizeReason = reason;
    appliedW = width;
    appliedH = height;

    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();

    // gl_PointSize needs the projection scale to stay perspective-correct.
    const halfFov = (FOV_DEGREES * Math.PI) / 360;
    uniforms.uSizeScale.value = height / (2 * Math.tan(halfFov));

    // World units per screen pixel at the sphere's depth — used to translate a
    // drawer width in px into a scene offset.
    const visibleHeight = 2 * Math.tan(halfFov) * CAMERA_Z;
    worldPerPixel = (visibleHeight * camera.aspect) / width;

    stats = {
      ...stats,
      canvas: {
        cssW: width,
        cssH: height,
        bufW: renderer.domElement.width,
        bufH: renderer.domElement.height,
      },
    };
  }

  function summarise(samples: number[]): Percentiles {
    const sorted = [...samples].sort((a, b) => a - b);
    return { p50: percentile(sorted, 0.5), p95: percentile(sorted, 0.95) };
  }

  /**
   * Publish a window and, only if OUR cost overran, demote.
   *
   * Called on every frame regardless of focus — an unfocused window still
   * produces valid cost numbers, it is simply paced slower. The `focused` flag
   * travels with the window so nobody reads a 10 fps background cadence as a
   * performance result. The governor itself still only acts on focused windows,
   * because that is the only state whose pacing we are trying to hit.
   */
  function publishWindow(): void {
    if (costSamples.length < GOVERNOR_WINDOW) return;

    const cost = summarise(costSamples);
    const raf = summarise(rafSamples);
    const present = summarise(presentSamples);

    // Track the display's actual cadence from the median raw interval. The
    // median, not the mean: a single 200 ms hitch while a drawer animates must
    // not convince the pacer the monitor is 5 Hz.
    if (raf.p50 > 1) rafEstimateMs = raf.p50;
    const samples = costSamples.length;
    const focused = windowFocused;

    costSamples.length = 0;
    rafSamples.length = 0;
    presentSamples.length = 0;
    windowFocused = true;

    stats = {
      ...stats,
      cost,
      raf,
      present,
      fps: present.p50 > 0 ? 1000 / present.p50 : 0,
      publishedAt: performance.now(),
      focused,
      samples,
    };

    if (!focused) return;

    if (cost.p95 > COST_BUDGET_MS) {
      breaches += 1;
      if (breaches >= BREACHES_BEFORE_DEMOTION) {
        const next = DEMOTION[tier];
        breaches = 0;
        if (next) {
          setTier(next);
          onTierChange(next, `own cost p95 ${cost.p95.toFixed(1)}ms over ${COST_BUDGET_MS}ms`);
        }
      }
    } else {
      breaches = 0;
    }
  }

  function step(deltaMs: number): void {
    const state = getState();
    const target = paramsFor(state);

    // Exponential approach, framerate-compensated. ~180 ms to close most of the
    // gap: fast enough that the change is visible on the next frame (CONTRACT
    // §4's 80 ms), slow enough that six states do not look like six cuts.
    const rate = 1 - Math.exp(-deltaMs / 180);

    smooth.radius = approach(smooth.radius, target.radius, rate);
    smooth.turbulence = approach(smooth.turbulence, target.turbulence, rate);
    smooth.breathDepth = approach(smooth.breathDepth, target.breathDepth, rate);
    smooth.amplitudeGain = approach(smooth.amplitudeGain, target.amplitudeGain, rate);
    smooth.spin = approach(smooth.spin, target.spin, rate);
    smooth.pointScale = approach(smooth.pointScale, target.pointScale, rate);
    smooth.brightness = approach(smooth.brightness, target.brightness, rate);
    smooth.coolMix = approach(smooth.coolMix, target.coolMix, rate);

    if (!target.frozen && !reducedMotion) {
      sceneTimeMs += deltaMs;
      breathPhase += (deltaMs / target.breathPeriodMs) * Math.PI * 2;
      spinAngle += (smooth.spin * deltaMs) / 1000;
    }

    const amplitude = reducedMotion ? 0 : fakeAmplitude(sceneTimeMs, state);

    const tint = palette[target.palette];
    uniforms.uColorHot.value.lerp(tint.hot, rate);
    uniforms.uColorCool.value.lerp(tint.cool, rate);

    // Advance an in-flight pulse; hold hard at 0 when none is. Note this is
    // outside the `frozen` guard on purpose — a heartbeat is the daemon's
    // liveness, not the agent's activity, so it must still show while the
    // sphere is otherwise motionless in `blocked`.
    if (pulseElapsedMs >= 0) {
      pulseElapsedMs += deltaMs;
      if (pulseElapsedMs >= PULSE_TRAVEL_MS) pulseElapsedMs = -1;
    }
    // The gain, not the phase, is what says "no beat in flight". Phase 0 is the
    // band at full amplitude on the equator — see the note in particles.vert.
    const pulseInFlight = pulseElapsedMs >= 0;
    uniforms.uPulse.value = pulseInFlight ? pulseElapsedMs / PULSE_TRAVEL_MS : 0;
    uniforms.uPulseGain.value = pulseInFlight ? 1 : 0;

    uniforms.uTime.value = sceneTimeMs / 1000;
    uniforms.uAmplitude.value = amplitude;
    uniforms.uRadius.value = smooth.radius;
    uniforms.uTurbulence.value = smooth.turbulence;
    uniforms.uBreath.value = Math.sin(breathPhase) * smooth.breathDepth;
    uniforms.uAmpGain.value = smooth.amplitudeGain;
    uniforms.uPointScale.value = smooth.pointScale;
    // §R.1: hotter under load. coolMix 1 is fully --sphere-cool and 0 is fully
    // --sphere-hot, so load pulls it DOWN toward hot from whatever the current
    // state's resting temperature is.
    loadCurrent = approach(loadCurrent, loadTarget, rate);
    uniforms.uCoolMix.value = smooth.coolMix * (1 - loadCurrent);
    uniforms.uBrightness.value = smooth.brightness;

    points.rotation.y = spinAngle;

    // The drawer shift moves the sphere inside the scene rather than resizing
    // the canvas. Reallocating a WebGL drawing buffer every frame of a 200 ms
    // drawer animation would be far more expensive than the animation itself.
    offsetCurrentPx = approach(offsetCurrentPx, offsetTargetPx, rate);
    points.position.x = -offsetCurrentPx * 0.5 * worldPerPixel;

    renderer.render(scene, camera);

    // Report AFTER the draw call, not before: the claim being measured is
    // "this state reached the screen", and the uniforms for it are only on the
    // GPU once render() has submitted them. Presentation is up to one vsync
    // later still — the reader is told that, rather than this quietly counting
    // submission as visibility.
    if (state !== renderedState) {
      renderedState = state;
      options.onStateRendered?.(state, performance.now());
    }
  }

  /* ── dev probe ─────────────────────────────────────────────────────────── */

  /** Reused across reads; a fresh 3.6 MB array every 60 ms would be the cost. */
  let probeBuffer: Uint8Array | null = null;

  function probeFrame(mode: 'full' | 'column'): ProbeReading | null {
    if (disposed) return null;

    // A probe must never be the thing that reports a stale buffer as a fact.
    ensureSized('probe');

    const bufW = renderer.domElement.width;
    const bufH = renderer.domElement.height;
    if (bufW < 2 || bufH < 2) return null;

    // Redraw first. `preserveDrawingBuffer` is false, so the buffer is only
    // guaranteed readable between the draw and the end of this task — which is
    // also exactly why there is no frame here for a compositor to tear.
    step(0);

    const width = mode === 'column' ? Math.min(bufW, PROBE_COLUMN_PX) : bufW;
    const x0 = Math.floor((bufW - width) / 2);
    const needed = width * bufH * 4;
    if (!probeBuffer || probeBuffer.length < needed) probeBuffer = new Uint8Array(needed);
    const px = probeBuffer;

    const gl = renderer.getContext();
    gl.readPixels(x0, 0, width, bufH, gl.RGBA, gl.UNSIGNED_BYTE, px);

    let sum = 0;
    let sx = 0;
    let sy = 0;
    let lit = 0;
    for (let row = 0; row < bufH; row++) {
      // readPixels' origin is bottom-left. Everything else in this file — CSS,
      // the status bar, the reader of these numbers — is top-left.
      const cssY = bufH - 1 - row;
      const base = row * width * 4;
      for (let col = 0; col < width; col++) {
        const i = base + col * 4;
        const r = px[i] ?? 0;
        const g = px[i + 1] ?? 0;
        const b = px[i + 2] ?? 0;
        const lum = r > g ? (r > b ? r : b) : g > b ? g : b;
        if (lum < PROBE_THRESHOLD) continue;
        sum += lum;
        sx += lum * (x0 + col);
        sy += lum * cssY;
        lit += 1;
      }
    }

    const centreX = (bufW - 1) / 2;
    const centreY = (bufH - 1) / 2;
    const cx = sum > 0 ? sx / sum : Number.NaN;
    const cy = sum > 0 ? sy / sum : Number.NaN;

    return {
      bufW,
      bufH,
      cssW: canvas.clientWidth,
      cssH: canvas.clientHeight,
      x0,
      x1: x0 + width,
      cx,
      cy,
      dx: cx - centreX,
      dy: cy - centreY,
      sum,
      lit,
      uPulse: uniforms.uPulse.value,
      resizeReason,
    };
  }

  function frame(now: number): void {
    if (disposed) return;
    rafId = requestAnimationFrame(frame);

    // Sample the RAW callback interval first, before any gating. This is the
    // one number that says what the browser and compositor can actually
    // deliver, independent of anything we choose to do with it.
    if (lastRafAt > 0) rafSamples.push(now - lastRafAt);
    lastRafAt = now;

    const fps = targetFps();
    if (fps === 0) return;

    ensureSized('frame');

    // Render every Nth callback. See the note on `rafEstimateMs`.
    if (++tickCounter < frameDivider(fps)) return;
    tickCounter = 0;

    // The REAL gap between rendered frames — measured against the previous
    // render, not against a virtual schedule that drifts from wall time.
    const delta = lastStepAt > 0 ? now - lastStepAt : 1000 / fps;
    lastStepAt = now;
    presentSamples.push(delta);
    if (!document.hasFocus()) windowFocused = false;

    // Cost = everything we do, submit included. Compare THIS to a budget.
    const costStart = performance.now();
    step(Math.min(delta, 250));
    costSamples.push(performance.now() - costStart);

    publishWindow();
  }

  /* ── tier changes ──────────────────────────────────────────────────────── */

  function setTier(next: SphereTier): void {
    if (next === tier || disposed) return;
    tier = next;

    const count = PARTICLE_COUNT[tier];
    scene.remove(points);
    geometry.dispose();
    geometry = buildGeometry(count);
    points.geometry = geometry;
    scene.add(points);

    // Discard the in-flight window: it straddles two particle counts and would
    // attribute the old tier's cost to the new one.
    costSamples.length = 0;
    rafSamples.length = 0;
    presentSamples.length = 0;
    breaches = 0;
    stats = { ...stats, tier, particles: count };
  }

  /* ── context loss ──────────────────────────────────────────────────────── */

  function onContextLost(event: Event): void {
    event.preventDefault();
    // Not attempting a restore in Phase 1. On a legacy driver a lost context is
    // a symptom, and re-establishing one to lose it again is worse than falling
    // back to something that cannot fail. The DOM rung shows the same six
    // states.
    onTierChange('dom', 'WebGL context lost');
  }
  canvas.addEventListener('webglcontextlost', onContextLost, false);

  /* ── resize ────────────────────────────────────────────────────────────── */

  /**
   * §R.8 item 7 — one reflow per 100 ms.
   *
   * A drag emits a resize event per frame, and each one reallocates the WebGL
   * drawing buffer and recomputes the projection. Coalescing to 100 ms means a
   * two-second drag costs 20 reflows instead of 120. `resize()` recentres and
   * rescales, so the sphere cannot end up off-centre or clipped once it settles.
   */
  let resizeTimer: number | null = null;
  const observer = new ResizeObserver(() => {
    if (resizeTimer !== null) return; // already scheduled; the last state wins
    resizeTimer = window.setTimeout(() => {
      resizeTimer = null;
      if (!disposed) resize('observer');
    }, 100);
  });
  observer.observe(canvas);
  resize('init');

  /**
   * The drawing buffer must follow the CSS box. Verified every frame, because
   * the ResizeObserver is not reliable enough to be the only thing that knows.
   *
   * MEASURED: with the window resized from outside (a Win32 MoveWindow, which
   * is how §R.8 is verified), `canvas.clientWidth` went 1318 → 936 and stayed
   * there for over five seconds while the drawing buffer stayed 1318 and
   * `resize()` was never called. The notification simply did not arrive. A
   * stale buffer is not cosmetic: the browser scales it into the new CSS box,
   * so the sphere is stretched to the old aspect ratio and every projection
   * derived from the old size — point scale, the drawer's world-per-pixel — is
   * wrong until something else happens to trigger a reflow.
   *
   * Two integer comparisons per frame is not a cost worth reasoning about, and
   * it makes the invariant hold by checking rather than by trusting.
   */
  function ensureSized(reason: string): void {
    if (canvas.clientWidth !== appliedW || canvas.clientHeight !== appliedH) resize(reason);
  }

  /* ── go ────────────────────────────────────────────────────────────────── */

  // Held so dispose() can detach it. Only assigned on the reduced-motion path.
  let redrawListener: (() => void) | null = null;

  if (reducedMotion) {
    // No loop at all. The owner asked the OS for less motion, and a breathing
    // sphere is exactly the kind of thing that setting means. One static frame,
    // redrawn only when the state changes.
    step(0);
    redrawListener = () => {
      if (!disposed) step(0);
    };
    document.addEventListener('visibilitychange', redrawListener);
    stats = { ...stats, fps: 0 };
  } else {
    rafId = requestAnimationFrame(frame);
  }

  return {
    setTier,

    setCentreOffsetPx(pixels: number) {
      offsetTargetPx = pixels;
    },

    setLoad(load: number) {
      loadTarget = Number.isFinite(load) ? Math.max(0, Math.min(1, load)) : 0;
    },

    beat() {
      // Restart from the equator even if one is still travelling. At the
      // daemon's 5s cadence and a 1.1s travel they never overlap; if beats ever
      // arrive faster, the newest one is the truthful one to show.
      pulseElapsedMs = 0;
    },

    reprobeRefresh() {
      // Drop the estimate AND the in-flight samples: a window that straddles
      // two refresh rates would average them into a divider that is right for
      // neither. The next full window re-derives it from scratch.
      rafEstimateMs = INITIAL_RAF_ESTIMATE_MS;
      lastRafAt = 0;
      tickCounter = 0;
      costSamples.length = 0;
      rafSamples.length = 0;
      presentSamples.length = 0;
      resize('reprobe');
    },

    stats: () => stats,

    probeFrame,

    dispose() {
      disposed = true;
      cancelAnimationFrame(rafId);
      observer.disconnect();
      canvas.removeEventListener('webglcontextlost', onContextLost);
      if (redrawListener) document.removeEventListener('visibilitychange', redrawListener);
      geometry.dispose();
      material.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
    },
  };
}
