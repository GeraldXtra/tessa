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

/** Samples per governor window. 120 at 30 fps ≈ 4 s of evidence before acting. */
const GOVERNOR_WINDOW = 120;

/**
 * How far past the target interval counts as "not keeping up".
 *
 * MEASURED, not guessed. This was 1.5 and it was wrong. On a 60 Hz display a
 * rAF-driven loop can only land on multiples of 16.67 ms, so a 30 fps target
 * (33.3 ms) has exactly two reachable cadences: 33.3 ms and 50 ms. At 1.5 the
 * threshold was 50.0 ms — meaning a single three-vsync frame put p95 at 50.1
 * and tripped a demotion. On this machine that cascaded low → dom within a
 * minute, discarding WebGL entirely because of ordinary vsync jitter.
 *
 * At 2.0 the threshold is 66.7 ms — four vsyncs, i.e. sustained sub-15 fps,
 * which is a real failure rather than a quantisation artefact.
 */
const OVERRUN_FACTOR = 2.0;

/** Two consecutive bad windows, not one — a single GC pause is not a verdict. */
const BREACHES_BEFORE_DEMOTION = 2;

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

export interface SphereStats {
  tier: SphereTier;
  particles: number;
  /** Frame interval percentiles, ms. The honest measure of keeping up. */
  p50: number;
  p95: number;
  /** Time spent inside renderer.render(), ms. CPU submit cost only. */
  submitMs: number;
  fps: number;
}

export interface SphereEngineOptions {
  canvas: HTMLCanvasElement;
  initialTier: SphereTier;
  /** Read each frame. Never a subscription — no React involvement. */
  getState: () => AgentState;
  /** Fired when the governor or a context loss changes the tier. */
  onTierChange: (tier: SphereTier, reason: string) => void;
}

export interface SphereEngine {
  setTier(tier: SphereTier): void;
  /** Shift the sphere left when a drawer opens. Pixels, animated internally. */
  setCentreOffsetPx(pixels: number): void;
  stats(): SphereStats;
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
  let sceneTimeMs = 0;
  let breathPhase = 0;
  let spinAngle = 0;

  let offsetTargetPx = 0;
  let offsetCurrentPx = 0;
  let worldPerPixel = 0.002;

  /* ── frame accounting ──────────────────────────────────────────────────── */

  let rafId = 0;
  let lastFrameAt = 0;
  const intervals: number[] = [];
  let breaches = 0;
  let stats: SphereStats = {
    tier,
    particles: PARTICLE_COUNT[tier],
    p50: 0,
    p95: 0,
    submitMs: 0,
    fps: 0,
  };

  function targetFps(): number {
    if (document.hidden) return 0;
    return document.hasFocus() ? FPS_FOCUSED : FPS_BACKGROUND;
  }

  function resize(): void {
    const width = canvas.clientWidth || 1;
    const height = canvas.clientHeight || 1;

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
  }

  function applyGovernor(intervalMs: number, targetIntervalMs: number): void {
    intervals.push(intervalMs);
    if (intervals.length < GOVERNOR_WINDOW) return;

    const sorted = [...intervals].sort((a, b) => a - b);
    const p50 = percentile(sorted, 0.5);
    const p95 = percentile(sorted, 0.95);
    intervals.length = 0;

    stats = { ...stats, p50, p95, fps: p50 > 0 ? 1000 / p50 : 0 };

    if (p95 > targetIntervalMs * OVERRUN_FACTOR) {
      breaches += 1;
      if (breaches >= BREACHES_BEFORE_DEMOTION) {
        const next = DEMOTION[tier];
        breaches = 0;
        if (next) {
          setTier(next);
          onTierChange(next, `frame p95 ${p95.toFixed(1)}ms exceeded budget`);
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

    uniforms.uTime.value = sceneTimeMs / 1000;
    uniforms.uAmplitude.value = amplitude;
    uniforms.uRadius.value = smooth.radius;
    uniforms.uTurbulence.value = smooth.turbulence;
    uniforms.uBreath.value = Math.sin(breathPhase) * smooth.breathDepth;
    uniforms.uAmpGain.value = smooth.amplitudeGain;
    uniforms.uPointScale.value = smooth.pointScale;
    uniforms.uCoolMix.value = smooth.coolMix;
    uniforms.uBrightness.value = smooth.brightness;

    points.rotation.y = spinAngle;

    // The drawer shift moves the sphere inside the scene rather than resizing
    // the canvas. Reallocating a WebGL drawing buffer every frame of a 200 ms
    // drawer animation would be far more expensive than the animation itself.
    offsetCurrentPx = approach(offsetCurrentPx, offsetTargetPx, rate);
    points.position.x = -offsetCurrentPx * 0.5 * worldPerPixel;

    const submitStart = performance.now();
    renderer.render(scene, camera);
    stats = { ...stats, submitMs: performance.now() - submitStart };
  }

  function frame(now: number): void {
    if (disposed) return;
    rafId = requestAnimationFrame(frame);

    const fps = targetFps();
    if (fps === 0) return;

    const targetInterval = 1000 / fps;
    const delta = now - lastFrameAt;
    if (delta < targetInterval) return;

    // Subtract the remainder rather than resetting, so the cadence does not
    // drift against the display's own refresh.
    lastFrameAt = now - (delta % targetInterval);

    step(Math.min(delta, 250));

    // Only judge performance when we are actually asking for 30 fps. The
    // background cadence is 100 ms intervals by design, not by failure.
    if (fps === FPS_FOCUSED) applyGovernor(delta, targetInterval);
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

    intervals.length = 0;
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

  const observer = new ResizeObserver(() => resize());
  observer.observe(canvas);
  resize();

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
    lastFrameAt = performance.now();
    rafId = requestAnimationFrame(frame);
  }

  return {
    setTier,

    setCentreOffsetPx(pixels: number) {
      offsetTargetPx = pixels;
    },

    stats: () => stats,

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
