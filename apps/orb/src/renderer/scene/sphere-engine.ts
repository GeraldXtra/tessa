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
  LinearSRGBColorSpace,
  PerspectiveCamera,
  Points,
  Scene,
  ShaderMaterial,
  Vector3,
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
 * ─── THIS SPHERE CANNOT EXPRESS DURATION. Three levers tried; all retired. ───
 *
 * DO NOT RE-RUN ANY OF THESE. All three constants below are deliberately zero,
 * for three different measured reasons, and the machinery is left in place so
 * the record survives. `thinking` no longer intensifies at all, on purpose.
 *
 * THE PROBLEM. A local model can hold `thinking` for 60–90 s, and at rest the
 * state was metronomic: median per-pixel change 2.416 with a range of 1.06
 * across a full minute. It reads as ambient — a screensaver — where the owner
 * needs to see that she is working.
 *
 * ATTEMPT 1 — TURBULENCE AMPLITUDE, +35% at the ceiling.
 *   Measured imperceptible: the intensity term ramped 0.053 -> 0.964 across 308
 *   samples while `lit` moved -0.36%, `sum` +0.28%, and the correlation of the
 *   per-pixel delta with it was r = +0.050. The arithmetic says why: `wobble` is
 *   three sines multiplied, so its RMS is ~0.35, and 0.19 -> 0.257 of
 *   displacement is ~2% of the radius — a few pixels in a fuzzy point cloud.
 *   It was also directionally wrong: amplitude growth deepens the deformation
 *   that made `thinking` look crushed at the old turbulence of 0.19.
 *   See TURB_AMP_GAIN.
 *
 * ATTEMPT 2 — NOISE CLOCK RATE, 2.2x churn.
 *   Never disproved and never detectable. The mechanism demonstrably worked —
 *   the clock is accumulated, not `uTime * factor`, so the phase never snaps,
 *   and the shader consumes it. But a differencing metric saturates at the same
 *   displacement scale at which stochastic churn stops being legible to a
 *   person, so neither an instrument nor an eye could find it. See
 *   NOISE_RATE_GAIN.
 *
 * ATTEMPT 3 — SPIN RATE, 0.34 -> 0.75 rad/s.
 *   This one was MEASURED TO WORK and still failed. Differentiating the
 *   accumulated rotation angle against the sample clock gave 0.4361 / 0.6105 /
 *   0.7019 / 0.7349 rad/s at 5 / 20 / 40 / 60 s — matching the intended curve to
 *   three decimals, on screen, with the silhouette provably unchanged.
 *   Gerald watched sixty seconds of it: "It's not working harder at all. Just
 *   spinning." See SPIN_GAIN.
 *
 * WHY ALL THREE FAILED, WHICH IS THE PART WORTH KEEPING. Every one of them is a
 * RATE. A rate cannot encode elapsed time to a viewer who has no reference to
 * compare against — nobody can tell 0.34 rad/s from 0.75 rad/s without seeing
 * both, and by the time the ramp has moved, the earlier value is gone. Attempt 3
 * proves the point rather than being an exception to it: the change was real,
 * large, and correctly rendered, and it still read as "spinning", because
 * spinning faster is what it looks like. A fourth rate parameter would fail the
 * same way and for the same reason.
 *
 * The only cue that could carry duration is an ABSOLUTE, ACCUMULATING quantity
 * with a visible reference — a thing that is visibly 40% of the way to
 * somewhere. On this shell every such option is either deformation (reads as
 * distress; killed attempt 1) or colour (§R.7 reserves red for critical, and the
 * temperature already tracks cpuPct — a second colour language would make
 * "thinking a while" and "the machine is hot" the same picture). Anything else
 * is a progress affordance OUTSIDE the sphere, which is different work.
 *
 * WHAT `thinking` DOES CORRECTLY AND KEEPS: it reads as a sphere, it is
 * distinguishable from `working` and `idle` at a glance, and it does not look
 * distressed. That was the actual goal and it is met.
 *
 * THE PROBES STAY. `probeFrame`'s 'limb' and 'centre' modes, `pixelDelta`, and
 * `spinRad` are NOT dead code left behind by these attempts — they are working
 * instrumentation that cost real time to get right, and the next visual question
 * will want them. See ProbeReading.
 */
const THINKING_TAU_MS = 18_000;
/**
 * ATTEMPT 2 — noise clock multiplier. ZERO. Was 1.2, giving 2.2x churn.
 *
 * Never disproved, never detectable. At 50 ms the per-frame radial displacement
 * at BOTH rates already exceeds a particle's 2-3 px footprint, so a differencing
 * metric is saturated — and the same fact is why an eye has nothing coherent to
 * lock onto. The mechanism is correct: `uNoiseTime` is accumulated rather than
 * derived as `uTime * factor`, so the phase never snaps when the rate changes.
 *
 * Kept as a named zero rather than deleted so the attempt stays on the record
 * and so the accumulate-don't-scale pattern survives for whatever needs it next.
 */
const NOISE_RATE_GAIN = 0;

/**
 * ATTEMPT 3 — spin multiplier. ZERO. Was 1.2, giving 0.34 -> 0.75 rad/s.
 *
 * The only one of the three that was measured to WORK, and it still failed.
 *
 * It rendered exactly as designed: differentiating the accumulated rotation
 * angle gave 0.4361 / 0.6105 / 0.7019 / 0.7349 rad/s at 5 / 20 / 40 / 60 s,
 * matching `0.34 * (1 + 1.2 * focus)` to three decimals. The silhouette was
 * provably unchanged — rotation moves particles ALONG the shell rather than off
 * it, so it cannot deform, which is the property that killed attempt 1. The
 * per-pixel delta stayed flat (1.896 -> 1.909, r = +0.086) and that saturation
 * was predicted from the arithmetic before the run rather than discovered after.
 *
 * Gerald watched sixty seconds of it: "It's not working harder at all. Just
 * spinning." A faster rotation reads as a faster rotation. See the block above
 * THINKING_TAU_MS for why that generalises to every rate parameter.
 *
 * Kept as a named zero because the retirement of a lever that demonstrably
 * worked is a more useful record than a clean file.
 */
const SPIN_GAIN = 0;

/**
 * ATTEMPT 1 — turbulence amplitude growth. ZERO, after looking at it.
 *
 * This was 0.35, and measuring it produced two findings that both point the
 * same way:
 *
 *   IT WAS IMPERCEPTIBLE. Across 308 samples the intensity uniform ramped
 *   0.05 → 0.96 exactly as designed, while lit moved −0.36%, sum +0.28%, and
 *   the per-pixel delta correlated with it at r = +0.050. Nothing. The reason
 *   is arithmetic: `wobble` is a product of three sines, so its RMS is about
 *   0.35 rather than 1, and 0.19 → 0.257 of displacement on a unit sphere is
 *   ~2% of the radius — a handful of pixels, lost in a fuzzy point cloud.
 *
 *   AND IT POINTED THE WRONG WAY. The captures settle it: at base turbulence
 *   `thinking` is ALREADY not a sphere. It is a creased, cornered, crumpled
 *   shape — next to `idle` and `working`, which are both clean spheres, it is
 *   the only state that reads as something being crushed rather than something
 *   being done. Growing the amplitude deepens exactly the deformation that
 *   makes it look distressed.
 *
 * So amplitude is not the lever. Rate is: it makes the same shell churn faster
 * without deforming it further, which is what effort looks like when nothing is
 * wrong. Left at zero rather than deleted, because the mechanism is correct and
 * the right value for it depends on the base turbulence — see the report.
 */
const TURB_AMP_GAIN = 0;

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
 * The LIMB patch — a probe that can actually see the turbulence rate.
 *
 * ─── why the previous two attempts could not ───
 * The per-pixel delta over a wide column saturated at 250 ms and the probe died
 * at 50 ms, and I blamed the sampling interval both times. The interval was not
 * the problem. THE SPIN WAS.
 *
 * For rotation about Y at angular rate w, a particle's screen-space velocity
 * depends entirely on where it sits. At the centre of the disc (z = +R, x = 0)
 * the velocity is `w y^ x R z^ = wR x^` — maximum lateral motion. At the LEFT
 * or RIGHT LIMB (x = -+R, z = 0) it is `w y^ x (-+R x^) = +-wR z^` — motion
 * straight toward or away from the camera, which changes screen position only
 * through perspective and is therefore almost nil.
 *
 * `thinking` spins at 0.34 rad/s, which at a ~259 px screen radius drags
 * centre-disc particles ~2.6 px per frame — comparable to a particle's own
 * diameter. So over the whole disc the field decorrelates from ROTATION alone
 * within a frame or two, and a metric differencing frames is pinned at "totally
 * different" no matter what the wobble does. Shrinking the readback would not
 * have fixed that; it would have made a cheaper saturated metric.
 *
 * At the limb, rotation contributes almost nothing to screen motion and the
 * dominant term is radial displacement — which is exactly what turbulence
 * produces. So this is where the wobble rate is legible.
 *
 * ─── one patch, not two ───
 * The cost of a read is the GPU pipeline flush, not the byte count, so a second
 * patch roughly doubles the cost. And under a Y-axis rotation the two limbs are
 * statistically equivalent: the right limb carries no information the left one
 * lacks, only more samples. Taller rather than doubled is the cheaper way to
 * buy sample size — 80 x 160 is 12,800 px against the column's 166,080.
 */
const PROBE_LIMB_W = 80;
const PROBE_LIMB_H = 160;

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
  /** Where the engine COMMANDED the centre to be. See the note at the read. */
  expectedCx: number;
  expectedCy: number;
  /** Total luminance over the region — the pulse's signal. */
  sum: number;
  /**
   * Mean absolute per-pixel change since the previous read of the same region.
   * NaN on the first read, and whenever the region size changed.
   *
   * `sum` cannot answer "is this moving". Total brightness is very nearly
   * conserved under motion — a rigid rotation of a symmetric shell moves every
   * particle while leaving the total almost unchanged — so a near-zero
   * frame-to-frame change in `sum` was being read as a stall when it was
   * nothing of the kind. This differences the actual pixels, which is the
   * question.
   */
  pixelDelta: number;
  /** Pixels above threshold. */
  lit: number;
  /** The pulse uniform at the instant of the read. Ground truth for §R.1. */
  uPulse: number;
  /**
   * How long the current agent state has been held, in ms, and the resulting
   * 0..1 intensity. Carried so the turbulence ramp can be bucketed by TIME IN
   * STATE rather than by wall clock — the two differ by however long the app
   * took to mount, which is exactly the kind of approximation that produces a
   * figure nobody can check.
   */
  heldMs: number;
  focus: number;
  /**
   * Accumulated rotation in radians at the instant of the read.
   *
   * Carried because a differencing metric cannot measure a ROTATION rate: at
   * the disc centre a particle already moves ~4.9 px per 50 ms at the resting
   * 0.34 rad/s, well past its own 2-3 px footprint, so consecutive reads are
   * decorrelated at every rate the ramp can produce. Differentiating this
   * against the sample timestamps measures the rendered rotation rate directly,
   * with no saturation to argue about.
   */
  spinRad: number;
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
  /**
   * DEV ONLY. `--force-depth=<0..1>`, the depth-shading falloff. Omitted uses
   * DEPTH_FAR_DEFAULT; 1.0 disables depth and reproduces the pre-depth shell
   * exactly, which is how the before/after captures are taken.
   */
  depthFar?: number;
  /**
   * DEV ONLY. `--force-sphere=<rimGain>,<rimSize>,<bodyBright>,<bodySize>`.
   *
   * The rim was tuned by MEASUREMENT, not by eye, and a cold build of this app
   * takes ~100 s. Four numbers on the command line turn a sweep of twelve
   * candidate settings from twenty minutes of rebuilds into one build and
   * twelve launches, which is the difference between measuring the rim and
   * guessing at it. It matches the `--force-` prefix, so it is already treated
   * as an instrumented launch and can never write window or theme state.
   *
   * `bodyBright` and `bodySize` are MULTIPLIERS on the per-state values in
   * states.ts, so the six states keep their relative ordering under a sweep —
   * the thing item 2f has to survive.
   */
  rim?: {
    gain: number;
    size: number;
    bodyBright: number;
    bodySize: number;
    darkSide: number;
    lambertPow: number;
    jitter: number;
    rimPow: number;
    spreadPow: number;
  };
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
  /**
   * Where the sphere sits, as a shift from the canvas centre. Animated.
   *
   * TWO AXES NOW, and the reason is the composition rather than the drawer: the
   * sphere is placed off-centre by design (34% of width, 47% of height), and
   * the drawer shift has to compose with that rather than fight it. The caller
   * computes one target position from the whole layout — base placement, drawer
   * open or shut, column visible or not — and passes the result. This engine
   * does not know what a drawer is.
   *
   * Units are the same as before: a positive `xPx` moves the sphere LEFT on
   * screen by `xPx / 2` pixels, a positive `yPx` moves it UP by `yPx / 2`. That
   * halving is inherited — it is what made a 320px drawer shift the sphere by
   * the 160px its available space actually moved.
   */
  setCentreOffset(xPx: number, yPx: number): void;
  /**
   * Scale the whole object so it always fits its frame. 1 is the natural size.
   *
   * THE SPHERE MUST NOT OVERFLOW, at any window size. Its natural projected
   * radius is `tan(asin(R / CAMERA_Z)) * canvasHeight / (2 tan(fov/2))`, which
   * is 43% of the canvas height — an 86%-of-height disc that clipped against
   * the inset border top and bottom in the owner's own screenshot.
   *
   * Scaling BOTH the radius and the point size by the same factor is what makes
   * this a true scaling rather than a squeeze: shrinking the shell alone would
   * leave the sprites at their old size and quietly make the sphere denser, so
   * the crescent measured against the reference would no longer be the crescent
   * on screen. The caller computes the factor from the stage it actually has;
   * this engine does not know what a panel is.
   */
  setFit(factor: number): void;
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
   * full-height strip, for the pulse. `'limb'` reads a small patch on the
   * sphere's left edge, for the turbulence RATE — the only place where the spin
   * does not swamp the measurement. See PROBE_LIMB_W.
   *
   * This is deliberately NOT a passive sampler of whatever the loop last drew.
   * It calls `step(0)`, and a zero delta is a no-op for every piece of animated
   * state in this engine — every `approach()` gets rate `1 - e^0 = 0`, and
   * every clock advances by `deltaMs`. So the probe re-renders exactly what is
   * on screen without becoming part of the animation it is measuring, and two
   * probes with no frame between them are identical by construction.
   */
  probeFrame(mode: 'full' | 'column' | 'limb' | 'centre'): ProbeReading | null;
  /**
   * Re-read the colour tokens and retarget the palette. Called on a theme
   * switch.
   *
   * The sphere's colours are shader UNIFORMS resolved once at construction, not
   * CSS — so a theme switch repaints every label, rail and marker on the
   * surface and leaves the one thing in the middle of the screen unchanged
   * unless this runs. It retargets rather than snapping: `uColorHot` and
   * `uColorCool` keep lerping toward the palette in `step()`, so the sphere
   * crossfades into the new theme over the same handful of frames a state
   * change uses, instead of jumping.
   */
  retint(): void;
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
      /**
       * `setStyle(raw, LinearSRGBColorSpace)`, NOT `set(raw)`. This is a bug
       * fix, and it is the reason the sphere has never once shown its own
       * token colours.
       *
       * three.js enables `ColorManagement` by default, so `Color.set()` on any
       * of the colour tokens treats the string as sRGB and converts it into the
       * linear working space. That is correct for a lit material, because the standard
       * fragment chunks run `<colorspace_fragment>` at the end and encode the
       * result back to sRGB for display.
       *
       * This material does neither. It is a raw `ShaderMaterial` writing
       * `gl_FragColor` directly, and `grep -c colorspace_fragment` on
       * shaders/particles.frag.glsl returns 0 — nothing encodes back. So the
       * linearised value was being written straight to an sRGB framebuffer and
       * displayed as a much darker, more saturated colour. Measured before this
       * change:
       *
       * (hex written without the leading hash so this comment does not itself
       * trip the no-hard-coded-colour gate — these are measurements, not values)
       *
       *     token          declared   rendered as
       *     --sphere-hot   FF3B00     FF0B00
       *     --sphere-cool  FFA94D     FF6513
       *     --accent       FF6B1A     FF2503
       *
       * Naming the working space tells three the string is ALREADY in it, so no
       * conversion happens and the shader emits the literal token value. The
       * alternative — adding `<colorspace_fragment>` to the shader — reaches the
       * same place, but through the hot path rather than through four calls made
       * at construction.
       *
       * This matters beyond tidiness now: the owner is choosing between five
       * palettes by eye, and every swatch he judged would have been rendered as
       * a colour that is not in tokens.json.
       */
      color.setStyle(raw, LinearSRGBColorSpace);
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

/** Default depth falloff. `--force-depth=` overrides it via bootstrap. */
export const DEPTH_FAR_DEFAULT = 0.42;

/**
 * THE RIM — extra brightness and extra point size at the silhouette.
 *
 * Both numbers are MEASURED, not chosen. See the sweep in item 2 of the build
 * report and the varying `vFresnel` in particles.vert.glsl. `--force-sphere=`
 * overrides all four rim/body numbers so a sweep needs one build, not twelve.
 */
export const RIM_GAIN_DEFAULT = 0.5;
export const RIM_SIZE_DEFAULT = 3.5;

/**
 * How much brightness the side facing away from the light keeps.
 *
 * The reference's dark limb measures 0.0% lit coverage against 34.8% at the
 * bright one, so the honest copy of it is zero. Zero renders a crescent moon,
 * not a sphere: the terminator becomes a hard edge and half the shell is simply
 * gone. This floor is the compromise, and it is stated rather than hidden.
 */
export const DARK_SIDE_DEFAULT = 0.18;

/** Exponent on the wrapped lambert. See the uniform's note in particles.frag. */
export const LAMBERT_POW_DEFAULT = 1.8;

/**
 * Peak-to-peak radial jitter. ZERO, and deliberately so.
 *
 * Built to break the Fibonacci lattice's concentric arcs, and it does — but it
 * breaks the silhouette with them, because the crisp limb and the visible
 * lattice come from the same evenness. Measured by eye across 0.0 / 0.10 /
 * 0.20: at 0.10 the crescent is already a diffuse band rather than an edge, and
 * at 0.20 the sphere is a fuzzy cloud with no surface at all — the exact
 * complaint this whole round exists to fix.
 *
 * The lattice was fixed the other way instead, by raising the particle count so
 * the dots are small enough that the arcs stop being legible. The uniform stays
 * at zero rather than being deleted so the finding survives and so the next
 * person does not spend the same afternoon rediscovering it.
 */
export const JITTER_DEFAULT = 0.0;

/**
 * The crescent's radial width, and its energy conservation. Both MEASURED.
 *
 * `RIM_POW` is the exponent on the fresnel; LOWER IS WIDER. It went 2.0 -> 1.3
 * -> its final value because the side-by-side against ref-2.png showed the
 * reference's crescent as a broad granular band and this one as a thin wire:
 * mean blob area at the lit mid-radius was 4.1 px here against 12.9 px there.
 *
 * `SPREAD_POW` is what makes broad and dim compatible, and without it they are
 * not. See uSpreadPow in particles.frag for the arithmetic; the short version
 * is that under additive blending a wider band is automatically a brighter one,
 * and the reference's band is wide and NOT bright — its limb is only 1.65x the
 * luminance of its own body.
 */
export const RIM_POW_DEFAULT = 0.8;
export const SPREAD_POW_DEFAULT = 0.6;

/**
 * The light, in VIEW space. Right, below, and slightly toward the camera.
 *
 * Direction taken from the reference rather than chosen: its bottom patch
 * measures 12.3% lit coverage against 1.5% at the top, and its right limb 34.8%
 * against 0.0% at the left. Right and below, therefore, and the small +z tips
 * the highlight a few degrees onto the face so the crescent has a soft inner
 * edge instead of ending exactly on the silhouette.
 *
 * The y component was -0.45 on the first pass and is measured down to -0.28.
 * The reference's ratio of bright limb to bottom is 34.8 : 12.3, i.e. 2.8 : 1;
 * at -0.45 mine measured 45.3 : 35.0, i.e. 1.3 : 1. The light was sitting too
 * low, which pooled the crescent under the sphere and read as the contact
 * ellipse he had just rejected, in a different form.
 */
const LIGHT_DIR = new Vector3(1.0, -0.28, 0.3).normalize();

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
    uNoiseTime: { value: 0 },
    uColorHot: { value: tokenColor('--sphere-hot') },
    uColorCool: { value: tokenColor('--sphere-cool') },
    uCoolMix: { value: 0.35 },
    uBrightness: { value: 0.6 },
    /**
     * §R.1 depth shading. How much brightness the FARTHEST particle keeps.
     *
     * 0.42 is a judgement, not a measured threshold, and it is stated rather
     * than hidden so it can be retuned by eye: the far side keeps 42% of its
     * brightness, which is enough separation to read as volume and not so much
     * that the back of the shell disappears and the sphere becomes a bowl.
     *
     * `--force-depth=<0..1>` overrides it, and 1.0 restores the pre-depth shell
     * exactly. That is how the before/after captures are taken — one binary,
     * one flag, identical geometry, so the comparison cannot be confounded the
     * way a 984x652-against-1366x720 comparison once was.
     */
    uDepthFar: { value: options.depthFar ?? DEPTH_FAR_DEFAULT },
    /**
     * THE RIM. See vFresnel in the vertex stage for the measurement that
     * produced these two numbers.
     *
     * `uRimGain` is extra brightness at the silhouette, `uRimSize` extra point
     * size there. Both are needed: brightness alone leaves separate dots
     * separate, and the reference's limb reads as a surface precisely because
     * its particles have merged (mean blob 214.8 px against 6.3 px here).
     *
     * They also pay back the depth term's 38%. Depth removed brightness from
     * the whole shell, which was right for form and wrong for mass; this puts
     * it back at the edge, where it builds a boundary instead of a fog.
     */
    uRimGain: { value: options.rim?.gain ?? RIM_GAIN_DEFAULT },
    uRimSize: { value: options.rim?.size ?? RIM_SIZE_DEFAULT },
    uDarkSide: { value: options.rim?.darkSide ?? DARK_SIDE_DEFAULT },
    uLambertPow: { value: options.rim?.lambertPow ?? LAMBERT_POW_DEFAULT },
    uJitter: { value: options.rim?.jitter ?? JITTER_DEFAULT },
    uRimPow: { value: options.rim?.rimPow ?? RIM_POW_DEFAULT },
    uSpreadPow: { value: options.rim?.spreadPow ?? SPREAD_POW_DEFAULT },
    uLightDir: { value: LIGHT_DIR.clone() },
  };

  // Multipliers on the per-state body values. 1 unless a sweep is running.
  const bodyBrightMul = options.rim?.bodyBright ?? 1;
  const bodySizeMul = options.rim?.bodySize ?? 1;

  // Uniform fit scaling. Smoothed like every other placement value so a
  // window resize eases rather than snapping. See SphereEngine.setFit.
  let fitTarget = 1;
  let fitCurrent = 1;

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

  /* ── sustained-state intensity (see THINKING_TAU_MS) ───────────────────── */

  /** The state the intensity clock is currently counting, and for how long. */
  let intensityState: AgentState | null = null;
  let stateHeldMs = 0;
  /** 0..1, smoothed, so leaving `thinking` eases out instead of snapping. */
  let focusCurrent = 0;
  /** The turbulence clock, accumulated at a variable rate. Seconds. */
  let noisePhaseS = 0;
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
  let offsetTargetYPx = 0;
  let offsetCurrentYPx = 0;
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

    // How long this state has been held, and how hard she is visibly working
    // because of it. Computed BEFORE the motion integration below, so the spin
    // ramp applies on the same frame rather than one behind. Only `thinking`
    // intensifies: it is the state that can last 90 s with nothing else to show
    // for it.
    if (state !== intensityState) {
      intensityState = state;
      stateHeldMs = 0;
    } else if (!target.frozen && !reducedMotion) {
      stateHeldMs += deltaMs;
    }
    const focusTarget = state === 'thinking' ? 1 - Math.exp(-stateHeldMs / THINKING_TAU_MS) : 0;
    focusCurrent = approach(focusCurrent, focusTarget, rate);

    if (!target.frozen && !reducedMotion) {
      sceneTimeMs += deltaMs;
      breathPhase += (deltaMs / target.breathPeriodMs) * Math.PI * 2;
      // THE INTENSIFICATION. Rotation, not displacement — it is a single
      // coherent cue the eye integrates, and it moves particles ALONG the shell
      // rather than off it, so the silhouette cannot deform. See SPIN_GAIN.
      spinAngle += (smooth.spin * (1 + SPIN_GAIN * focusCurrent) * deltaMs) / 1000;
      // Accumulated, never uTime * factor — see the note in particles.vert.
      noisePhaseS += (deltaMs / 1000) * (1 + NOISE_RATE_GAIN * focusCurrent);
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
    uniforms.uNoiseTime.value = noisePhaseS;
    uniforms.uAmplitude.value = amplitude;
    fitCurrent = approach(fitCurrent, fitTarget, rate);
    uniforms.uRadius.value = smooth.radius * fitCurrent;
    uniforms.uTurbulence.value = smooth.turbulence * (1 + TURB_AMP_GAIN * focusCurrent);
    uniforms.uBreath.value = Math.sin(breathPhase) * smooth.breathDepth;
    uniforms.uAmpGain.value = smooth.amplitudeGain;
    uniforms.uPointScale.value = smooth.pointScale * bodySizeMul * fitCurrent;
    // §R.1: hotter under load. coolMix 1 is fully --sphere-cool and 0 is fully
    // --sphere-hot, so load pulls it DOWN toward hot from whatever the current
    // state's resting temperature is.
    loadCurrent = approach(loadCurrent, loadTarget, rate);
    uniforms.uCoolMix.value = smooth.coolMix * (1 - loadCurrent);
    uniforms.uBrightness.value = smooth.brightness * bodyBrightMul;

    points.rotation.y = spinAngle;

    // The drawer shift moves the sphere inside the scene rather than resizing
    // the canvas. Reallocating a WebGL drawing buffer every frame of a 200 ms
    // drawer animation would be far more expensive than the animation itself.
    offsetCurrentPx = approach(offsetCurrentPx, offsetTargetPx, rate);
    offsetCurrentYPx = approach(offsetCurrentYPx, offsetTargetYPx, rate);
    points.position.x = -offsetCurrentPx * 0.5 * worldPerPixel;
    // World +y is screen UP, so a positive yPx lifts the sphere.
    points.position.y = offsetCurrentYPx * 0.5 * worldPerPixel;

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
  /** Previous read, for the per-pixel difference. Same size or discarded. */
  let probePrev: Uint8Array | null = null;
  let probePrevLen = 0;

  function probeFrame(mode: 'full' | 'column' | 'limb' | 'centre'): ProbeReading | null {
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

    let width: number;
    let height: number;
    let x0: number;
    /** Bottom-left origin, as readPixels wants it. */
    let glY0: number;

    if (mode === 'limb' || mode === 'centre') {
      // Screen radius from the projection the engine already maintains:
      // uSizeScale is height / (2 tan(fov/2)), so pixels-per-world-unit at the
      // sphere's depth is uSizeScale / CAMERA_Z.
      const screenR = uniforms.uRadius.value * (uniforms.uSizeScale.value / CAMERA_Z);
      // The drawer shift is `-offsetCurrentPx * 0.5 * worldPerPixel` world
      // units, and worldPerPixel * (uSizeScale / CAMERA_Z) is exactly 1, so in
      // pixels the shift is simply half the offset.
      const shiftPx = -offsetCurrentPx * 0.5;
      width = Math.min(PROBE_LIMB_W, bufW);
      height = Math.min(PROBE_LIMB_H, bufH);
      // 'limb' sits on the silhouette, where rotation contributes least to
      // screen motion. 'centre' sits on the disc centre, where it contributes
      // MOST — which is the sensitive region for a spin change and the reason
      // the same patch serves both questions from opposite ends.
      const cssCx = (bufW - 1) / 2 + shiftPx - (mode === 'limb' ? screenR : 0);
      const cssCy = (bufH - 1) / 2;
      x0 = Math.max(0, Math.min(bufW - width, Math.round(cssCx - width / 2)));
      const cssY0 = Math.max(0, Math.min(bufH - height, Math.round(cssCy - height / 2)));
      glY0 = bufH - (cssY0 + height);
    } else {
      width = mode === 'column' ? Math.min(bufW, PROBE_COLUMN_PX) : bufW;
      height = bufH;
      x0 = Math.floor((bufW - width) / 2);
      glY0 = 0;
    }

    const needed = width * height * 4;
    if (!probeBuffer || probeBuffer.length < needed) probeBuffer = new Uint8Array(needed);
    const px = probeBuffer;

    const gl = renderer.getContext();
    gl.readPixels(x0, glY0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, px);

    let sum = 0;
    let sx = 0;
    let sy = 0;
    let lit = 0;
    for (let row = 0; row < height; row++) {
      // readPixels' origin is bottom-left. Everything else in this file — CSS,
      // the status bar, the reader of these numbers — is top-left.
      const cssY = bufH - 1 - (glY0 + row);
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

    // Per-pixel change against the previous read. Green channel only: the
    // three are near-identical on an additively-blended monochrome-ish shell,
    // so differencing all three costs 3× for no extra information.
    let pixelDelta = Number.NaN;
    if (probePrev && probePrevLen === needed) {
      let acc = 0;
      let n = 0;
      for (let i = 1; i < needed; i += 4) {
        acc += Math.abs((px[i] ?? 0) - (probePrev[i] ?? 0));
        n += 1;
      }
      pixelDelta = n > 0 ? acc / n : Number.NaN;
    }
    if (!probePrev || probePrevLen !== needed) probePrev = new Uint8Array(needed);
    probePrev.set(px.subarray(0, needed));
    probePrevLen = needed;

    /**
     * MEASURED AGAINST WHERE THE ENGINE PUT IT, not against the middle of the
     * window.
     *
     * This used to be `cx - (bufW-1)/2`, which asked "is the sphere centred in
     * the buffer". The moment the composition placed it at 34% of the width
     * that question had a large permanent answer and the instrument stopped
     * measuring anything — it would have reported a ~243 px error forever, on a
     * sphere that was exactly where it was told to be.
     *
     * The question worth asking is "is the sphere where the engine commanded
     * it", which stays valid at any composition, any window size, and with a
     * drawer open. Both numbers are carried out so a reader sees the position
     * and the expectation rather than trusting a difference.
     */
    const expectedCx = (bufW - 1) / 2 - offsetCurrentPx * 0.5;
    const expectedCy = (bufH - 1) / 2 - offsetCurrentYPx * 0.5;
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
      expectedCx,
      expectedCy,
      dx: cx - expectedCx,
      dy: cy - expectedCy,
      sum,
      pixelDelta,
      lit,
      uPulse: uniforms.uPulse.value,
      heldMs: stateHeldMs,
      focus: focusCurrent,
      spinRad: spinAngle,
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

    setFit(factor: number) {
      // Clamped, not trusted. A zero or a NaN out of a layout calculation
      // would silently render nothing, which is the hardest bug to see.
      fitTarget = Number.isFinite(factor) ? Math.min(1, Math.max(0.2, factor)) : 1;
    },

    setCentreOffset(xPx: number, yPx: number) {
      offsetTargetPx = Number.isFinite(xPx) ? xPx : 0;
      offsetTargetYPx = Number.isFinite(yPx) ? yPx : 0;
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

    /**
     * Re-read the tokens after a theme switch.
     *
     * `copy()` into the existing Color objects rather than replacing them: the
     * palette entries are the lerp TARGETS that `step()` reads every frame, so
     * mutating them in place retargets the crossfade already in flight. A
     * reassignment would work too, but only because nothing else holds a
     * reference — and that is exactly the kind of thing that stops being true
     * later.
     *
     * `amber` is re-read as well, and it will not move: it comes from
     * `--status-warn`, which no theme touches. Reading it anyway keeps one code
     * path for "the tokens may have changed" rather than encoding the current
     * list of themed properties in a second place.
     */
    retint() {
      palette.flame.hot.copy(tokenColor('--sphere-hot'));
      palette.flame.cool.copy(tokenColor('--sphere-cool'));
      palette.amber.hot.copy(tokenColor('--status-warn'));
      palette.amber.cool.copy(tokenColor('--status-warn'));
    },

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
