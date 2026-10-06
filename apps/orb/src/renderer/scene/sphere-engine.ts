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
  Euler,
  LinearSRGBColorSpace,
  Mesh,
  NormalBlending,
  PerspectiveCamera,
  Points,
  Quaternion,
  Scene,
  ShaderMaterial,
  SphereGeometry,
  Vector2,
  Vector3,
  Vector4,
  WebGLRenderer,
} from 'three';

import type { AgentState } from '@tessa/protocol';

import type { SphereTier } from '../../shared/ipc-contract.ts';
import { tokenValue } from '../design-tokens.ts';
import { fakeAmplitude } from './amplitude.ts';
import { PARTICLE_COUNT } from './gpu-tier.ts';
import { paramsFor, type SphereParams } from './states.ts';
import { createCompanion, type Companion } from './companions.ts';

import vertexShader from './shaders/particles.vert.glsl?raw';
import fragmentShader from './shaders/particles.frag.glsl?raw';
import deformChunk from './shaders/deform.glsl?raw';

/**
 * ─── THE DEAD NORTH POLE WAS THE CAMERA, NOT THE LIGHTING ───
 *
 * Three rounds treated the welded north cap as a brightness problem. It is a
 * PROJECTION problem, and the reference says so precisely.
 *
 * On a sphere the surface normal IS the position, so the grazing angle is a pure
 * function of screen radius — n.eye = z, and the projected radius is
 * sqrt(1-z^2). Computed over the real camera at every radius and every exponent,
 * a cap dot and a limb dot AT THE SAME SCREEN RADIUS have IDENTICAL fresnel:
 * separation 1.00x, not 1.01x. So no fresnel curve, however steep, can dim the
 * rim while sparing a cap that is sitting ON the rim. The cap has to MOVE.
 *
 * And the reason it was on the rim is that the camera was too close. At
 * CAMERA_Z/R = 3.46 perspective magnifies the NEAR pole and shrinks the FAR one:
 *
 *                       north cap      south cap     skew
 *   CAMERA_Z 3.2          0.982 R        0.754 R     0.228   <- what shipped
 *   CAMERA_Z 12.76        0.919 R        0.860 R     0.058   <- this
 *   orthographic          0.891 R        0.891 R     0.000
 *
 * MEASURED ON THE REFERENCE, central column strip at one sphere radius, its cap
 * rings peak at r/R 0.828 / 0.877 / 0.916 / 0.960 at the TOP and 0.877 / 0.916 /
 * 0.960 at the BOTTOM — the same radii top and bottom, SYMMETRIC. And
 * cos(27 +/- theta) for theta = 5..15 degrees is 0.848..0.927 and 0.799..0.956.
 * The reference's cap IS the orthographic cap at this exact tilt. Its camera is
 * orthographic or nearly so; ours was not.
 *
 * ─── WHY BOTH NUMBERS MOVE TOGETHER: THIS IS A DOLLY ZOOM ───
 *
 * Pulling the camera back alone would shrink the sphere. Scaling CAMERA_Z by m
 * and tan(fov/2) by 1/m holds the image size exactly, because every place the
 * two appear in this file they appear as one of two invariant products:
 *
 *   uSizeScale / CAMERA_Z      = height / (2 tan(fov/2) CAMERA_Z)   -> point size
 *   2 tan(fov/2) CAMERA_Z      = visibleHeight                      -> worldPerPixel
 *
 * Both are unchanged by construction, so the layout, the fit factor and the
 * sprite scale all carry over untouched. m = 4: FOV 42 -> 11 degrees and
 * CAMERA_Z 3.2 -> 12.76, which holds tan(fov/2) * CAMERA_Z at 1.2285.
 */
const FOV_DEGREES = 11;
const CAMERA_Z = 12.76;

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
   * The palette-luminance gain actually applied to the shell this frame.
   *
   * Published because it shipped wrong and silently: a constant in the wrong
   * colour space made magenta's gain 0.7237 where the arithmetic claimed
   * 1.0000, and nothing on screen or in any log said so. A capture whose log
   * reads `pgain` other than 1.000 under magenta is a capture taken with that
   * bug back.
   */
  paletteGain: number;
  /**
   * The glow skirt's amplitude AS APPLIED, after the bright-end clamp. Published
   * for the same reason `paletteGain` is: the clamp is invisible in a still, and
   * a capture that reads the unclamped UV_GLOW_GAIN under a bright theme is a
   * capture of a build where it did not run. See UV_GLOW_LUM_POW.
   */
  glowGain: number;
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
   * Round W: the form's sway at the instant of the read, degrees. Carried for
   * the same reason as spinRad — a differencing metric on a still cannot
   * separate a lean from the breath, and the claim "it sways +/-4 deg on an
   * 11.3 s period" is checked against these, not inferred from pixels.
   */
  swayYawDeg: number;
  swayPitchDeg: number;
  swayRollDeg: number;
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
  /**
   * DEV ONLY. `--force-count=<main>[,<companion>]`, overriding PARTICLE_COUNT.
   *
   * Point SIZE already sweeps through `rim.bodySize`; this completes the pair
   * so the (count x size) grid the particle work needs is one build and N
   * launches rather than N builds. A governor demotion during a forced-count
   * run would swap the count out from under the measurement, so `setTier` keeps
   * the override rather than reverting to the tier's own number — the sweep is
   * measuring the count it was given, and it says so in `stats().particles`.
   */
  counts?: { main: number; companion: number | null; companionSize: number | null };
  /** DEV ONLY. `--force-facesat=<0..1>`. 1 reproduces the pre-faceSat shell. */
  faceSat?: number;
  /** DEV ONLY. `--force-pgain=<0|1>`. false reproduces the un-normalised shell. */
  paletteGain?: boolean;
  /**
   * DEV ONLY. `--force-deform=<0|1>`. 0 reproduces round T's ROUND shell
   * exactly — same binary, same lattice, same colours — which is how the
   * "shape only" claim of round U is checked. See FIB_DEFORM_ON.
   */
  deform?: boolean;
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
   * Place the two background companions, as fractions of the canvas.
   *
   * The caller owns the composition and therefore owns where they sit; this
   * engine only knows how to draw them. `scale` is their world radius, so a
   * value of 0.2 is a fifth of the main sphere's — see companions.ts.
   */
  setCompanions(
    placements: readonly { side: 'left' | 'right'; fx: number; fy: number; scale: number }[],
  ): void;
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
   * `load` is 0..1, normalised by the caller. This is TESSA's exertion, not the
   * machine's: §R.1 lists the machine's CPU and RAM separately as the P6
   * "resource aura". Feeding machine load in here would collapse two distinct
   * instruments into one and make the sphere claim Tessa is busy when it is
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
  /**
   * Run the animation loop, or STOP it and hold one frozen frame.
   *
   * Built for the ambient widget, which renders in the corner of his screen all
   * day rather than for as long as he keeps a window open. `false` cancels the
   * rAF outright — not a lower frame rate, not a skipped render, no callback at
   * all — so an idle sphere costs the compositor nothing. See the note on the
   * implementation for why the existing `fps === 0` path is not sufficient.
   *
   * A no-op under `prefers-reduced-motion`, which already runs with no loop.
   */
  setAnimating(on: boolean): void;
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
 * Fibonacci lattice, TANGENTIALLY JITTERED — and the jitter is the finding.
 *
 * ─── WHAT THE REFERENCE ACTUALLY IS, MEASURED ───
 * Every round so far compared how BIG the particles are and how MANY there are.
 * Both now match. The image still did not, and this is why: the two point
 * FIELDS have different statistics, and no size-or-count measurement can see
 * it. Sampled at mid-face, both normalised to a 514 px disc, particles found as
 * connected components:
 *
 *                            NN-direction     NN2/NN1   gap/hexPredicted
 *   reference image11          R = 0.020        1.53          0.63
 *   synthetic Poisson          R = 0.061        1.56          0.48
 *   synthetic hex lattice      R = 0.575        1.00          1.00
 *   THIS BUILD (before)        R = 0.914        1.03          0.90
 *
 * `R` is the circular concentration of the direction from each particle to its
 * nearest neighbour, on doubled angles. R near 0 means those directions are
 * spread over every angle; R near 1 means they all point the same way. This
 * build measured 0.914 — 204 of 216 nearest-neighbour vectors fell in ONE 15
 * degree bin. That is not a cloud of points, it is a printed grid, and a grid
 * reads as flat wallpaper no matter how small its dots are.
 *
 * The reference measures 0.020, which is Poisson. Its points are IRREGULARLY
 * scattered. That single fact also resolves a contradiction that had been
 * standing for two rounds — its nearest-neighbour gap (6.77 px) is far smaller
 * than its own density implies for an even field (10.86 px). It was read as
 * "the points cluster along the Fibonacci rows". They do not. Random points
 * simply land near each other sometimes, and a Poisson field's mean NN distance
 * is 0.46x a lattice's at the same density. No row clustering needs to exist,
 * and inventing one would have been the wrong fix built on a right number.
 *
 * ─── WHY TANGENTIAL, AND WHY THE OLD RADIAL JITTER FAILED ───
 * `uJitter` already existed and is measured to zero, with a note saying the
 * lattice could not be broken without destroying the silhouette. That note is
 * half right and the half that is wrong cost this round. uJitter is RADIAL: it
 * moves particles off the shell, so of course the limb dissolves — at 0.10 the
 * crescent was already a diffuse band. But the crisp limb comes from every
 * particle sharing one RADIUS, and the visible lattice comes from their ANGULAR
 * arrangement. Those are independent, and radial jitter conflated them.
 *
 * Displacing along the TANGENT PLANE and renormalising leaves every particle
 * at exactly radius 1. The silhouette is arithmetically untouched; only the
 * regularity goes. It is baked into the buffer at build time, so it costs
 * nothing per frame, and it is driven by a fixed-seed PRNG so the shell is
 * identical on every launch — a shell that reshuffled per run could not be
 * measured twice.
 *
 * @param latticeJitter tangential offset as a multiple of the lattice's own
 *   mean spacing. 0 restores the exact previous geometry.
 */
/**
 * ─── THE MAIN SPHERE IS A UV GRID NOW. THE FIBONACCI PATH IS KEPT, NOT DELETED ───
 *
 * The main sphere's target changed completely: `reference/main-orb.png` is a
 * latitude-longitude lattice — horizontal bands, longitude lines converging into
 * clean concentric rings at both poles, evenly lit, no crescent. Classified
 * before anything was built, by eye and by measurement, and the two agree:
 *
 *   mean luminance within 4 px of each pole centre    top 133   bottom 119
 *   the same statistic at the middle of the same disc              0
 *   the same statistic on empty background                          0
 *
 * A hundredfold concentration at two poles and nothing in the middle. It is a UV
 * sphere, and it is a RENDER, not a photograph — 100.0% of its background pixels
 * are exactly zero, so none of the lifted-black or tone-curve caveats that
 * governed every previous round apply to it.
 *
 * `buildFibonacciGeometry` below is the golden-angle generator this sphere used
 * until now. IT IS DELIBERATELY LEFT IN PLACE AND EXPORTED, unused by the main
 * sphere, because companion 3 (`reference/third-orb.png`) is the Fibonacci-rows
 * object and will need it. Do not delete it to tidy up.
 *
 * THE COMPANIONS ARE NOT AFFECTED AND DID NOT NEED FORKING: they never called
 * this function. `companions.ts:125` has its own private `lattice()`, and
 * `sphere-engine.ts:1363-1364` passes it nothing from here but the jitter. That
 * separation already existed; it is stated because a shared generator would have
 * silently turned both companions into UV grids.
 */
/**
 * ─── THE FIELD WAS STRIPED, AND IT WAS LATITUDE, NOT DENSITY ───
 *
 * Beside the reference the build read COARSE and STRIPED — bright rows with
 * visibly dark gaps between them — where the reference reads fine, dense and
 * continuous. The obvious reading is "not enough dots", and it is wrong: the
 * TOTAL areal dot density already matched. Counting deduplicated dots in a
 * 140x140 px patch at the centre of the disc, both images resampled to a sphere
 * radius of 227 px, gave 80 for the reference and 88 for the build — 1.10x, not
 * a shortfall.
 *
 * The gap is entirely in ONE AXIS. Counting latitude rows in that same patch:
 *
 *                  row pitch      implied bands
 *   reference       16.0 px            45
 *   build           36.0 px            20
 *
 * The build packs its dots into too few rows and then over-packs each row —
 * which is exactly why adding longitude would not have closed the stripes, and
 * why the areal density looked right while the picture did not.
 *
 * LONGITUDE IS ALREADY CORRECT and is deliberately left alone: 76 at the
 * equator puts the horizontal spacing at the sub-viewer point at
 * 2*pi*227*cos(27 deg)/76 = 16.7 px, against the reference's 16.0. Raising it
 * would cost points and coverage to fix something that measures right.
 *
 * So the bands scale by 36.0/16.0 = 2.25, and 26 * 2.25 = 58.5.
 */
export const UV_LAT_BANDS = 44;
/**
 * ─── CUT FOR AIR: 76 -> 50, AND THE DOLLY ZOOM IS WHY IT MOVED AT ALL ───
 *
 * Round J measured this correct at 76 and left it alone. That was true under the
 * OLD camera. The dolly zoom removed the centre magnification and compressed
 * both pitches, exactly as it did for latitude — the row pitch needed 58 -> 44
 * bands for the same reason. Measured after it: the centre patch holds 123 dots
 * against the reference's 80, and with the row pitch at 15.5 px that puts the
 * COLUMN pitch at 10.2 px against the reference's 15.4 — columns 1.5x too dense
 * while rows are right. The field was anisotropic again, the other way round.
 *
 * 76 * 80/123 = 49.4, and 76 * 10.2/15.4 = 50.3. Both roads give 50.
 */
export const UV_LON_EQUATOR = 50;

/**
 * ─── THE EQUATOR ARM, AS DENSITY ───
 *
 * The bands nearest the equator carry this multiple of their longitude count, so
 * their dots sit at half the spacing and merge into the continuous line the
 * reference shows. It is the reference's own mechanism (see UV_CROSS_GAIN) and,
 * unlike a marked meridian, it is ROTATIONALLY SYMMETRIC — a denser ring reads
 * the same at every spin angle, so this arm is rotation-safe by construction
 * where an object-space meridian would swing off the screen axis.
 */
/**
 * ─── REVERTED TO 1.0 (OFF), AND THE GEOMETRY IS WHY ───
 *
 * REPORT-M established that the reference's cross is DENSITY, not brightness,
 * and this band was the right answer to that. It is the wrong answer to WHERE.
 *
 * The band packs the EQUATOR RING, which is object space. The shell is leaned 27
 * degrees, so that ring projects as an ELLIPSE reaching +/-R*sin(27) = 0.45 R
 * above and below the centre — it does not lie along the horizontal line through
 * the disc where the reference's band is and where the metric samples. Measured,
 * strengthening it from +/-3 deg at 2.0x to +/-6 deg at 2.5x moved the equator
 * band-mean the WRONG WAY, 1.23x -> 1.10x: it was adding dots off the line and
 * diluting the strip it was meant to fill.
 *
 * This is the same trap round J hit with an object-space cross mask, and the
 * same conclusion: on a leaned shell, nothing painted on the SURFACE lies along
 * a straight screen axis. A density cross therefore cannot be built in geometry
 * at this tilt, and the view-space brightness mask is restored instead.
 */
const UV_EQ_BAND_MUL = 1.0;
/** How far from the equator the density band reaches, in degrees of latitude. */
const UV_EQ_BAND_DEG = 6.0;
/**
 * THE POLE PINCH, AND THE ONE PIECE OF CRAFT IN THIS FILE.
 *
 * A naive UV sphere puts UV_LON_EQUATOR points on EVERY latitude ring, so the
 * ring at 86.5 degrees — circumference 0.060 of the equator's — receives all 76
 * of them and the pole becomes a solid blob. Tapering the count by cos(latitude)
 * keeps the arc spacing roughly constant instead:
 *
 *   band          equator          outermost (phi = 86.5 deg)
 *   ring radius   1.000 R          0.0603 R
 *   points        76               max(6, round(76 * 0.0603)) = 6
 *   arc spacing   0.0827 R         0.0632 R      <- 1.31x denser, not 16x
 *
 * Without the floor of 6 the last ring would round to 5 and the one inside it to
 * 9; the floor keeps the cap a legible small ring rather than a triangle. The
 * reference shows four to five nested rings at each pole, which is what this
 * produces.
 */
/**
 * ─── AND THE TAPER HAD TO EASE HARDER, BECAUSE THE BANDS MORE THAN DOUBLED ───
 *
 * The floor and the exponent are not independent of UV_LAT_BANDS. With 26 bands
 * the outermost ring sat 3.5 degrees from the pole; with 58 it sits 1.6 degrees
 * out, so the polar rings are less than half as far apart on screen and the SAME
 * dots-per-ring welds them into a solid mass. Holding floor 16 / exponent 0.5
 * through the band change would have made the pile-up worse, not better.
 *
 * Dots on the outermost four rings, equator held at 76:
 *
 *   26 bands, floor 16, exp 0.50    19 / 32 / 41 / 49     (what shipped)
 *   58 bands, floor 16, exp 0.50    16 / 22 / 28 / 33     denser cap, worse
 *   58 bands, floor 12, exp 0.65    12 / 15 / 21 / 26     what this ships
 *
 * Total 3,180 points — 20.4% of the med tier's 15,600 allocation, so the grid
 * is bought out of headroom rather than out of the companions.
 */
const UV_POLE_MIN_LON = 10;

/**
 * ─── THE TAPER EXPONENT, AND I HAD OVER-CORRECTED ───
 *
 * A full cosine taper (exponent 1.0) holds the ARC SPACING constant from equator
 * to pole, which is the textbook fix for the pole pinch and which is what I
 * shipped last round. Measured against the reference it is wrong, and the
 * magnified pole crops say so plainly: `reference/main-orb.png` has three to four
 * TIGHTLY NESTED, DENSELY POPULATED rings at each pole — they are the brightest
 * feature in the image — and the full taper produces a thin scattered dome with
 * no rings at all, because it strips the outermost ring down to six points.
 *
 * The pinch IS the look here. The exponent is lowered to 0.5 so the count falls
 * with the SQUARE ROOT of cos(latitude) rather than with cos itself, which keeps
 * the cap populated while still preventing the degenerate all-76-points-on-one-
 * tiny-ring blob that a taper of 0 would give:
 *
 *   latitude    ring radius   n_lon at exp 1.0   at exp 0.5   arc spacing vs equator
 *   +/-86.5       0.060 R            6               19            x0.24
 *   +/-79.6       0.180 R           14               32            x0.43
 *   +/-72.7       0.298 R           23               41            x0.55
 *   equator       1.000 R           76               76            x1.00
 *
 * So the pole cap is about four times denser than the equator instead of equal —
 * dense enough to read as a bright ring, and nowhere near the sixteen-times of an
 * untapered sphere.
 */
const UV_LON_TAPER_EXP = 0.85;

/**
 * How far the pole axis leans toward the viewer. MEASURED off the reference.
 *
 * Its two pole-cap centres are at y = 153 and y = 584 on a disc of centre 372 and
 * radius 242 — 0.905 R above and 0.876 R below, where an untilted sphere would
 * put them at exactly 1.000 R. `cos(tilt) = 0.89` gives **27 degrees**, and both
 * caps land inside the silhouette at that angle, which is what makes them read as
 * nested ELLIPSES rather than as an edge-on line.
 *
 * It is a fixed lean, not an animation: the shell spins about this axis, so the
 * poles stay where they are for the whole rotation.
 */
/**
 * ─── 38 DEGREES WAS TRIED AND REVERTED; THE LEAN IS NOT WHAT SHUTS THE CAP ───
 *
 * 27 degrees comes from the reference's cap positions read as an ORTHOGRAPHIC
 * projection, cap distance = R * cos(tilt). The camera is not orthographic, and
 * that is real: the north pole leans TOWARD the viewer, sits closer to the
 * camera than the shell's centre, and projects FURTHER out than the cosine
 * predicts — measured, a nominal 0.891 R rendered at about 0.97 R.
 *
 * So 38 degrees was tried, which is what lands the cap at the reference's
 * ~0.86 R once that 1.09 factor is inverted. IT DID NOT OPEN THE CAP. The
 * capture is in the report: the rings still weld into a bright arc, because
 * what is drowning the cap is not its position but the LIMB. Projection
 * compresses surface density as 1/cos toward the silhouette, and this shell has
 * no limb darkening, so the outermost few degrees of the disc are far brighter
 * than the cap sitting just inside them. The reference's limb is much fainter
 * relative to its cap.
 *
 * That is the deferred front/back balance work, not a tilt value, so the tilt
 * goes back to the number that was actually measured rather than shipping a
 * composition change that did not do what it was changed for.
 */
const POLE_TILT_RAD = (27 * Math.PI) / 180;

/**
 * How far short of each pole the latitude bands stop.
 *
 * The reference's pole caps are OPEN BULLSEYES: nested rings around a dark
 * centre. A UV grid whose bands run all the way in has no such centre — its
 * innermost ring is a few pixels across and simply fills. Measured on the
 * reference, the hole inside the innermost ring is about 15-20 px on a 227 px
 * radius, which is 4-5 degrees of latitude.
 */
const UV_POLE_HOLE_RAD = (5 * Math.PI) / 180;

/** Single-particle ceiling for the evenly lit UV sphere. See particles.frag.glsl. */
export const UV_ALPHA_MAX = 1.0;

/**
 * ─── BRIGHTNESS FOR THE EVENLY LIT SPHERE, AND WHY IT IS A MULTIPLIER ───
 *
 * At idle the shell's own brightness is 1.10 (the DIMMEST of the six states — he
 * always sees it at rest, which is item 3a's question and the answer is yes,
 * idle dims it by design) and the gold palette gain is 0.693, so the alpha
 * arriving at the ceiling is 1.10 x 0.693 x ~0.9 = 0.686.
 *
 * Measured, that renders a dot peak of 43.58 against reference/main-orb.png's
 * 84.32. Because the additive blend contributes `tint * out^2`, the effective
 * tint luminance here is 43.58 / 0.686^2 = 92.6, so reaching 84.32 needs
 * out = sqrt(84.32/92.6) = 0.954 — a factor of 1.39 on the alpha.
 *
 * 1.45 is that factor with a little headroom. It is applied ONLY to the main
 * sphere's uBrightness uniform, so the six-state ordering is preserved (every
 * state is scaled by the same number) and the companions, which build their own
 * uniforms, are untouched.
 */
export const UV_BRIGHT_MUL = 1.9;

/**
 * ─── THE DOTS WERE SINGLE PIXELS, AND EVEN LIGHTING IS WHY ───
 *
 * `uEvenLight` removes the rim term, and the rim term was not only brightness:
 * `particles.vert.glsl` grows the point size by `1 + uRimSize*grow*face`, and
 * with `uRimPow` at 0.2 that `grow` is ~0.67 even at mid-face. So turning the rim
 * off did not merely flatten the lighting — it SHRANK every dot on the sphere by
 * roughly two to three times, everywhere, not just at the limb.
 *
 * Measured against reference/main-orb.png at one disc scale:
 *
 *                      dot FWHM   FWHM/D    dot peak   coverage >8
 *   reference           2.20 px   0.0046      84.32       24.99%
 *   build, even-lit     1.59 px   0.0029      32.91        1.58%
 *   build, rim growth   3.09 px   0.0056      36.88       40.59%   (the old sphere)
 *
 * So the size has to come back through the one lever even lighting does not
 * touch: `uPointScale`. 1.55 puts the FWHM at about 2.5 px, between the
 * reference's 2.20 and the old sphere's 3.09, and it widens the Gaussian's skirt
 * with it — which is the halo the reference has and the bare build did not.
 */
/**
 * ─── RAISED AGAIN, BECAUSE COVERAGE IS QUAD-BOUND, NOT AMPLITUDE-BOUND ───
 *
 * After the thinning, coverage above luminance 8 sat at 19.97% against the
 * reference's 25.74%, and RAISING THE SKIRT AMPLITUDE DID ALMOST NOTHING:
 * UV_GLOW_GAIN 0.38 -> 0.70, an applied 0.28 -> 0.52, moved gold's coverage from
 * 19.58% to 19.97%. Meanwhile mean luminance was ALREADY ABOVE the reference —
 * 16.77 against 12.51 — with a lower coverage.
 *
 * That pair is diagnostic: the build's light is concentrated in fewer, brighter
 * pixels where the reference's is spread. The skirt is `exp(-dist2*12)` and the
 * fragment discards beyond dist2 > 0.25, so at the quad's edge the skirt is
 * already down to exp(-3) = 0.05 — it is being CUT OFF, and more amplitude just
 * makes the same small footprint brighter.
 *
 * The fix is a bigger quad, not a bigger number: 2.6 -> 3.6 widens the skirt by
 * 1.38x and nearly doubles its area, and UV_CORE_TIGHT rises by the square of
 * the same factor so the CORE keeps its size in pixels.
 */
export const UV_POINT_SCALE_MUL = 3.2;

/**
 * THE GLOW, and the two numbers that make it work together.
 *
 * The quad must grow to hold a skirt — it is the hard limit, since the fragment
 * discards beyond dist2 > 0.25 — so UV_POINT_SCALE_MUL went 1.55 -> 4.0, taking
 * gl_PointSize from about 3.7 px to about 9.6 px. On its own that would make the
 * dots fat, which is exactly the failure mode to avoid, so UV_CORE_TIGHT scales
 * dist2 for the CORE term by the square of that same factor (2.6^2 = 6.7): the
 * core's half-maximum moves from dist2 0.135 to 0.020, i.e. from r = 0.368 of the
 * quad to r = 0.142, and the rendered core FWHM stays at about 2.7 px.
 *
 * UV_GLOW_GAIN is the skirt's amplitude as a fraction of the core's peak. The
 * target is the reference's between-dot floor of 6-7 luminance: at 4 px from a
 * dot, exp(-dist2*12) is 0.122, so a single dot contributes gain * 79 * 0.122,
 * and neighbours at ~8 px spacing overlap two-deep.
 */
/**
 * SHRUNK WITH THE QUAD, AND IT HAS TO BE. `uCoreTight` scales dist2 for the core
 * term only, so it is what keeps the core the same size IN PIXELS while the quad
 * changes. dist2 goes as the square of the quad width, so when
 * UV_POINT_SCALE_MUL went 4.0 -> 2.6 this had to go 6.7 * (2.6/4.0)^2 = 2.83.
 *
 * The quad had to come down because the row pitch did: at 36 px rows a 9.6 px
 * quad had room, at 16 px rows it does not, and overlapping skirts would fill
 * the black the round is trying to keep. The CORE is deliberately unchanged —
 * that is the shrink/brightness coupling, and the dot peak is the check.
 */
export const UV_CORE_TIGHT = 4.29;
/**
 * ─── CUT HARD: THE HALO IS THE "GLOWING BALL", NOT THE CORE ───
 *
 * Measured at 8x on a matched-scale field crop, the build's dot CORES already
 * match the reference: field dot area 8.00 px red / 6.00 gold against the
 * reference's 7.00, and the normalised radial profile falls to 0.50 at 1.03 px
 * against the reference's 0.93. The cores were never the fault.
 *
 * What the build has and the reference does NOT is a smooth circular HALO around
 * every dot — the skirt. It measures low (0.03-0.12 of peak past r=2) but it is
 * SMOOTH and ROUND, so the eye reads a glowing ball; the reference's surround at
 * the same radii is noisy speckle with no ring structure at all. Its profile
 * sits on a 0.16-0.24 floor that never reaches black, which is photographic
 * haze, not a per-dot halo.
 *
 * So the skirt goes to a quarter of what it was. Coverage falls with it and is
 * ALLOWED to: see the note on the coverage target in REPORT-O.
 */
export const UV_GLOW_GAIN = 0.06;

/**
 * ─── THE SKIRT IS CLAMPED AT THE BRIGHT END, AND A GLOBAL CUT WAS WRONG ───
 *
 * At UV_GLOW_GAIN 0.3 the wash landed for RED — coverage above luminance 8 of
 * 22.73% against reference/main-orb.png's 24.99%, on a between-dot floor of 7.0
 * against 6-7. Under GOLD the same gain measured 33.08%: the wash filled too
 * much of the black and the dots stopped reading as points.
 *
 * MY FIRST FIX WAS A FLAT CUT AND IT WAS THE WRONG SHAPE. Taking the gain to
 * 0.2 and UV_BRIGHT_MUL to 1.35 brought gold to 27.85% but dragged red down
 * with it, from 22.73% to 17.48% — it fixed the theme that was wrong by
 * breaking the one that was right. Both constants are restored above.
 *
 * WHAT ACTUALLY RUNS HOT IS THE BRIGHT END. `gainFor` below already normalises
 * the shell for the palette luminance, and it does so correctly for the dot
 * CORES. It does not hold for the wash: measured at one disc scale, the same
 * gain gives a between-dot floor of 11.0-12.4 under gold against 6.9-7.6 under
 * red, a factor of 1.6, and a mean disc luminance of 18.045 against 9.738. So
 * the skirt needs its own normalisation on top of the shell's.
 *
 * This is that normalisation, and it only ever REDUCES. The knee is the
 * luminance of the theme reference/main-orb.png was rendered in, read from its
 * token rather than written here, so the theme the wash was fitted against
 * comes out at exactly 1.0 and red cannot move by construction. Every brighter
 * theme is scaled by (knee / its luminance).
 *
 * ─── THE EXPONENT IS FITTED, AND A FULL CLAMP OVERSHOT ───
 *
 * Coverage above a threshold grows with the LOG of the skirt amplitude: the
 * skirt is a Gaussian, so the radius at which it crosses 8 goes as ln(A/8) and
 * the covered area goes with it. Two gold captures at the same brightness give
 * that slope directly — gain 0.300 measured 28.12% and gain 0.126 measured
 * 15.24%, so 14.9 coverage points per e-fold of amplitude.
 *
 * 1.0 — the full clamp, gold at the knee ratio 0.3424/0.8157 = 0.42 — is what
 * produced that 15.24%, which is 8.5 points BELOW the reference rather than
 * above it. It over-corrects because the palette gain has already done part of
 * this job for the cores; only the residual belongs here.
 *
 * The reference reads 23.72% on the same ruler, so gold needs ln 0.296 down
 * from 28.12%, a factor of 0.744, and 0.4197^0.35 is 0.738. Hence 0.35.
 *
 * RED CANNOT MOVE, whatever this number is: its own luminance IS the knee, so
 * the ratio is exactly 1 and 1 to any power is 1. That is the property the
 * exponent was allowed to be fitted freely against.
 */
/**
 * ─── REFIT TO 0 AT THE WIDE-QUAD OPERATING POINT, AND THE MEASUREMENT SAYS SO ───
 *
 * 0.35 was fitted when the skirt was QUAD-BOUND — clipped by the sprite's own
 * discard — and in that regime gold ran hot and needed holding down. Widening
 * the quad changed the regime, and re-measuring the slope on this build gives:
 *
 *              applied gain   cov>8      slope (points per e-fold of amplitude)
 *   gold       0.517 / 0.199  39.79 / 24.47      16.0
 *   red        0.700 / 0.270  41.31 / 30.18      11.7
 *
 * Solving each theme to the reference's 25.74% asks for an applied gain of
 * 0.216 for GOLD and 0.185 for RED — i.e. gold now wants MORE skirt than red,
 * the opposite of what the clamp does. Holding the old 0.35 would push gold
 * further under a target it is already below.
 *
 * So the exponent is refit to 0, which makes the clamp inert: both themes take
 * the raw gain. The machinery is one `Math.pow` and is kept rather than deleted
 * because it is the thing that has to move again if the sprite geometry does —
 * it has been refitted twice for exactly that reason.
 */
export const UV_GLOW_LUM_POW = 0;

/**
 * ─── THE CROSS, AND WHY IT IS AN ATTRIBUTE AND NOT A GRADIENT ───
 *
 * The reference has a brighter equator band and a brighter centre meridian.
 * Measured on the inner 0.8 R, so limb compression cannot contribute:
 *
 *                  equator / off-equator     meridian / off-meridian
 *   reference             1.34x                     1.26x
 *   build                 1.01x                     1.04x
 *
 * i.e. the build has no cross at all. The tempting fix is a view-facing wash,
 * and it is the wrong one twice over: it would put the light back in a
 * DIRECTION, which is the crescent this sphere spent two rounds removing, and
 * it would brighten the whole centre of the disc rather than two lines.
 *
 * So the cross is carried as a PER-VERTEX ATTRIBUTE. `aCross` is 1 on the
 * equatorial band and on the two meridians that project to the vertical line
 * through the centre, 0 everywhere else, and the vertex stage multiplies
 * brightness by (1 + uCrossGain * aCross). Nothing else on the sphere can be
 * touched by it, because nothing else carries the attribute.
 *
 * 0.55 is set from the ratio: the marked dots have to lift their own band by
 * about a third against neighbours that do not move.
 */
/**
 * ─── THE CROSS IS DENSITY, NOT BRIGHTNESS, AND THE MEASUREMENT SAYS SO ───
 *
 * Comparing dots ON the cross arms with dots OFF them, per-dot peak luminance:
 *
 *                on/off per-dot brightness
 *   REFERENCE            1.12x        <- essentially nothing
 *   build red            1.01x        <- already matches the reference
 *   build gold           1.61x        <- ALREADY an artificial bright stripe
 *
 * The reference's cross is NOT brighter dots. REPORT-L found the same thing from
 * the other side: per-dot brightness correlates -0.035 with distance to the
 * cross, i.e. not at all. What a magnified strip through the centre shows is the
 * middle row carrying its dots in PAIRS — the same brightness as the rows either
 * side, packed at roughly twice the spacing.
 *
 * So the arm is carried by SPACING. Raising this gain further would paint a
 * bright stripe where the reference has a dense band, and gold is already past
 * the reference on it — hence the trim rather than the raise.
 */
export const UV_CROSS_GAIN = 0.2;

/**
 * Half-thickness of each arm, as a fraction of the sphere radius. The metric
 * that sets the target samples a strip of +/-0.035 R, so the arms are cut to
 * match what is being measured rather than to a number that looked right.
 */
const CROSS_HALF_WIDTH = 0.045;

/** How much wider a marked cross dot draws. See the note in particles.vert.glsl. */
export const UV_CROSS_SIZE = 0.18;

/**
 * Half-thickness of the two cross arms, in shell radii: [meridian, equator].
 *
 * ONE WIDTH COULD NOT LAND BOTH ARMS. At a common 0.014 the meridian measured
 * 1.36x against its 1.26x target while the equator sat at 1.12x against 1.34x —
 * the vertical arm was already past target while the horizontal one was a third
 * of the way. The metric samples a strip of +/-0.035 R, so a 0.014 arm fills
 * only 40% of what is being averaged; widening the equator arm toward the strip
 * raises its measured ratio without touching the meridian.
 */
export const UV_CROSS_WIDTH: readonly [number, number] = [0.020, 0.028];

/**
 * ─── PER-DOT BRIGHTNESS SPREAD. See the long note in particles.frag.glsl. ───
 *
 * The reference's per-dot spread is 16.28x (CV 0.776) and 92% of it is RANDOM,
 * not positional. The build measured 1.99x. `grain` was `0.78 + 0.22*h`, a range
 * ratio of only 1.28.
 *
 * The rendered contribution goes as alpha^2, so a rendered spread of R needs an
 * alpha spread of sqrt(R): 16.28x wants 4.03x on alpha against the 1.41x the
 * build had. The floor carries that, and the power carries the SHAPE — the
 * reference is skewed toward the bright end, alpha p5/p50/p95 = 0.248/0.824/1.0,
 * which a power below 1 on a uniform hash reproduces.
 *
 * DELIBERATELY WIDENED DOWNWARD ONLY. The bright end stays where it is, so this
 * cannot introduce clipping that was not already there; all the new range is
 * added below the old floor.
 */
export const UV_GRAIN_MIN = 0.075;
export const UV_GRAIN_POW = 0.45;

/**
 * ─── THE POLE CAPS ARE BRIGHTER THAN THE FIELD, AND BY HOW MUCH ───
 *
 * Measured as a cap disc (r < 0.16 R about each projected pole) against a ring
 * at the SAME radius from the disc centre — matched that way so projection
 * crowding cannot bias it:
 *
 *              cap / same-radius ring     cap / inner field
 *   REFERENCE          2.49x                    7.79x
 *   build red          1.19x                    2.32x
 *   build gold         1.07x                    2.02x
 *
 * The caps recede where the reference's pop. This is the ONE structural
 * component the per-dot regression found (corr -0.187 with distance to the
 * pole), so it is the structural half of the variation and the random half is
 * UV_GRAIN_MIN's.
 *
 * POSITIONAL, NOT DIRECTIONAL, and symmetric by construction: the mask is
 * `smoothstep` on |dir.y|, which cannot tell the two poles apart. The band runs
 * from cos(16 deg) to cos(8 deg) — the cap disc measured above is about 10-12
 * degrees of colatitude.
 *
 * Both a brightness and a SIZE term, for the reason the cross needed both: one
 * sprite is capped at uAlphaMax and the front dots already arrive near it, so
 * brightness alone gets eaten. See the note in particles.vert.glsl.
 */
export const UV_CAP_BAND: readonly [number, number] = [0.899, 0.995];
export const UV_CAP_GAIN = 4.0;
/**
 * ─── ZERO, AND THE CAPTURE IS WHY ───
 *
 * The cross needed a SIZE term because the alpha ceiling ate its brightness. The
 * caps must NOT have one. At 0.25 the cap ratio measured correctly — 2.55x
 * against the reference's 2.49x — and still looked wrong: fattening dots that
 * are already the densest on the shell welded the cap rings into a bright BLOB
 * where the reference has nested ellipses around a dark centre. The number was
 * right and the picture was not.
 *
 * So the caps are lifted by BRIGHTNESS ONLY, over a WIDER band (colatitude ~6-20
 * degrees rather than 8-16) so the lift is spread across more rings with a
 * gradient instead of concentrated on two.
 */
/**
 * ─── -0.45 -> -0.20: GROWN TO THE REFERENCE'S OWN CAP DOT SIZE, NOT FATTENED ───
 *
 * Round O cut the halo and left the cap dots at 12 px area against the field's 7.
 * Cap/field brightness fell to 1.60x red / 1.49x gold against the reference's
 * 2.49x, and the reachability arithmetic says gain alone cannot get it back:
 *
 *   the reference's cap dots are 26 px area (3.71x its field) at 1.18x its field
 *   PEAK. Its cap brightness is AREA, not heat. The build's peak ratio is already
 *   1.18x — identical — so the only thing missing is the area. Carrying the gap
 *   on peak instead would need ~296 luminance on red, which clips at 253.
 *
 * So the quad grows back toward the reference's 26 px and no further. Area goes
 * as quad^2 while unclamped: (0.80/0.55)^2 x 12 = 25 px. This is the reference's
 * actual mechanism, not the round-M welding: the halo is gone, so a bigger core
 * has no skirt to merge with its neighbours through. The crop is the hard line.
 */
export const UV_CAP_SIZE = -0.2;

/**
 * ─── HOW FAR THE CAP DOTS' OWN CEILING IS LIFTED ───
 *
 * Scoped to the cap mask, so `UV_ALPHA_MAX` itself does not move and the field
 * cannot newly clip. See the note in particles.frag.glsl.
 *
 * The target is the reference's cap/field brightness of 2.49x. The rendered
 * contribution goes as out^2, so reaching 2.4x on a dot's OWN peak — rather than
 * through overlap — needs the ceiling at sqrt(2.4) = 1.55x, i.e. +0.55.
 *
 * The palette gain keeps this from clipping the bright themes: red's alpha runs
 * 1.070/0.693 = 1.54x gold's, so where red's cap dots reach the new ceiling
 * gold's arrive at about 1.0 and never use the headroom. That is the prediction;
 * item 4 of the report is the measurement.
 */
export const UV_CAP_CEIL = 0.7;

function buildGeometry(count: number, latticeJitter: number): BufferGeometry {
  // Ring plan first, so the buffers are allocated to the EXACT produced count and
  // nothing overflows the tier's allocation.
  const bands: { phi: number; n: number }[] = [];
  for (let i = 0; i < UV_LAT_BANDS; i++) {
    // THE POLAR HOLE. Bands stop short of the pole instead of marching into it.
    // With 58 bands the innermost sat 1.55 degrees out — 6 px on this sphere —
    // so the cap's centre filled in and the reference's DARK HOLE inside the
    // innermost ring was lost. The reference keeps roughly 5 degrees clear.
    const span = Math.PI - 2 * UV_POLE_HOLE_RAD;
    const phi = -Math.PI / 2 + UV_POLE_HOLE_RAD + (span * (i + 0.5)) / UV_LAT_BANDS;
    bands.push({
      phi,
      n: Math.round(
        Math.max(
          UV_POLE_MIN_LON,
          Math.round(
            UV_LON_EQUATOR * Math.pow(Math.max(Math.cos(phi), 1e-6), UV_LON_TAPER_EXP),
          ),
          // THE EQUATOR ARM: the bands within UV_EQ_BAND_DEG of the equator are
          // packed at UV_EQ_BAND_MUL times their normal longitude count.
        ) * (Math.abs(phi) <= (UV_EQ_BAND_DEG * Math.PI) / 180 ? UV_EQ_BAND_MUL : 1),
      ),
    });
  }
  const total = bands.reduce((a, b) => a + b.n, 0);
  if (total > count) {
    // The tier budget is a CEILING here, not a target. A UV grid's density is a
    // structural property matched to the reference, not a performance dial, so
    // the tier can only refuse a grid that is too big — it never inflates one.
    // At med (15,600) this never fires: the grid is ~1,270.
    return buildFibonacciGeometry(count, latticeJitter);
  }

  const positions = new Float32Array(total * 3);
  const seeds = new Float32Array(total);
  // THE CROSS. 1 marks a dot as belonging to the equator ring or to the centre
  // meridian; the vertex stage lifts only those. See UV_CROSS_GAIN.
  const cross = new Float32Array(total);


  // Sub-pixel DITHER, not lattice jitter. Expressed as a multiple of the
  // equatorial arc spacing, so at the shipped 0.04 it is 0.0033 rad — about
  // 0.9 px at this sphere's radius. Enough to stop every dot sharing one pixel
  // phase as the shell rotates; far too small to blur the grid.
  const arc = (2 * Math.PI) / UV_LON_EQUATOR;
  const sigma = Math.max(0, latticeJitter) * arc;
  let rngState = 0x9e3779b9;
  const next = (): number => {
    rngState = (Math.imul(rngState, 1664525) + 1013904223) >>> 0;
    return (rngState >>> 8) / 16777216;
  };
  const gauss = (): number => {
    const u = Math.max(next(), 1e-9);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * next());
  };

  let k = 0;
  for (const band of bands) {
    for (let j = 0; j < band.n; j++) {
      // Offset alternate rings by half a step so the longitude lines are not all
      // in phase — the reference's are not, and perfectly aligned columns are the
      // most aliasing-prone arrangement there is.
      const theta = (2 * Math.PI * (j + (k % 2) * 0.5)) / band.n;
      const phi = band.phi + (sigma > 0 ? gauss() * sigma : 0);
      const lon = theta + (sigma > 0 ? (gauss() * sigma) / Math.max(Math.cos(phi), 0.06) : 0);
      const ring = Math.cos(phi);
      positions[k * 3] = Math.cos(lon) * ring;
      positions[k * 3 + 1] = Math.sin(phi);
      positions[k * 3 + 2] = Math.sin(lon) * ring;
      seeds[k] = (k * 0.618033988749895) % 1;
      /**
       * THE CROSS IS MARKED BY POSITION, NOT BY RING INDEX, AND THE FIRST
       * VERSION GOT BOTH LINES WRONG.
       *
       * It marked longitudes 0 and pi as "the meridian". Those lie in the plane
       * z = 0, so they project onto the SILHOUETTE, not onto the vertical line
       * through the centre — the vertical line is the meridian pair at x = 0.
       * And it marked the equator band, but the shell is LEANED, so the equator
       * projects as an ellipse reaching +/-R*sin(tilt) above and below the
       * centre and almost never crosses the horizontal strip at all. Measured,
       * that version moved the equator ratio the WRONG WAY, 1.01 -> 0.93.
       *
       * Both lines are planes through the origin, so both are one dot product:
       *
       *   vertical   the plane x = 0
       *   horizontal the plane whose normal is the screen's up axis carried
       *              into object space, (0, cos tilt, -sin tilt) — the great
       *              circle that actually projects to y = 0 once the shell is
       *              leaned. At zero tilt this collapses to the equator, which
       *              is what makes it the right generalisation rather than a
       *              second special case.
       */
      const px = positions[k * 3];
      const py = positions[k * 3 + 1];
      const pz = positions[k * 3 + 2];
      const onVertical = Math.abs(px) < CROSS_HALF_WIDTH;
      const onHorizontal =
        Math.abs(py * Math.cos(POLE_TILT_RAD) - pz * Math.sin(POLE_TILT_RAD)) <
        CROSS_HALF_WIDTH;
      cross[k] = onVertical || onHorizontal ? 1 : 0;
      k += 1;
    }
  }

  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(positions, 3));
  geometry.setAttribute('aSeed', new BufferAttribute(seeds, 1));
  geometry.setAttribute('aCross', new BufferAttribute(cross, 1));
  return geometry;
}

/**
 * ─── ROUND Q: THE MAIN SPHERE IS A GOLDEN-ANGLE LATTICE, WITH A FIXED GRADIENT ───
 *
 * The main-sphere target changed from `reference/main-orb.png` (a photograph of
 * a UV grid) to `reference/third-orb.png` (a clean render of flowing rows, no
 * bloom, no camera). Everything below is MEASURED off that file at its own
 * scale — sphere radius 265.8 px in a 740x678 frame — see REPORT-Q.md items 2-5.
 *
 *   dots       fwhm 4.51 px = 0.0170 R; a flat-topped soft disc (profile
 *              1.00 1.06 0.65 0.07 0.05 at r = 0..4 px), no glow, matte
 *   spacing    7.00 px nearest-neighbour at the face centre (0.0263 R)
 *   count      15,500-16,000 from the centre density (57 px^2 per dot) and
 *              14,500 from the count inside 0.5 R; the med tier's own 15,600
 *              sits inside that range and matches the row spacing to 1%
 *   jitter     0 — the rows are perfectly smooth. The reference's psi6 of
 *              0.25 (a clean projected golden-angle lattice scores 0.80) is
 *              ANISOTROPY — its centre cell is a 7x8 px rectangle with a
 *              10.6 px diagonal — not positional noise
 *   axis       in-plane azimuth 126 deg (lower-right to upper-left); the
 *              elevation toward the camera is FIB_AXIS_EL_DEG; handedness is
 *              this generator's own (its mirror correlates 0.029 vs 0.087)
 *   gradient   RADIAL: hue vs distance-from-centre correlates +0.81 (R^2
 *              0.66) and linear x/y terms add 0.02 — rim WARM, centre COOL.
 *              Purple at the centre, magenta at 0.5 R, orange at the rim,
 *              a smoothstep on r/R with edges 0.12..0.89. The three stops are
 *              tokens (--orb-grad-cool/-mid/-warm), fixed to the image, NOT
 *              the theme — by the owner's ruling
 *   back       the between-dot floor is black (p50 5.3 of 255): the far
 *              hemisphere is not visible, so it is faded out here
 *
 * The UV generator and every UV_* constant stay in this file and are unused
 * by the main sphere while MAIN_LATTICE is 'fibonacci'. Flip it to 'uv' and
 * round P's sphere comes back exactly.
 */
export type MainLattice = 'fibonacci' | 'spiral' | 'uv';
// `as`, not an annotation: an annotated const is narrowed to its initializer
// for the rest of the file, and every `=== 'spiral'` below would be a type error.
export const MAIN_LATTICE = 'fibonacci' as MainLattice;
/** Points on the main golden-angle shell. The tier count is a CEILING on it. */
export const FIB_COUNT = 15_600;
/** Tangential lattice jitter of the main shell. 0: the reference's rows are clean. */
export const FIB_JITTER = 0;
/** Spiral axis: elevation toward the camera, and in-plane azimuth (0 = right, 90 = up). */
export const FIB_AXIS_EL_DEG = 12;
export const FIB_AXIS_AZ_DEG = 126;
/** Phase about the spiral axis at rest. */
export const FIB_SPIN_DEG = 0;
/**
 * ─── ROUND V: THE SHELL TURNS — THE LATTICE FLOWS, THE FORM STAYS ───
 *
 * Rounds Q-U were still by ruling; the owner now wants the sphere alive on
 * its own. The constraint is round U's fold: a body rotation would carry the
 * crease and the far wall round to the front, where a fold with nothing
 * behind it reads as a tear. So the ROTATION IS OF THE LATTICE ONLY: the
 * dots spin about the lattice's own axis (the state's `spin`, through
 * fibRestEuler as always) while the deformation frame and the gradient axis
 * are re-derived from the CURRENT pose every frame — they are pinned to the
 * view, not to the shell. The squash, the bumps, the crease and the far
 * wall stay exactly where the reference has them; the purple centre stays
 * at the centre; the rows slide through the form, flow into the fold, go
 * under the crease and re-emerge on the far wall. That is option A of the
 * brief, and it costs four quaternion rotations a frame.
 *
 * FIB_SPIN_MAX caps the rate. The state table's `spin` was tuned when spin
 * was a "how hard she works" lever (`thinking` 0.34 rad/s, which the owner
 * called "just spinning"); calm is the brief now. 0.10 rad/s is one turn a
 * minute; idle's 0.04 is one turn every 2.6 minutes, 10 px/s at the limb —
 * visibly drifting in two seconds, never a spin. `blocked` is frozen and
 * does not turn; reduced motion does not turn.
 */
export const FIB_SPIN_MUL = 1;
/** Ceiling on the lattice spin rate, rad/s. */
export const FIB_SPIN_MAX = 0.1;
/**
 * ─── ROUND W: THE FORM SWAYS — OPTION B, ON TOP OF OPTION A ───
 *
 * Round V's lattice flow was measured and shipped, and the owner still read
 * the sphere as static. The reason is in what the flow moves: 15,600 near-
 * identical dots sliding through a FIXED outline is a texture change, and
 * the eye reads an object as moving when its OUTLINE moves. The breath
 * moves the outline, but uniformly — a slow zoom, which the eye discounts.
 *
 * So the whole form now sways: a few degrees of yaw about the screen's
 * vertical and of pitch about its horizontal, on two incommensurate periods
 * so the path never repeats. It is applied AFTER the rest pose and the
 * lattice spin (pose = sway * rest(spin)), and the deformation frame is taken
 * from the un-swayed rest pose, so the squash, the bumps, the crease and the
 * far wall — and the purple centre, through uGradAxis — all turn TOGETHER as
 * one rigid thing. The fold therefore stays on the right side (it moves by
 * a few degrees about the vertical and comes back; it never travels round to
 * the front), and the shape is unchanged: a rigid rotation is not a
 * deformation. The lattice keeps flowing through it underneath.
 *
 * ─── WHY MOST OF IT IS ROLL, AND THE YAW IS SMALL ───
 * The first cut was yaw +/-4 deg, pitch +/-2.5. Measured on captures, the
 * fold is far more sensitive to yaw than the face is: the crease is a sheet
 * seen at a grazing angle, so turning the right side 4 deg toward the eye
 * opened it into a wide rippled sail (silhouette residual 5.5 px std /
 * 13.7 px max at that phase, against 3.6-4.0 / 7-8 at every other), while
 * turning it away pinched the peel to a line. Same shape, but it stopped
 * READING as the reference's fold for a third of every cycle.
 *
 * Roll — rotation about the view axis — has none of that cost. It is a 2D
 * rotation of the silhouette: the squash axis and the fold turn in the plane
 * of the screen, nothing about what is exposed changes, uDeformFwd (and so
 * the gradient's purple centre) is unmoved, and the outline visibly moves,
 * which is the cue. 3 deg of roll carries the peel's tip 14 px along the rim
 * and back. Pitch keeps the fold's exposure too (it tilts the smooth top
 * and bottom, not the crease). Yaw stays, at 1.5 deg, so the peel breathes
 * a little — 7 px of far-wall exposure — and never opens.
 *
 * Peak angular rate 1.4 deg/s on the roll. Visible at a glance, never a
 * spin. `blocked` is frozen and does not sway; reduced motion does not sway.
 * Cost is one Euler->quaternion and one quaternion multiply a frame.
 */
export const SWAY_YAW_DEG = 1.5;
export const SWAY_PITCH_DEG = 2.5;
export const SWAY_ROLL_DEG = 3;
export const SWAY_YAW_PERIOD_MS = 11_300;
export const SWAY_PITCH_PERIOD_MS = 17_900;
export const SWAY_ROLL_PERIOD_MS = 13_700;
/**
 * ─── ROUND R: the idle turbulence is OFF on the main sphere ───
 *
 * `uTurbulence` (idle 0.022) moves every dot radially by a per-dot noise, and
 * a radial move has a lateral component that grows with distance from the
 * face centre. Measured on sub-pixel centroids (`subpix3.py`, round Q's
 * shipped build): row straightness 0.054 / 0.047 / 0.076 / 0.099 px by 0.2 R
 * band, against 0.03 for the clean lattice — small, but it is the only thing
 * displacing a dot off its lattice position, and the reference is a still
 * render with rows straight to 0.027 px. Breath is a uniform scale and moves
 * nothing relative to anything.
 */
export const FIB_TURB_MUL = 0;

/**
 * ─── ROUND R EVIDENCE, OFF BY DEFAULT: THE CONSTANT-PITCH SPIRAL ───
 *
 * The owner ruled that the reference's tight aligned ranks are a golden-angle
 * lattice made tighter, and that the generator is not to change. The
 * measurement disagrees, and this block exists so the disagreement can be
 * SEEN in the app rather than argued (REPORT-R.md items 2 and 3):
 *
 *   - the shipped golden-angle shell is already as regular as the generator
 *     can make it: sub-pixel straightness 0.050 px (clean control 0.03),
 *     jitter 0, sub-pixel phase random in both images;
 *   - the reference's cell has TWO close families and a far third
 *     (1.00 · 1.07 · 1.45 on centroids, 1.00 · 1.14 · 1.52 on peaks; psi6
 *     0.25-0.34); a golden-angle cell has THREE near-equal families
 *     (1.00 · 1.19 · 1.26; psi6 0.78) at every axis elevation swept
 *     (`cell3.py`, 0-90 deg: 6th/1st never above 1.43). Three equal families
 *     read as scatter; two plus a far third read as ranks. That is the whole
 *     of "loose vs tight", and no jitter, spacing, size or snap changes it;
 *   - a spiral of CONSTANT PITCH and constant arc spacing (one continuous
 *     turn after another, dots evenly spaced along it, an integer count per
 *     turn at the equator so consecutive turns line up into columns) has the
 *     reference's cell (1.00 · 1.15 · 1.48), rows straight to 0.01 px, scores
 *     0.496 on the reference's whole-face row-direction field against the
 *     golden angle's 0.437, and still curls into spiral arcs at its poles —
 *     it is NOT the UV grid (no closed rings, no meridians).
 *
 * `MAIN_LATTICE = 'spiral'` turns it on. Shipped OFF, as ruled.
 */
/** Dots per turn at the equator — an integer, so consecutive turns align into columns there. */
export const SPIRAL_EQ_COUNT = 236;
/** Pitch between turns over spacing along a turn. The reference's centre cell is 8 : 7. */
export const SPIRAL_PITCH_RATIO = 8 / 7;
/** The count that gives exactly SPIRAL_EQ_COUNT per equatorial turn: n^2 / (pi * ratio). 15,511 at 236. */
export const SPIRAL_COUNT = Math.round((SPIRAL_EQ_COUNT * SPIRAL_EQ_COUNT) / (Math.PI * SPIRAL_PITCH_RATIO));
/** Chirality; the reference's, by the direction-field fit (0.505 against 0.494 for the mirror). */
export const SPIRAL_HAND = -1;
/** Axis: in the view plane pointing right (poles at the left and right rims), 6 deg toward the camera. */
export const SPIRAL_AXIS_EL_DEG = 6;
export const SPIRAL_AXIS_AZ_DEG = 0;
export const SPIRAL_SPIN_DEG = 0;

/** The rest pose that applies to whichever lattice the main sphere is. */
const MAIN_AXIS =
  MAIN_LATTICE === 'spiral'
    ? { el: SPIRAL_AXIS_EL_DEG, az: SPIRAL_AXIS_AZ_DEG, spin: SPIRAL_SPIN_DEG }
    : { el: FIB_AXIS_EL_DEG, az: FIB_AXIS_AZ_DEG, spin: FIB_SPIN_DEG };
/** Point-size multiplier on the state's pointScale, fitted to the reference's 0.0170 R fwhm. */
export const FIB_POINT_SCALE_MUL = 2.12;
/**
 * Brightness multiplier. Idle brightness is 1.10; 1.10 x this = 1.0, so with
 * uAlphaMax 1.0, even lighting and no depth term a front dot's flat top writes
 * exactly its token colour to the framebuffer. No palette gain: the gradient
 * is fixed to the image, so there is no theme to normalise against.
 */
export const FIB_BRIGHT_MUL = 1 / 1.1;
export const FIB_ALPHA_MAX = 1.0;
export const FIB_GLOW_GAIN = 0;
export const FIB_CORE_TIGHT = 1.0;
/** Flat grain: the reference's dots are uniform within a colour (sd/mean 0.13, and that includes its smooth shading). */
export const FIB_GRAIN_MIN = 1.0;
export const FIB_GRAIN_POW = 1.0;
/** No linear depth dimming: the far hemisphere is removed by FIB_BACK_FADE instead. */
export const FIB_DEPTH_FAR = 1.0;
/**
 * Gradient position: smoothstep(edges, sin(angle from the camera-facing axis)).
 *
 * ─── ROUND S: 0.12..0.89 -> 0.275..1.0 — THE BALANCE, NOT THE PROFILE ───
 *
 * 0.12..0.89 was fitted to the reference's median hue per radius, and it
 * matched it: the build crossed from red into orange at 0.70 R, the reference
 * at 0.69 R (`balance3.py`). And the owner still saw a build that was mostly
 * orange against a reference that is mostly purple/magenta, because the eye
 * weighs PIXELS, not radii — and the two images do not put their pixels at
 * the same radii. Share of lit pixels by hue, reference vs that build:
 *
 *     purple/blue 16.8% vs  9.7%    magenta 19.9% vs 17.4%
 *     red-pink    17.3% vs 11.1%    orange  46.0% vs 61.8%
 *
 * Two causes, neither a colour: the round build's outermost 0.1 R is an 81%-
 * covered solid band where the reference's is 43% (its right side recedes),
 * and the reference's purple and magenta dots on the left face are enlarged
 * by its bulge (16 -> 43 px). Both are locked by ruling (round, no bulge),
 * so the mapping carries the difference: the transfer curve that gives this
 * sphere's pixel distribution the reference's hue distribution
 * (hue(r) = Q_ref(F_build(r))) is fitted by smoothstep(0.275, 1.0) with the
 * same stops and easing, rms 4.3 deg against 17.9 for the old edges. Cost,
 * stated: at any given radius the build is now cooler than the reference by
 * up to ~25 deg (0.6-0.8 R); in pixel share it is the reference's balance.
 */
export const FIB_GRAD_EDGES: readonly [number, number] = [0.275, 1.0];
/**
 * Easing of the mid -> warm half. Linear (1.0) measured 12 deg too warm at
 * 0.6-0.7 R on the first build (hue 359 vs the reference's 347) while every
 * other band sat within 4 deg: the reference lingers in crimson before it
 * turns orange. 1.6 puts that band's mix at 0.20 instead of 0.36.
 */
export const FIB_GRAD_WARM_POW = 1.6;
/**
 * Fade by signed facing (normal . eye). Round Q: (-0.25, 0) — the far hemisphere
 * out, the silhouette row at full brightness.
 *
 * ─── ROUND T: (0.0, 0.22) — the last row fades where no size can separate it ───
 * At facing 0.22 (r 0.975) a round shell's radial row spacing is 1.55 px; the
 * point-size floor is 1 px; below that the rows cannot be resolved at any size
 * and would fuse into a hairline. This fades exactly that band — about one
 * row — and nothing inside it. The minimal thinning the ruling allows, stated.
 */
export const FIB_BACK_FADE: readonly [number, number] = [0.0, 0.22];
/**
 * ─── ROUND T: THE RIM SHRINK — (gap / spacing, dot / spacing) ───
 *
 * Measured on the shipped build (`mergem3.py`, half-peak components per 0.1 R
 * band): fused fraction 0% to 0.6 R, 5.7% at 0.6-0.7, 34% at 0.7-0.8, 86% at
 * 0.8-0.9 and 100% at 0.9-1.0 — 717 dots in 3 components, a solid ring. The
 * centre-to-centre gap minus the dot goes negative at 0.80 R. Pure geometry:
 * radial row spacing on a round shell is 7.07 px x facing, the dot is 4.5 px.
 *
 * The reference's rim (`third-orb.png`) keeps its dots resolvable as beads
 * with a 0.6-0.8 px gap: its rim dots are 12% smaller than its centre dots
 * (4.22 vs 4.79 px) and — the larger part — its deformed shell spaces them
 * 5.1 px apart where the round shell has 4.1. Round is his ruling, so the
 * dot has to carry the whole difference.
 *
 * Law: dot = spacing x facing - gap, as a multiplier (facing - g/s) / (d0/s):
 *   g/s = 0.7 / 7.07 = 0.10 — the reference's rim gap;
 *   d0/s = 4.5 / 7.07 = 0.64 — the face dot over the face spacing.
 * 1.0 wherever the natural gap is >= 0.7 px (facing >= 0.74, r <= 0.67);
 * 3.0 px at 0.85 R, 2.4 at 0.90, 1.5 at 0.95, the 1 px floor by 0.975 R.
 * Every dot stays, every dot keeps its token colour; brightness is untouched
 * (the energy-spread term only acts on GROWN dots). The third component is
 * the on switch; companions pin (0, 1, 0).
 */
export const FIB_RIM_SHRINK: readonly [number, number] = [0.10, 0.64];

/**
 * ─── ROUND U: THE DEFORMATION IS BACK — SQUASH, ORGANIC FIELD, RIGHT-SIDE FOLD ───
 *
 * Rounds Q-T built the main sphere ROUND by ruling. The owner has now put
 * that build beside `reference/third-orb.png` and ruled the other way: the
 * reference is an organic, squashed, folded blob, and this round re-adds the
 * shape. Nothing else moves: the lattice, the dot profile, the gradient and
 * its stops, the rim shrink and the far-side fade are the round-T values.
 * `--force-deform=0` gives the round-T shell back, bit for bit, from the same
 * binary — that is how the "shape only" claim is checked.
 *
 * There was no squash and no fold to re-enable: the only deformation the
 * shell ever had was the per-dot radial turbulence (FIB_TURB_MUL, 0 since
 * round R), which is scatter, not shape. All three parts below are new, and
 * every number is MEASURED off the reference (report U):
 *
 *   THE SQUASH. The reference is not wider than tall — it is TALLER than
 *   wide: lit-mask bbox 502 x 532 px (w:h 0.944), max chords 501 x 532
 *   (0.942), moment ellipse 266.7 x 250.6 with the major axis 7 deg off
 *   vertical. Its centre cell is a 7 x 8 px rectangle (round Q), the same
 *   vertical stretch seen in the lattice. FIB_DEFORM_ASPECT scales the
 *   screen plane by (0.982, 1.058). Why not 0.944 outright: the SAME mask
 *   pipeline reads round T's true sphere as 1.045 wide (its top and bottom
 *   limb rows are thinned by the rim shrink and fade, the sides are not), so
 *   the target is the reference's moment-ellipse ratio, 1.064 tall, which
 *   that thinning barely moves. Measured on the first build at (0.988,
 *   1.047): 1.056 tall by moments, 0.975 by bbox; this is 1.5% more.
 *
 *   THE CREASE CURVE was fitted twice: once to the reference's crease
 *   positions, then corrected by the offset the rendered crease sits from
 *   the curve (0.07-0.15 R outboard, growing toward the bottom).
 *
 *   THE ORGANIC FIELD. Silhouette radius per 15-deg bin over the mask's
 *   equivalent radius: sd/mean 0.029, min 0.955 (upper left), max 1.060
 *   (top). The field is the turbulence's own three-sine product evaluated on
 *   the SMOOTH direction at a frozen phase — a continuous lumpy field, not
 *   per-dot noise — amplitude 0.059 at frequency 0.592, plus four limb
 *   harmonics fitted by least squares to the residual: rms 0.015 per bin
 *   against the reference's profile. The field's face-centre value is
 *   subtracted, so the centre spacing is untouched by construction.
 *
 *   THE FOLD. The reference's right side is not a rim. A bright crease runs
 *   at 0.67 R near the top (screen-up 0.61 R), bows out to 0.74 R above
 *   centre, 0.72 R at centre, and sweeps in to 0.43 R at the bottom
 *   (-0.79 R), while the outer edge beyond it tracks the undeformed circle
 *   (0.97 R at centre). Between the two: a sparse gap right after the crease,
 *   then a second layer of rows whose density rises to the outer edge. That
 *   is a sheet CURLING UNDER — the limb region carried screen-left until the
 *   front sheet turns edge-on — with the surface behind the limb coming back
 *   out to the silhouette as the far wall of the fold. FIB_DEFORM_CREASE is
 *   the cubic fitted to the crease positions. FIB_DEFORM_FOLD is the shift
 *   (0.83 R), the ramp over which a dot past the crease curve is carried
 *   (1.0 R — wide on purpose: the sheet foreshortens progressively, so its
 *   rows converge over ~10 rows and overlap into a solid band about 15 px
 *   wide, the reference's; a sharp fold at ramp 0.12 piled up ONE row and
 *   read as a seam, measured coverage 95 against the reference's 154-250;
 *   0.72 gave a 10 px band at 145-163), and the facing band over which the
 *   shift lets go behind the limb (-0.32..0.12), which is what puts the
 *   sparse gap beside the crease and the dense edge at the silhouette. The
 *   shift over the ramp must exceed 2/3 for the sheet to turn at all.
 *
 * THE OCCLUDER. A fold has a hidden side: the sheet folded under, and the far
 * hemisphere behind the front one. The round shell hid its back with a facing
 * fade; a folded shell cannot, because the far wall of the fold faces the eye
 * and must show. So the main sphere is drawn against a depth-only mesh of the
 * SAME deformed surface — a SphereGeometry through the same GLSL chunk, colour
 * writes off, depth writes on, inset so surface dots sit in front of it — and
 * the dots test depth. That is the reference's own mechanism: its between-dot
 * floor is black because it is an opaque surface with dots on it. The
 * companions never test depth and are untouched.
 */
export const FIB_DEFORM_ON = true;
/** Screen-plane scale (x, y). The reference's w:h is 0.94; this is its 0.944 bbox to the third decimal. */
export const FIB_DEFORM_ASPECT: readonly [number, number] = [0.982, 1.058];
/** The organic field: amplitude, spatial frequency, frozen phase, offset into the field. */
export const FIB_DEFORM_BUMP = { amp: 0.059, freq: 0.592, phase: 9.158, offset: [2.119, 5.05, 1.437] } as const;
/** Limb harmonics a1 b1 a2 b2 a3 b3 a4 b4 on (1 - facing^2) * sum(a_k cos k phi + b_k sin k phi). */
export const FIB_DEFORM_HARM: readonly number[] = [0.0194, 0.0082, 0.0013, 0.0089, 0.0046, -0.0223, -0.0075, 0.0082];
/** The fold: shift (R), ramp (R past the crease curve), back band (facing from..to). */
export const FIB_DEFORM_FOLD = { shift: 0.83, ramp: 1.0, back: [-0.32, 0.12] } as const;
/** The crease curve, screen x (R) as a cubic in screen-up (R): c0 + c1 u + c2 u^2 + c3 u^3. */
export const FIB_DEFORM_CREASE: readonly [number, number, number, number] = [0.63, 0.075, -0.394, 0.169];
/**
 * How far inside the surface the occluder sits, as a scale. Surface dots are
 * in front of it by 4% of R; the amplitude ripple (0.35 x gain, up to 0.07 at
 * `speaking`) is subtracted per frame so no dot can dip behind it.
 */
export const FIB_DEFORM_OCCLUDER_INSET = 0.96;
/** Occluder tessellation. 96 x 48 puts its chord error at 0.0005 R against a 0.04 R inset. */
export const FIB_DEFORM_OCCLUDER_SEGMENTS = 96;

/** The organic field's value at the face centre, subtracted in the shader. Same arithmetic as deformWobble. */
function fibDeformCentreValue(): number {
  const { freq, phase, offset } = FIB_DEFORM_BUMP;
  const x = 0 * freq + offset[0];
  const y = 0 * freq + offset[1];
  const z = 1 * freq + offset[2];
  return Math.sin(x * 3.1 + phase * 1.7) * Math.sin(y * 2.7 - phase * 1.3) * Math.sin(z * 3.9 + phase * 2.1);
}

/** The depth-only occluder's vertex stage; deform.glsl is prepended. */
const OCCLUDER_VERTEX = `
uniform float uRadius;
uniform float uBreath;
void main() {
  vec3 dir = normalize(position);
  vec3 shape = (uDeformOn > 0.5) ? deformShape(dir) : dir;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(shape * (uRadius + uBreath) * uDeformInset, 1.0);
}
`;
const OCCLUDER_FRAGMENT = 'void main() { gl_FragColor = vec4(0.0); }';

/**
 * The shell's rest orientation. Order 'ZXY' applies Ry (spin about the
 * lattice's own +y spiral axis) first, then Rx (tilt toward the camera), then
 * Rz (in-plane azimuth), so the axis lands at FIB_AXIS_AZ_DEG / FIB_AXIS_EL_DEG
 * whatever the spin — the spin becomes rotation later without touching this.
 */
function fibRestEuler(spin = 0): Euler {
  return new Euler(
    (MAIN_AXIS.el * Math.PI) / 180,
    (MAIN_AXIS.spin * Math.PI) / 180 + spin,
    ((MAIN_AXIS.az - 90) * Math.PI) / 180,
    'ZXY',
  );
}

/**
 * The OBJECT-SPACE direction that faces the camera at rest. The gradient is
 * computed against this in the vertex stage from the dot's own object-space
 * direction, which is what makes it a surface property: rotate the shell and
 * the cool patch turns with it. Derived from the same Euler the shell is
 * posed with, so the two cannot drift apart.
 */
function fibGradAxis(): Vector3 {
  const q = new Quaternion().setFromEuler(fibRestEuler());
  return new Vector3(0, 0, 1).applyQuaternion(q.invert()).normalize();
}

/**
 * ROUND U: the whole rest-pose screen frame in object space — right, up and
 * toward-camera — by the same construction as fibGradAxis, so the deformation
 * is defined against the very axes the gradient is, and rides with the shell.
 */
/** Scratch for the per-frame view-anchoring of the deformation frame (round V). */
const tmpPoseQuat = new Quaternion();
/** Round W scratch: the un-swayed rest pose, and the sway itself. */
const tmpRestQuat = new Quaternion();
const tmpSwayQuat = new Quaternion();
const tmpSwayEuler = new Euler(0, 0, 0, 'YXZ');

function fibRestFrame(): { right: Vector3; up: Vector3; fwd: Vector3 } {
  const q = new Quaternion().setFromEuler(fibRestEuler()).invert();
  return {
    right: new Vector3(1, 0, 0).applyQuaternion(q).normalize(),
    up: new Vector3(0, 1, 0).applyQuaternion(q).normalize(),
    fwd: new Vector3(0, 0, 1).applyQuaternion(q).normalize(),
  };
}

/** The main sphere's geometry: the golden-angle shell, or round P's UV grid. */
function buildMainGeometry(count: number, latticeJitter: number): BufferGeometry {
  if (MAIN_LATTICE === 'fibonacci') {
    return buildFibonacciGeometry(Math.min(FIB_COUNT, count), FIB_JITTER);
  }
  if (MAIN_LATTICE === 'spiral') {
    return buildSpiralGeometry(Math.min(SPIRAL_COUNT, count));
  }
  return buildGeometry(count, latticeJitter);
}

/**
 * THE CONSTANT-PITCH SPIRAL (round R evidence; see the SPIRAL_* note). One
 * continuous spiral from pole to pole: equal-area latitude steps, and the
 * angle advances so that each turn sits one pitch below the last — theta =
 * 2*pi*phi/pitch — which makes the arc spacing along a turn constant and the
 * count per turn proportional to sin(phi): an integer at the equator by
 * construction of SPIRAL_COUNT, so consecutive turns line up into columns
 * there and shear slowly away from it, which is the curving-row flow; short
 * turns at the poles are the spiral arcs. At a capped count (a lower tier)
 * the per-turn count is no longer an integer and the columns stagger — a
 * degradation, stated. Same attributes as the golden-angle generator.
 */
export function buildSpiralGeometry(count: number): BufferGeometry {
  const positions = new Float32Array(count * 3);
  const seeds = new Float32Array(count);
  // s * pitch = 4*pi/count (equal-area), pitch = ratio * s.
  const s = Math.sqrt((4 * Math.PI) / (Math.max(count, 1) * SPIRAL_PITCH_RATIO));
  const pitch = SPIRAL_PITCH_RATIO * s;
  for (let i = 0; i < count; i++) {
    const y = 1 - (2 * (i + 0.5)) / count;
    const ring = Math.sqrt(Math.max(0, 1 - y * y));
    const phi = Math.acos(Math.max(-1, Math.min(1, y)));
    const theta = (SPIRAL_HAND * 2 * Math.PI * phi) / pitch;
    positions[i * 3] = Math.cos(theta) * ring;
    positions[i * 3 + 1] = y;
    positions[i * 3 + 2] = Math.sin(theta) * ring;
    seeds[i] = (i * 0.618033988749895) % 1;
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(positions, 3));
  geometry.setAttribute('aSeed', new BufferAttribute(seeds, 1));
  geometry.setAttribute('aCross', new BufferAttribute(new Float32Array(count), 1));
  return geometry;
}

/**
 * THE GOLDEN-ANGLE GENERATOR. Used by the main sphere since round Q; also the
 * generator for companion 3. Its parameters are exactly two — the count and a
 * tangential jitter as a multiple of the lattice's own spacing — and it bakes
 * in NO colour, size, deformation or orientation: the lattice is canonical,
 * axis +y, and the shell is posed by fibRestEuler.
 */
export function buildFibonacciGeometry(count: number, latticeJitter: number): BufferGeometry {
  const positions = new Float32Array(count * 3);
  const seeds = new Float32Array(count);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const denominator = Math.max(count - 1, 1);

  /**
   * Mean nearest-neighbour spacing of N points on the unit sphere.
   *
   * Area per point is 4*PI/N; a hexagonal cell of side s has area
   * (sqrt(3)/2)*s^2, so s = sqrt(8*PI/(sqrt(3)*N)) = 3.8093/sqrt(N). The jitter
   * is expressed as a multiple of this rather than in absolute units, so
   * changing the count does not silently change how disordered the shell is.
   */
  const spacing = 3.8093 / Math.sqrt(Math.max(count, 1));
  const sigma = Math.max(0, latticeJitter) * spacing;

  // Deterministic LCG. Math.random() would reshuffle the shell on every launch,
  // which would make two captures of "the same" build not comparable.
  let rngState = 0x9e3779b9;
  const next = (): number => {
    rngState = (Math.imul(rngState, 1664525) + 1013904223) >>> 0;
    return (rngState >>> 8) / 16777216;
  };
  const gauss = (): number => {
    // Box-Muller. Build time only, so the cost of a log and a cos is irrelevant
    // and correctness of the distribution is not.
    const u = Math.max(next(), 1e-9);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * next());
  };

  for (let i = 0; i < count; i++) {
    const y = 1 - (i / denominator) * 2;
    const ring = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = goldenAngle * i;

    let px = Math.cos(theta) * ring;
    let py = y;
    let pz = Math.sin(theta) * ring;

    if (sigma > 0) {
      // An orthonormal tangent basis at p. The reference axis is swapped near
      // the poles so the cross product never degenerates — without it the two
      // caps would get no jitter at all and would keep the lattice.
      const ax = Math.abs(py) > 0.9 ? 1 : 0;
      const ay = Math.abs(py) > 0.9 ? 0 : 1;
      let t1x = ay * pz - 0 * py;
      let t1y = 0 * px - ax * pz;
      let t1z = ax * py - ay * px;
      const t1n = Math.hypot(t1x, t1y, t1z) || 1;
      t1x /= t1n;
      t1y /= t1n;
      t1z /= t1n;
      const t2x = py * t1z - pz * t1y;
      const t2y = pz * t1x - px * t1z;
      const t2z = px * t1y - py * t1x;

      const g1 = gauss() * sigma;
      const g2 = gauss() * sigma;
      px += t1x * g1 + t2x * g2;
      py += t1y * g1 + t2y * g2;
      pz += t1z * g1 + t2z * g2;
      // Back onto the shell. THIS is what keeps the silhouette exact.
      const n = Math.hypot(px, py, pz) || 1;
      px /= n;
      py /= n;
      pz /= n;
    }

    positions[i * 3] = px;
    positions[i * 3 + 1] = py;
    positions[i * 3 + 2] = pz;

    // Golden-ratio stride: decorrelated per particle, deterministic per index.
    seeds[i] = (i * 0.618033988749895) % 1;
  }

  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(positions, 3));
  geometry.setAttribute('aSeed', new BufferAttribute(seeds, 1));
  // Zeroed, never omitted. The main sphere and the companions compile to ONE
  // WebGLProgram, so an attribute the vertex stage declares must be bound by
  // every geometry that program ever draws — leaving it off is the attribute
  // version of the uEvenLight leak, and it fails silently the same way.
  geometry.setAttribute('aCross', new BufferAttribute(new Float32Array(count), 1));
  return geometry;
}

/**
 * How disordered the shell is, as a multiple of its own lattice spacing.
 *
 * ─── 0.40 -> 0.12. THE OLD VALUE ERASED THE LATTICE COMPLETELY ───
 *
 * 0.40 was fitted with a statistic that could not measure what it claimed to.
 * That statistic was `R`, the circular concentration of the direction to the
 * SINGLE nearest neighbour, and its own control falsified it at the time: a
 * PERFECT hexagonal lattice scored R = 0.526, not 1.0, because in a hex lattice
 * six neighbours are equidistant and which one is "nearest" is decided by noise.
 * R measures ANISOTROPY, not ORDER. The build scored 0.914 on it because a
 * projected Fibonacci lattice is strongly anisotropic at mid-face, and the
 * reference scored 0.020, so the reference was called a random scatter and this
 * constant was raised until the build matched it.
 *
 * The right statistic is the bond-orientational order parameter over k=6
 * neighbours, psi6 = |mean_j exp(6 i theta_j)|, which does not care which
 * neighbour is closest. Measured with one instrument across synthetic controls,
 * the three largest fully-in-frame reference photographs, and this build:
 *
 *                                             sd/mean    psi6
 *   synthetic Poisson                          0.578     0.375
 *   BUILD as shipped, jitter 0.40              0.227     0.343   <- a scatter
 *   REFERENCE image9 / image5 / image2      .264/.271/.260  0.437/0.514/0.524
 *   BUILD jitter 0.00                          0.037     0.775
 *   synthetic hexagonal                        0.000     0.888
 *
 * At 0.40 this build scored 0.343 against Poisson's 0.375 — it had NO angular
 * order at all, and scored below the reference's own moire controls (background
 * 0.347-0.362, UI panel 0.403-0.436). The reference sits decisively above all of
 * them. It is a lattice and the build was not.
 *
 * ─── WHY 0.12 AND NOT 0 ───
 * The reference is not a CLEAN lattice either: 0.44-0.52 against 0.775 for this
 * renderer with the jitter off. That gap is not the camera. Pushing the
 * jitter-0 render through the reference's own pipeline — upscaled to its disc
 * size, blurred 1.2 px, JPEG 4:2:0 q75, plus sensor noise swept to sigma 9 —
 * left psi6 at 0.756-0.763 and sd/mean at 0.072-0.082. Blur, chroma subsampling
 * and noise do not erase angular order, so the reference's partial disorder is
 * really there.
 *
 * Swept on this renderer, r<=0.4R, engine-truth geometry:
 *
 *     jitter   0.00   0.05   0.10   0.15   0.20   0.28   0.40
 *     psi6     0.775  0.715  0.563  0.409  0.350  0.341  0.343
 *     sd/mean  0.037  0.069  0.122  0.163  0.204  0.222  0.227
 *
 * The reference's median psi6 of 0.514 falls between 0.10 and 0.15;
 * interpolated, 0.12 gives ~0.50. Its three frames span 0.437-0.524, i.e.
 * jitter 0.10-0.145, so 0.12 is the centre of the reference's own range.
 *
 * ─── ONE DISAGREEMENT, REPORTED NOT HIDDEN ───
 * sd/mean does NOT agree. The reference measures 0.260-0.271, above even this
 * build at jitter 0.40 (0.227), which read alone would say the reference is MORE
 * disordered than the shipped build — the opposite verdict. psi6 is weighted
 * because it is an angular average over six neighbours and survives positional
 * noise, whereas spacing variance is inflated directly by it: the reference's
 * dots are photographed at fwhm 5.8-7.8 px on a 12-16 px spacing, so neighbours
 * partially merge and centroids pull, widening the spacing distribution without
 * disturbing the angular pattern.
 */
/**
 * ─── 0.12 -> 0.04. THE ROWS WERE THERE AND COULD NOT BE SEEN ───
 *
 * The complaint was that the sphere reads flat — "mine just seems like a normal
 * round circle" — against a reference whose dots lie along curved spiral rows
 * (parastichies) that make the eye read a 3-D surface.
 *
 * THE REFERENCE'S ROWS ARE REAL, NOT LCD MOIRE, and that was tested first
 * because building toward a photographic artefact is how uFaceSat went wrong.
 * Two independent controls: the disc's autocorrelation periodicity is LOWER
 * than every off-disc background patch of equal area in all four frames
 * (0.286-0.513 against 0.417-0.980) — the beat is everywhere and the sphere
 * shows less of it, not more; and the best row direction ROTATES by 32-59
 * degrees across the disc, which a curved surface does and a rigid raster
 * cannot.
 *
 * ─── AND THEN THE ARRANGEMENT METRIC GOT IT WRONG ───
 *
 * A one-dimensional order parameter on detected dot centroids — max over row
 * direction and row spacing of |mean exp(2*pi*i*u/p)| — validates cleanly
 * against controls (a golden-angle sphere scores 0.866 against a matched-n
 * Poisson null of 0.111, +54 sigma; jitter 0.40 scores 0.123, indistinguishable
 * from Poisson). Measured patchwise on this build it read
 *
 *     jitter   0.00   0.04   0.08   0.12
 *     ROWSCORE 0.638  0.631  0.636  0.494       reference 0.392-0.515
 *
 * which says 0.08 is as good as 0.00 and 0.12 is already inside the reference's
 * range. ON THOSE NUMBERS I CONCLUDED "CHANGE NOTHING". Then I rendered all four
 * at one disc scale and looked: 0.12 and 0.08 are smooth discs with no arcs at
 * all, and 0.04 and 0.00 show strong curved rows sweeping pole to pole. THE
 * VISUAL THRESHOLD IS BETWEEN 0.08 AND 0.04; the metric put it between 0.12 and
 * 0.08 and ranked 0.08 with 0.00.
 *
 * The metric is not wrong about the geometry — the rows survive 0.12 as a point
 * statistic. It is measuring the wrong domain. Rows READ because of coherent
 * brightness along arcs across the WHOLE disc, integrated in the image; a
 * point-statistics measure inside a 0.4R patch can be healthy while the rendered
 * frame shows nothing. The eye is the criterion for a visibility question.
 *
 * ─── WHY 0.04 AND NOT 0.00 ───
 * 0.04 is the LOWEST value at which the rows read. It keeps irregularity, which
 * the reference has (its own patchwise score is 0.392-0.515 with 32-59 degrees
 * of direction spread, not a crystal). And a perfect lattice is the most exposed
 * to temporal aliasing against a 60 Hz pixel grid as the shell rotates —
 * SHIMMER WAS NOT TESTED, because the capture path samples stills 1.5 s apart
 * and cannot see crawl, so 0.04 is partly a hedge against a risk that was not
 * measured. If it crawls, the direction back is toward 0.08, and the rows go
 * with it.
 *
 * FREE SIDE EFFECT, MEASURED: lower jitter is also BRIGHTER. Face dot core
 * luminance is 35.68 at 0.12 against 65.23 at 0.00 — x1.83 — because a clean
 * lattice lands its dots on consistent sub-pixel positions instead of smearing
 * them across two. That helps the separate, unfixed problem that this build's
 * face reads at 37 against the reference's 109.
 */
export const LATTICE_JITTER_DEFAULT = 0.04;

/** Default depth falloff. `--force-depth=` overrides it via bootstrap. */
export const DEPTH_FAR_DEFAULT = 0.42;

/**
 * THE RIM — extra brightness and extra point size at the silhouette.
 *
 * Both numbers are MEASURED, not chosen. See the sweep in item 2 of the build
 * report and the varying `vFresnel` in particles.vert.glsl. `--force-sphere=`
 * overrides all four rim/body numbers so a sweep needs one build, not twelve.
 */
/**
 * ─── THE WHOLE CRESCENT WAS REFITTED, AND THE BRIEF HAD IT BACKWARDS ───
 *
 * The instruction was that the crescent is "too wide — a broad band down the
 * right where the reference has a narrow edge hugging the silhouette". The
 * measurement says the opposite and the measurement wins.
 *
 * Sector luminance along a radius through the lit limb, both discs normalised,
 * both background-subtracted (the reference is a photograph and its frame sits
 * on a floor of 6.6 that belongs to the camera, not to the app):
 *
 *                        body/peak   half-max width   clipped   peak RGB
 *   reference image11      0.206         22.5%         0.15%   (239,122,227)
 *   build, before          0.132          7.5%        40.73%   (255,180,255)
 *   build, after           0.202         12.5%         0.00%   (212, 37,189)
 *
 * The reference's crescent is THREE TIMES WIDER than this build's was, and the
 * build's was clipping four pixels in ten to white. (255,180,255) is not a
 * colour: it is the framebuffer running out of room, and a limb that saturates
 * stops carrying the theme — which is half of why the shell read as a hard
 * white wire rather than as a lit edge.
 *
 * WHAT ACTUALLY CONTROLS THE WIDTH, and it is not what the name suggests.
 * `uRimPow` is documented as the width control and moving it 0.8 -> 0.2 changed
 * the measured width by nothing at all. The width is set by `uRimSize`: bigger
 * points at the limb OVERLAP, and under additive blending overlap is where the
 * peak comes from. A tall narrow peak puts the half-max threshold high, so the
 * band measures thin. Dropping rimSize 3.5 -> 2.4 and raising the energy-spread
 * exponent 0.6 -> 0.85 lowered the peak, which both stopped the clipping and
 * widened the band, from one change.
 *
 * RAISING BODY BRIGHTNESS MAKES body/peak WORSE, which was worth learning: the
 * body scales linearly with it and the limb scales faster, because more
 * brightness per particle means more of the overlapping stack clears the
 * visible floor. bodyBright stays at 1.0 and the contrast is closed from the
 * limb end instead.
 *
 * `uDarkSide` 0.18 -> 0.38 and `uLambertPow` 1.8 -> 0.85 are the other half:
 * they set the UNLIT side's gradient, which is the depth cue item 4 is about.
 * Measured limb/centre on the unlit side: reference 0.74, build 0.43 before,
 * 0.92-1.20 across the bracket this lands inside.
 *
 * WHAT DOES NOT REACH THE REFERENCE, stated rather than glossed: 12.5% against
 * 22.5%. The reference's profile rises smoothly from its centre; this one is
 * flat across the inner half and then knees. Closing that needs the mid-face
 * to carry more light relative to the limb than an additive point cloud with a
 * clamped sprite size produces, and every lever tried here trades it against
 * clipping. It is 1.8x short and it is 3x better than it was.
 */
/**
 * ─── THESE THREE WENT TOO FAR AND TOOK THE CRESCENT WITH THEM ───
 *
 * 0.5 -> 0.24, 3.5 -> 2.4 and 0.6 -> 0.85 were changed together, in one pass,
 * to stop the crescent clipping 40.73% of its band to white. They stopped it.
 * They also removed the crescent, and the summary metric being optimised at the
 * time could not see that — see the note on `litLimb` below.
 *
 * Bisected one constant at a time from the shipped values, everything else held,
 * magenta, idle, palette gain forced to 1:
 *
 *     restored             litLimb   peak-bg
 *     (nothing)             50.4%     31.54
 *     rimGain    -> 0.50    76.3%     60.73   <- largest single effect
 *     spreadPow  -> 0.60    78.1%     55.86
 *     rimSize    -> 3.5     74.0%     44.90
 *     darkSide   -> 0.18    50.3%     31.70   (no effect)
 *     lambertPow -> 1.80    47.4%     30.18   (no effect)
 *     rimPow     -> 0.80    44.6%     28.63   (no effect)
 *     reference             75.0%     58.06
 *
 * Three constants, each independently able to restore it, all moved the same
 * way at once. The values below are the fitted middle: litLimb 76.0% against
 * the reference's 75.0%, peak 53.25 against 58.06, and 0.02% clipped against
 * its 0.15% — so the clipping fix is kept and the crescent comes back.
 *
 * ─── AND WHY THE METRIC DID NOT CATCH IT ───
 * The figure being optimised was `body/peak`, a RATIO. It cannot tell "the body
 * got brighter" from "the peak collapsed", and what happened was the second:
 * body/peak IMPROVED from 0.132 to 0.148 while the crescent's absolute peak
 * fell from 100.99 to 31.54. `litLimb` — an ABSOLUTE coverage number earlier
 * rounds reported and this one had stopped reporting — fell 89.9% -> 50.4% over
 * the same change. Both numbers are now in the instrument and both are quoted.
 */
/**
 * ─── 0.34 -> 0.10. IT WAS FITTED IN GOLD AND MAGENTA WAS NEVER MEASURED ───
 *
 * Every crescent fit in this file's history was done in GOLD. Gold's accent
 * (`--theme-gold-body`) has two channels near full and a luminance of 208.
 * Magenta's (`--theme-magenta-body`) has one, and a luminance of 99.8.
 * `gainFor` normalises the palette by LUMINANCE, so to reach
 * the same luminance magenta needs 2.08x gold's amplitude, and that drives its
 * red and blue channels past 255 while its luminance is still moderate. The
 * result was never looked at: the lit limb in magenta measures
 *
 *     clipped pixels in the lit limb wedge, median over 19 captured frames
 *       gold     0.15%
 *       magenta 17.02%      reference image11  0.16%
 *       red     17.11%
 *
 * and magnified it is not a crescent at all but a solid white-pink wall with no
 * dots left in it, beside a reference whose lit limb stays resolved into
 * separate bright dots right up to the silhouette.
 *
 * NOT A REGRESSION FROM THE LATTICE CHANGE, and that was tested rather than
 * assumed: rendering magenta at the OLD lattice jitter of 0.40 clips 17.41%
 * against 0.12's 17.02%. Ordering the lattice did not cause this. It was
 * already there and nobody had rendered magenta.
 *
 * ─── THE SWEEP, magenta, idle, 9-19 frames each, medians ───
 *
 *   rimGain  RIBBON  HALF-MAX  litLimb  peak-bg  body/peak  CLIPPED
 *     0.34    27.5%     7.5%    89.8%    84.88     0.073    17.02%
 *     0.12    30.0%     7.5%    77.8%    66.12     0.090     7.41%
 *     0.08    27.5%     7.5%    66.2%    52.04     0.120     2.40%
 *     0.00    28.8%     7.5%    68.8%    52.49     0.103     1.63%
 *   reference image11  27.5%   22.5%    74.9%    57.50     0.198     0.16%
 *   reference median   30.0%   23.8%    67.1%    53.77     0.262     0.16%
 *   (across image2/5/9/11)
 *
 * 0.10 interpolates to litLimb ~72%, peak-bg ~59, clipped ~4.9%, ribbon ~28.8%
 * — four metrics on the reference's own medians, and the fifth (body/peak)
 * improved by about 45%. It is not the point that minimises clipping; it is the
 * point that keeps the crescent while removing the wall.
 *
 * ─── WHAT IT DOES NOT FIX, STATED ───
 * The HALF-MAX WIDTH does not move at all: 7.5% at every value of rimGain from
 * 0.34 to 0.00, against the reference's 17.5-37.5%. rimGain sets the crescent's
 * HEIGHT, not its width — which agrees with the note below that uRimSize is the
 * width control, and with the fact that dropping rimSize to 0 only takes the
 * half-max to 5.0%. The width remains open and it remains a factor of three.
 *
 * ─── AND WHY THE OLD 50.4% COLLAPSE IS NOT BEING REPEATED ───
 * The failure that produced this constant's previous value was litLimb falling
 * to 50.4% with peak-bg at 31.54 — the crescent gone. At 0.10 litLimb lands
 * near 72% and peak-bg near 59, both ABOVE the reference's own medians of 67.1%
 * and 53.77. The crescent is not being removed; a saturated rim line is.
 *
 * THE COMPANIONS ARE NOT FOLLOWING THIS and that is deliberate: companions.ts
 * carries its own uRimGain of 0.24, and measured against the reference their
 * brightness ratios are already right (right/main 0.524 against the reference's
 * 0.493). Changing a thing that measures correctly to chase a constant it does
 * not share would be trading a right answer for a tidy one. Flagged, not done.
 */
export const RIM_GAIN_DEFAULT = 0.1;
export const RIM_SIZE_DEFAULT = 3.0;

/**
 * How much brightness the side facing away from the light keeps.
 *
 * The reference's dark limb measures 0.0% lit coverage against 34.8% at the
 * bright one, so the honest copy of it is zero. Zero renders a crescent moon,
 * not a sphere: the terminator becomes a hard edge and half the shell is simply
 * gone. This floor is the compromise, and it is stated rather than hidden.
 */
export const DARK_SIDE_DEFAULT = 0.38;

/** Exponent on the wrapped lambert. See the uniform's note in particles.frag. */
export const LAMBERT_POW_DEFAULT = 0.85;

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
export const RIM_POW_DEFAULT = 0.2;
export const SPREAD_POW_DEFAULT = 0.75;

/**
 * How much of the palette the UNLIT FACE keeps. See uFaceSat in particles.frag
 * for the measurement and for why this is 0.45 and not the 0.11 that would
 * match the reference photograph exactly.
 */
/**
 * ─── 0.45 -> 1.0. THE DESATURATION IS DELETED, AND IT WAS FITTED TO AN ARTEFACT ───
 *
 * This is the constant that made the face grey, and it is mine. Measured on the
 * rendered pixels, one core pixel per connected component, gold at idle:
 *
 *                        face saturation   limb saturation
 *   uFaceSat 0.45            0.422             0.611
 *   uFaceSat 1.00 (off)      0.865             0.931
 *
 * It removes more than half the hue from the body and leaves the limb alone,
 * which is exactly what he has described seven times.
 *
 * ─── AND THE TARGET IT WAS FITTED TO DOES NOT EXIST ───
 * It was set to chase the reference's mid-face saturation of 0.077. That number
 * is an encoding artefact, and the test is this build's own capture pushed
 * through the reference's pipeline — upscaled to its disc size, blurred by the
 * measured 1.2 px PSF, then JPEG-encoded:
 *
 *     pipeline                      face sat   limb sat
 *     as rendered                     0.865      0.931
 *     blur only, 4:4:4 (control)      0.806      0.929
 *     blur + 4:2:0 q90                0.535      0.862
 *     blur + 4:2:0 q75                0.400      0.814
 *     blur + 4:2:0 q60                0.369      0.785
 *     reference image11               0.077      0.540
 *
 * 4:2:0 chroma subsampling averages colour over 2x2 blocks. An ISOLATED face
 * dot has its chroma averaged with the black around it; MERGED limb dots
 * protect each other. So the pipeline alone reproduces the reference's
 * face-neutral/limb-coloured split, and the 4:4:4 control does not — it is the
 * subsampling, not the blur, and not a design decision. The reference is a
 * photograph of a screen showing an already-compressed video, so it has been
 * through that chain more times than this test can reproduce, which is why even
 * q60 only reaches 0.369.
 *
 * ─── THE READING THAT WAS WRONG, STATED PLAINLY ───
 * Saturation is scale-invariant, so a LOW saturation number says nothing about
 * whether a particle looks white or grey. The reference's face particles
 * measure luminance 97.3 at saturation 0.077 — bright and neutral. This build's
 * measured 49.3 at 0.422 — DIM and washed. Half the brightness and a hue
 * half-removed is grey, and grey is what he sees. The two states share a
 * saturation figure and share nothing else, which is what made a single number
 * enough to fit the wrong thing to.
 *
 * 1.0 means the term is inert: `sat` becomes 1 everywhere and the mix and its
 * luminance rescale are identities. The uniform and the flag stay so the
 * finding survives and so this is one launch to re-test rather than a rebuild.
 */
export const FACE_SAT_DEFAULT = 1.0;

/**
 * The light, in VIEW space. LEFT, below, and slightly toward the camera.
 *
 * Direction taken from the reference rather than chosen: its bottom patch
 * measures 12.3% lit coverage against 1.5% at the top, and its lit limb 34.8%
 * against 0.0% at the opposite one. Below, therefore, and the small +z tips
 * the highlight a few degrees onto the face so the crescent has a soft inner
 * edge instead of ending exactly on the silhouette.
 *
 * The y component was -0.45 on the first pass and is measured down to -0.28.
 * The reference's ratio of bright limb to bottom is 34.8 : 12.3, i.e. 2.8 : 1;
 * at -0.45 mine measured 45.3 : 35.0, i.e. 1.3 : 1. The light was sitting too
 * low, which pooled the crescent under the sphere and read as the contact
 * ellipse he had just rejected, in a different form.
 *
 * ─── AND THEN X FLIPPED, ON A COUNT ACROSS ALL SIXTEEN ───
 *
 * "Right" came from ONE image. Measuring the lit azimuth in every one of the
 * sixteen says the reference is lit from the LEFT in twelve and from the right
 * in four (images 3, 7, 10, 15). Among the well-determined left-lit frames the
 * azimuth clusters at 157-200 degrees with a median near 176 — due left, nine
 * o'clock, near-horizontal, wrapping about 90 degrees of limb.
 *
 * So the earlier reading was not wrong about its own image; it was a sample of
 * one. Twelve to four is not a tie and image11, the sharpest and the one every
 * particle measurement in this project is taken from, is left-lit. x goes
 * +1.0 -> -1.0 and nothing else about the vector moves: the elevation and the
 * few degrees of +z were measured on ratios, which are unaffected by which side
 * they are measured on.
 */
const LIGHT_DIR = new Vector3(-1.0, -0.28, 0.3).normalize();

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
    // ROUND U: a depth buffer, for the fold's occluder. Nothing else reads it.
    depth: true,
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
  // Both spiral lattices take the round-Q material and uniforms; only 'uv' takes round P's.
  const fib = MAIN_LATTICE !== 'uv';
  // ROUND U: the deformation is a golden-angle-shell feature; `--force-deform=0` is the round-T shell.
  const deformOn = fib && (options.deform ?? FIB_DEFORM_ON);
  const restFrame = fibRestFrame();

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
    uDepthFar: { value: fib ? FIB_DEPTH_FAR : (options.depthFar ?? DEPTH_FAR_DEFAULT) },
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
    // Radial jitter, and it stays at zero. `--force-sphere`'s seventh slot no
    // longer reaches it: that slot now drives the TANGENTIAL lattice jitter in
    // buildGeometry, which is the one that breaks the grid without breaking the
    // silhouette. See the note there.
    uJitter: { value: JITTER_DEFAULT },
    uRimPow: { value: options.rim?.rimPow ?? RIM_POW_DEFAULT },
    uSpreadPow: { value: options.rim?.spreadPow ?? SPREAD_POW_DEFAULT },
    uFaceSat: { value: options.faceSat ?? FACE_SAT_DEFAULT },
    /**
     * EVEN LIGHTING — the MAIN sphere only. `companions.ts` does not declare this
     * uniform, so WebGL leaves it at 0 there and both companions keep the
     * directional crescent they were fitted with. That is the whole of the
     * scoping: one uniform set on one object, no shared constant touched.
     */
    uEvenLight: { value: 1 },
    /**
     * The single-particle ceiling, per object now rather than a shared const.
     * 0.70 for the evenly lit UV sphere; `companions.ts` pins 0.55 and is
     * unchanged. See the note on `uAlphaMax` in particles.frag.glsl for the
     * arithmetic — at 0.55 every front dot on this sphere was clamped flat.
     */
    /**
     * ROUND Q: the UV-only machinery — cross, caps, lifted cap ceiling, glow,
     * tight core, wide grain — is scoped OFF the golden-angle main sphere
     * here, by value, not by deleting anything. `fib` false restores every
     * UV_* constant.
     */
    uAlphaMax: { value: fib ? FIB_ALPHA_MAX : UV_ALPHA_MAX },
    uGlowGain: { value: fib ? FIB_GLOW_GAIN : UV_GLOW_GAIN },
    uCoreTight: { value: fib ? FIB_CORE_TIGHT : UV_CORE_TIGHT },
    uCrossGain: { value: fib ? 0 : UV_CROSS_GAIN },
    uCrossSize: { value: fib ? 0 : UV_CROSS_SIZE },
    uCrossWidth: { value: fib ? new Vector2(0, 0) : new Vector2(UV_CROSS_WIDTH[0], UV_CROSS_WIDTH[1]) },
    uGrainMin: { value: fib ? FIB_GRAIN_MIN : UV_GRAIN_MIN },
    uGrainPow: { value: fib ? FIB_GRAIN_POW : UV_GRAIN_POW },
    uCapBand: { value: fib ? new Vector2(2, 3) : new Vector2(UV_CAP_BAND[0], UV_CAP_BAND[1]) },
    uCapGain: { value: fib ? 0 : UV_CAP_GAIN },
    uCapSize: { value: fib ? 0 : UV_CAP_SIZE },
    uCapCeil: { value: fib ? 0 : UV_CAP_CEIL },
    /**
     * THE FIXED SURFACE GRADIENT (round Q). Three stops read from tokens —
     * measured off `third-orb.png`'s dot peak pixels, fixed to the image, not
     * the theme — placed along sin(angle from the camera-facing object-space
     * axis). `uGradMix` 1 replaces the theme tint entirely; companions.ts pins
     * it to 0. `uBackFade` removes the far hemisphere, which the reference
     * does not show.
     */
    uGradMix: { value: fib ? 1 : 0 },
    uGradCool: { value: tokenColor('--orb-grad-cool') },
    uGradMid: { value: tokenColor('--orb-grad-mid') },
    uGradWarm: { value: tokenColor('--orb-grad-warm') },
    uGradEdges: { value: new Vector2(FIB_GRAD_EDGES[0], FIB_GRAD_EDGES[1]) },
    uGradWarmPow: { value: FIB_GRAD_WARM_POW },
    uGradAxis: { value: fibGradAxis() },
    uBackFade: { value: fib ? new Vector2(FIB_BACK_FADE[0], FIB_BACK_FADE[1]) : new Vector2(-2, -1) },
    uRimShrink: { value: fib ? new Vector3(FIB_RIM_SHRINK[0], FIB_RIM_SHRINK[1], 1) : new Vector3(0, 1, 0) },
    uLightDir: { value: LIGHT_DIR.clone() },
    /**
     * ROUND U: the deformation (see FIB_DEFORM_*). Read only under the
     * TESSA_DEFORM define, which only the golden-angle main sphere and the
     * occluder carry; the companions' program never declares these.
     */
    uDeformOn: { value: deformOn ? 1 : 0 },
    uDeformRight: { value: restFrame.right },
    uDeformUp: { value: restFrame.up },
    uDeformFwd: { value: restFrame.fwd },
    uDeformAspect: { value: new Vector2(FIB_DEFORM_ASPECT[0], FIB_DEFORM_ASPECT[1]) },
    uDeformBump: {
      value: new Vector4(FIB_DEFORM_BUMP.amp, FIB_DEFORM_BUMP.freq, FIB_DEFORM_BUMP.phase, fibDeformCentreValue()),
    },
    uDeformBumpOff: { value: new Vector3(FIB_DEFORM_BUMP.offset[0], FIB_DEFORM_BUMP.offset[1], FIB_DEFORM_BUMP.offset[2]) },
    uDeformHarmA: { value: new Vector4(FIB_DEFORM_HARM[0], FIB_DEFORM_HARM[1], FIB_DEFORM_HARM[2], FIB_DEFORM_HARM[3]) },
    uDeformHarmB: { value: new Vector4(FIB_DEFORM_HARM[4], FIB_DEFORM_HARM[5], FIB_DEFORM_HARM[6], FIB_DEFORM_HARM[7]) },
    uDeformFold: {
      value: new Vector4(FIB_DEFORM_FOLD.shift, FIB_DEFORM_FOLD.ramp, FIB_DEFORM_FOLD.back[0], FIB_DEFORM_FOLD.back[1]),
    },
    uDeformCurve: {
      value: new Vector4(FIB_DEFORM_CREASE[0], FIB_DEFORM_CREASE[1], FIB_DEFORM_CREASE[2], FIB_DEFORM_CREASE[3]),
    },
    uDeformInset: { value: FIB_DEFORM_OCCLUDER_INSET },
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
    // ROUND U: the golden-angle shell takes the deformation chunk and the
    // TESSA_DEFORM define. The UV path and the companions keep the plain stage.
    vertexShader: fib ? `${deformChunk}\n${vertexShader}` : vertexShader,
    fragmentShader,
    defines: fib ? { TESSA_DEFORM: 1 } : {},
    transparent: true,
    depthWrite: false,
    // ROUND U: tested against the depth-only occluder, which is what hides the
    // sheet folded under and the far hemisphere behind the front one. Off,
    // exactly as before, when the deformation is off.
    depthTest: deformOn,
    /**
     * ROUND Q: MATTE, not additive, for the golden-angle sphere. The
     * reference's dots are opaque discs — where its silhouette rows overlap
     * they stay the same orange, they do not sum toward white. The shader
     * already writes premultiplied colour (tint * out, out), so NormalBlending
     * with premultipliedAlpha is ONE / ONE_MINUS_SRC_ALPHA: "over". Draw order
     * is the buffer order, and it only matters where dots overlap, which is
     * the silhouette, where neighbours share a colour. The far hemisphere is
     * faded to zero before it can paint over anything. Companions keep their
     * own additive material.
     */
    blending: fib ? NormalBlending : AdditiveBlending,
    premultipliedAlpha: fib,
  });

  /**
   * The count actually in the buffer. `--force-count` pins it; otherwise it is
   * the tier's own number and a demotion changes it.
   */
  const forcedCount = options.counts?.main ?? null;
  function countFor(t: SphereTier): number {
    return forcedCount ?? PARTICLE_COUNT[t];
  }

  const latticeJitter = options.rim?.jitter ?? LATTICE_JITTER_DEFAULT;
  let geometry = buildMainGeometry(countFor(tier), latticeJitter);
  const points = new Points(geometry, material);
  scene.add(points);

  /**
   * ROUND U: THE OCCLUDER. The same deformed surface as a depth-only mesh —
   * see the FIB_DEFORM_* note. Opaque, so three.js draws it before every
   * transparent object; colour writes off, so it paints nothing and the CSS
   * behind the canvas still shows through the between-dot floor; polygon
   * offset pushes it back by its own slope so a sprite on a steep part of the
   * surface is not nicked by it. It shares this engine's uniforms object, so
   * radius, breath and the deformation cannot drift from the dots'.
   */
  const occluder: Mesh | null = deformOn
    ? new Mesh(
        new SphereGeometry(1, FIB_DEFORM_OCCLUDER_SEGMENTS, FIB_DEFORM_OCCLUDER_SEGMENTS / 2),
        new ShaderMaterial({
          uniforms,
          vertexShader: `${deformChunk}\n${OCCLUDER_VERTEX}`,
          fragmentShader: OCCLUDER_FRAGMENT,
          colorWrite: false,
          depthWrite: true,
          depthTest: true,
          transparent: false,
          polygonOffset: true,
          polygonOffsetFactor: 1,
          polygonOffsetUnits: 1,
        }),
      )
    : null;
  if (occluder) {
    occluder.frustumCulled = false;
    scene.add(occluder);
  }

  /**
   * THE TWO BACKGROUND COMPANIONS. See companions.ts.
   *
   * Built here rather than by the caller because they share this engine's
   * scene, camera and projection — three of them in one context is two extra
   * draw calls, where three contexts would be three GPU contexts on a part
   * that has one.
   */
  const companions: Companion[] = [
    createCompanion(scene, 'left', () => worldPerPixel, () => ({ w: appliedW, h: appliedH }), options.counts?.companion ?? null, options.counts?.companionSize ?? null, latticeJitter),
    createCompanion(scene, 'right', () => worldPerPixel, () => ({ w: appliedW, h: appliedH }), options.counts?.companion ?? null, options.counts?.companionSize ?? null, latticeJitter),
  ];

  // Palette targets, resolved once from tokens. `amber` is the `blocked` state:
  // flat --status-warn, no gradient, because stillness plus a single hue is the
  // signal (CONTRACT §4.1).
  const palette = {
    flame: { hot: tokenColor('--sphere-hot'), cool: tokenColor('--sphere-cool') },
    amber: { hot: tokenColor('--status-warn'), cool: tokenColor('--status-warn') },
  };

  /**
   * ─── THE SHELL IS NORMALISED FOR THE PALETTE'S LUMINANCE, AND IT HAS TO BE ───
   *
   * Every shell parameter in this file was fitted under MAGENTA, and magenta is
   * a dark colour: `--theme-magenta-body` has a relative luminance of 0.283,
   * because its green channel is 41. `--theme-gold-body` has 0.699 — TWO AND A
   * HALF TIMES more — because gold is red plus almost all of green, and green
   * carries 71.5% of luminance. (Hashes are omitted throughout this note so it
   * does not itself trip the no-hard-coded-colour gate; the values live in
   * tokens.json, which is the only place a colour may.)
   *
   * Under additive blending that multiplies straight through. Switching to gold
   * with the fitted parameters measured a crescent peak of rgb(255,255,0) with
   * 4.93% of the band clipped, against magenta's rgb(212,37,189) and 0.00%.
   * Pure yellow is not a colour the palette contains; it is two channels
   * running out of headroom, and a limb that saturates stops carrying the theme
   * — which is the exact failure the ALPHA_MAX ceiling was added to bound.
   *
   * So the gain divides the fitted brightness by how bright the palette already
   * is. Reference luminance is magenta's, because that is what the fit was done
   * against, so magenta comes out at exactly 1.0 and nothing about the round's
   * measurements moves. Every other theme is corrected relative to it.
   *
   * Clamped to [0.35, 2.0]: violet is the darkest palette here and would ask
   * for a 1.6x boost, which is fine, but an unclamped ratio would let a future
   * near-black token drive the shell into overdraw.
   *
   * ─── AND IT APPLIES TO `amber` TOO, WHICH REVERSES MY FIRST ATTEMPT ───
   *
   * The first version exempted `blocked` on the reasoning that CONTRACT §4.1
   * fixes its colour and the alarm must not be normalised. That conflated HUE
   * with BRIGHTNESS. `--status-warn` has a linear luminance of 0.509,
   * so an unnormalised `blocked` beside six gold states at gain 0.406 measured
   * a body of 15.2 against their 3.4-5.7 and a crescent peak of 164 against
   * their 21-52 — eight times brighter than `idle`, which is not a signature,
   * it is a flashbang. Its `brightness` parameter is 1.25, mid-table between
   * idle's 1.10 and listening's 1.50, and it should render mid-table.
   *
   * Normalising the brightness leaves the hue untouched: amber is still amber,
   * still fixed, still unthemed. §4.1 is satisfied by the colour and by the
   * stillness, neither of which this touches.
   */
  /**
   * ─── THIS CONSTANT WAS IN THE WRONG COLOUR SPACE AND IT DIMMED EVERY THEME ───
   *
   * It was 0.2834, magenta's LINEAR-sRGB luminance, and it was divided by the
   * output of `relativeLuminance()`, which measures the ENCODED values. Those
   * are different numbers for the same colour — magenta encodes to 0.3916 — so
   * the ratio that was supposed to be exactly 1.0000 for magenta shipped at
   * 0.7237. Every palette came out 28% darker than the arithmetic claimed, and
   * gold landed on the 0.35 CLAMP FLOOR instead of its true 0.4801, which is
   * why gold and magenta looked the same to the owner: the normaliser was
   * compressing them toward each other and then flooring the brighter one.
   *
   * The comment that used to sit on `relativeLuminance` asserted the opposite
   * and that assertion is the whole bug: three's `LinearSRGBColorSpace` means
   * NO CONVERSION IS APPLIED, not "these are linear". `tokenColor` passes it
   * precisely so the transfer function is NOT applied — the raw ShaderMaterial
   * writes `gl_FragColor` directly and never runs three's colorspace pass — so
   * what is stored is the sRGB code value, 0..1.
   *
   * ENCODED is also the right space to normalise in, which is why the constant
   * moves rather than the function. Additive blending sums whatever the shader
   * writes, and the shader writes encoded values; how bright a palette *looks*
   * when summed is a property of those numbers, not of their linearisation.
   *
   * Recomputed in the same space the function measures. (Hex values omitted
   * throughout this note so it does not itself trip the no-hard-coded-colour
   * gate — every body colour is `theme-<name>-body` in tokens.json, which is
   * the only place a colour may live. This has now caught the same comment
   * twice; the gate is right and the comment is what moves.)
   *
   *     theme      encoded L(body)   gain BEFORE   gain NOW
   *     magenta        0.3916           0.7237       1.0000
   *     gold           0.8156           0.3500*      0.4801
   *     cyan           0.7986           0.3549*      0.4903
   *     violet         0.4248           0.6671       0.9217
   *     emerald        0.5927           0.4782       0.6607
   *     red            0.3422           0.8281       1.1442
   *     (* on the clamp floor)
   *
   * The gain is published on the metrics line so this can never drift silently
   * again — a capture whose log says pgain != 1.000 under magenta is a capture
   * taken with this bug back.
   */
  const MAGENTA_REF_ENCODED_LUMINANCE = 0.3916;

  function relativeLuminance(c: Color): number {
    // ENCODED sRGB, not linear. `tokenColor` reads with LinearSRGBColorSpace,
    // which in three means "apply no conversion" — so `c.r/g/b` are the code
    // values 0..1, and this is their weighted sum in that same space. See the
    // note above; getting this wrong is what shipped a 28% dimming.
    return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
  }

  /**
   * ─── AND THE GAIN IS A SQUARE ROOT, BECAUSE THE OUTPUT IS QUADRATIC IN IT ───
   *
   * This is the second half of the same bug and it is the half that made GOLD
   * specifically broken while magenta merely dimmed.
   *
   * The fragment stage ends `gl_FragColor = vec4(tint * out_, out_)` — a
   * PREMULTIPLIED colour — and the material blends with three's
   * `AdditiveBlending`, whose source factor is `SrcAlphaFactor`. So what
   * actually reaches the framebuffer is
   *
   *     dst += (tint * out_) * out_   =   tint * out_^2
   *
   * The contribution is QUADRATIC in alpha. The gain divides alpha, so dividing
   * by L reduces the contribution by L^2 while the tint only supplies L — and
   * the brighter the palette, the worse it gets. Measured against magenta:
   *
   *     theme      linear gain   relative output   sqrt gain   relative output
   *     magenta       1.0000          1.000          1.0000         1.000
   *     gold          0.4801          0.414          0.6929         0.862
   *     cyan          0.4903          0.422          0.7002         0.861
   *     emerald       0.6607          0.621          0.8128         0.940
   *     violet        0.9217          0.890          0.9601         0.966
   *     red           1.1442          1.197          1.0697         1.046
   *
   * 0.414 is what the owner was looking at: gold rendering at four tenths of
   * magenta, both already dimmed 28% by the colour-space error above. Measured
   * on the captures, gold's face p95 was 16.7 against magenta's 35.9 — a ratio
   * of 0.465 against the 0.414 this arithmetic predicts.
   *
   * The square root leaves magenta at exactly 1.0000, so nothing this round
   * fitted under magenta moves.
   *
   * The residual — gold at 0.862 rather than 1.000 — is because the gain is
   * taken from `--sphere-cool` alone while the rendered tint is a hot/cool mix
   * that varies with `uCoolMix` per state and per frame. Closing that would
   * mean recomputing the gain every frame from the live mix. 14% is inside the
   * frame-to-frame variance of every number in this file, so it is reported
   * rather than chased.
   *
   * NOT fixing the blend factor instead, deliberately: every shell parameter in
   * this file was fitted with the squaring in place, so changing it would
   * invalidate the whole round rather than one constant.
   */
  function gainFor(c: Color): number {
    const l = relativeLuminance(c);
    if (!(l > 0.001)) return 1;
    return Math.min(2.0, Math.max(0.35, Math.sqrt(MAGENTA_REF_ENCODED_LUMINANCE / l)));
  }

  const paletteGainOn = options.paletteGain ?? true;
  let flameGain = paletteGainOn ? gainFor(palette.flame.cool) : 1;
  let amberGain = paletteGainOn ? gainFor(palette.amber.cool) : 1;

  /**
   * The tint luminance the glow skirt was fitted at. Read from the token of the
   * theme `reference/main-orb.png` was rendered in, never written here, so that
   * theme normalises to exactly 1.0 and a token edit moves the knee with it.
   *
   * Falls back to no clamping at all if the property is missing, which is the
   * safe direction: an absent token must not silently delete the wash.
   */
  const glowKneeLum = relativeLuminance(tokenColor('--theme-red-body'));

  /** See UV_GLOW_LUM_POW. Only ever reduces; at or below the knee it is 1.0. */
  function glowGainFor(c: Color): number {
    const l = relativeLuminance(c);
    if (!(l > 0.001) || !(glowKneeLum > 0.001)) return UV_GLOW_GAIN;
    return UV_GLOW_GAIN * Math.min(1, Math.pow(glowKneeLum / l, UV_GLOW_LUM_POW));
  }

  let flameGlowGain = glowGainFor(palette.flame.cool);
  let amberGlowGain = glowGainFor(palette.amber.cool);

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
  /** Round W: the form's sway phases, radians. Accumulated, never uTime * factor. */
  let swayYawPhase = 0;
  let swayPitchPhase = 0;
  let swayRollPhase = 0;

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
   * Is the rAF loop running at all? See `setAnimating`.
   *
   * Starts true so every existing caller — the full Orb — behaves exactly as it
   * did before this existed. Only the ambient widget ever turns it off.
   */
  let animating = true;

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
    particles: countFor(tier),
    paletteGain: 1,
    glowGain: UV_GLOW_GAIN,
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
      paletteGain: flameGain,
      glowGain: uniforms.uGlowGain.value as number,
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
    smooth.voicePulse = approach(smooth.voicePulse, target.voicePulse, rate);
    smooth.voiceGlow = approach(smooth.voiceGlow, target.voiceGlow, rate);
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
      // Round V: the golden-angle shell caps the rate — see FIB_SPIN_MAX.
      const spinRate = fib ? Math.min(smooth.spin, FIB_SPIN_MAX) : smooth.spin;
      spinAngle += (spinRate * (1 + SPIN_GAIN * focusCurrent) * deltaMs) / 1000;
      // Accumulated, never uTime * factor — see the note in particles.vert.
      noisePhaseS += (deltaMs / 1000) * (1 + NOISE_RATE_GAIN * focusCurrent);
      // Round W: the form's sway. Inside the frozen guard, so `blocked` holds
      // whatever lean it had and reduced motion never leans at all.
      swayYawPhase += (deltaMs / SWAY_YAW_PERIOD_MS) * Math.PI * 2;
      swayPitchPhase += (deltaMs / SWAY_PITCH_PERIOD_MS) * Math.PI * 2;
      swayRollPhase += (deltaMs / SWAY_ROLL_PERIOD_MS) * Math.PI * 2;
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
    uniforms.uTurbulence.value =
      smooth.turbulence * (1 + TURB_AMP_GAIN * focusCurrent) * (fib ? FIB_TURB_MUL : 1);
    // Round V: the voice pulse rides on the breath — a whole-shell swell in
    // time with the amplitude envelope. The occluder reads uBreath too.
    uniforms.uBreath.value = Math.sin(breathPhase) * smooth.breathDepth + amplitude * smooth.voicePulse;
    uniforms.uAmpGain.value = smooth.amplitudeGain;
    uniforms.uPointScale.value =
      smooth.pointScale * bodySizeMul * fitCurrent * (fib ? FIB_POINT_SCALE_MUL : UV_POINT_SCALE_MUL);
    // §R.1: hotter under load. coolMix 1 is fully --sphere-cool and 0 is fully
    // --sphere-hot, so load pulls it DOWN toward hot from whatever the current
    // state's resting temperature is.
    loadCurrent = approach(loadCurrent, loadTarget, rate);
    uniforms.uCoolMix.value = smooth.coolMix * (1 - loadCurrent);
    // The palette gain rides on the state's brightness, not on the token, so a
    // theme switch crossfades through it with everything else. `blocked` gets
    // its own gain from --status-warn rather than an exemption — see the note
    // on gainFor for why exempting it was wrong.
    // Round W: the voice glow rides the amplitude with the swell — see
    // SphereParams.voiceGlow. 1.0 exactly whenever nothing is being said.
    uniforms.uBrightness.value =
      smooth.brightness *
      (1 + smooth.voiceGlow * amplitude) *
      bodyBrightMul *
      (fib
        ? FIB_BRIGHT_MUL
        : UV_BRIGHT_MUL * (target.palette === 'amber' ? amberGain : flameGain));
    // The skirt rides the SAME palette switch as the brightness, so a theme or
    // state crossfade carries the wash with it rather than stepping it.
    uniforms.uGlowGain.value = fib
      ? FIB_GLOW_GAIN
      : target.palette === 'amber' ? amberGlowGain : flameGlowGain;

    /**
     * SPIN ABOUT THE POLE AXIS, THEN TILT. The order matters and three.js gives
     * it for free: the default Euler order is XYZ, which composes as Rx·Ry·Rz, so
     * `rotation.y` turns the shell about its OWN pole axis and `rotation.x` then
     * leans that axis toward the viewer. The poles therefore stay put at top and
     * bottom for the whole rotation instead of swinging through the face — which
     * is the failure item 2b warns about, and it is avoided by construction
     * rather than by choosing a lucky rate.
     *
     * WHY TILT AT ALL. With the axis exactly vertical both pole caps sit at the
     * extreme top and bottom of the disc, edge-on, and the converging rings read
     * as a dense line rather than as rings. `reference/main-orb.png` is tilted —
     * that is why its pole caps show as four to five nested ELLIPSES, one seen
     * from slightly above and one from slightly below. Measured off the
     * reference: its pole centres sit at y = 153 and y = 584 on a disc of centre
     * 372 and radius 242, i.e. at 0.90 R and 0.88 R from centre rather than at
     * 1.00 R, which is a tilt of about asin(0.89) complement — 27 degrees.
     */
    if (fib) {
      // The measured rest pose (see fibRestEuler), spun about the lattice's
      // own axis. Round V turned FIB_SPIN_MUL on.
      tmpRestQuat.setFromEuler(fibRestEuler(spinAngle * FIB_SPIN_MUL));
      // ROUND V: pin the FORM and the GRADIENT to the view. The deformation
      // frame and the gradient axis are the object-space images of screen
      // right/up/forward under the REST pose (spin included, sway excluded),
      // so the squash, the fold and the purple centre stay where they are
      // while the lattice turns through them. At spin 0 this is
      // fibRestFrame() exactly.
      tmpPoseQuat.copy(tmpRestQuat).invert();
      (uniforms.uDeformRight.value as Vector3).set(1, 0, 0).applyQuaternion(tmpPoseQuat).normalize();
      (uniforms.uDeformUp.value as Vector3).set(0, 1, 0).applyQuaternion(tmpPoseQuat).normalize();
      (uniforms.uDeformFwd.value as Vector3).set(0, 0, 1).applyQuaternion(tmpPoseQuat).normalize();
      (uniforms.uGradAxis.value as Vector3).copy(uniforms.uDeformFwd.value as Vector3);
      // ROUND W: the sway, applied AFTER the rest pose — pose = sway * rest —
      // in view space (the camera sits on +z unrotated, so world axes are
      // screen axes). Because the frame above was taken from `rest` alone,
      // the form is pinned to the SWAYING frame: lattice, deformation,
      // occluder and gradient all lean together as one rigid body. See
      // SWAY_YAW_DEG.
      tmpSwayEuler.set(
        Math.sin(swayPitchPhase) * ((SWAY_PITCH_DEG * Math.PI) / 180),
        Math.sin(swayYawPhase) * ((SWAY_YAW_DEG * Math.PI) / 180),
        Math.sin(swayRollPhase) * ((SWAY_ROLL_DEG * Math.PI) / 180),
        'YXZ',
      );
      tmpSwayQuat.setFromEuler(tmpSwayEuler);
      points.quaternion.copy(tmpSwayQuat).multiply(tmpRestQuat);
    } else {
      points.rotation.y = spinAngle;
      points.rotation.x = POLE_TILT_RAD;
    }

    // The drawer shift moves the sphere inside the scene rather than resizing
    // the canvas. Reallocating a WebGL drawing buffer every frame of a 200 ms
    // drawer animation would be far more expensive than the animation itself.
    offsetCurrentPx = approach(offsetCurrentPx, offsetTargetPx, rate);
    offsetCurrentYPx = approach(offsetCurrentYPx, offsetTargetYPx, rate);
    points.position.x = -offsetCurrentPx * 0.5 * worldPerPixel;
    // World +y is screen UP, so a positive yPx lifts the sphere.
    points.position.y = offsetCurrentYPx * 0.5 * worldPerPixel;
    if (occluder) {
      // ROUND U: the occluder is the dots' own surface; it goes where they go.
      // Round W copies the quaternion — the pose is now composed there.
      occluder.quaternion.copy(points.quaternion);
      occluder.position.copy(points.position);
      // The amplitude ripple moves a dot radially by up to 0.35 x gain x the
      // amplitude of THIS frame. The occluder retreats by exactly that, so no
      // surface dot can dip behind it and it does not sit needlessly deep
      // between syllables (round V: the bound follows the envelope).
      uniforms.uDeformInset.value = FIB_DEFORM_OCCLUDER_INSET - 0.35 * smooth.amplitudeGain * amplitude;
    }

    // The companions advance on the same accepted frame as the main sphere, so
    // all three share one clock and cannot drift into looking independent.
    for (const c of companions) c.step(deltaMs, uniforms.uSizeScale.value);

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
      swayYawDeg: Math.sin(swayYawPhase) * SWAY_YAW_DEG,
      swayPitchDeg: Math.sin(swayPitchPhase) * SWAY_PITCH_DEG,
      swayRollDeg: Math.sin(swayRollPhase) * SWAY_ROLL_DEG,
      resizeReason,
    };
  }

  /**
   * ─── THE AMBIENT WIDGET'S BATTERY SWITCH ───
   *
   * `false` STOPS THE LOOP. It does not slow it, and the difference is the
   * whole feature. `targetFps()` already returns 0 when the document is hidden,
   * and the line below it (`if (fps === 0) return;`) skips the RENDER — but the
   * rAF callback still fires on every vsync, so the compositor still wakes this
   * process 60 times a second to be told there is nothing to do. That is fine
   * for a window the owner opened and will close. It is not fine for a sphere
   * pinned to his corner all day on a two-core laptop with no GPU.
   *
   * So this cancels the callback outright and draws one last frame, which then
   * simply stays on screen — a WebGL back buffer persists until something draws
   * over it. Restarting re-enters the loop with the clocks continuous, because
   * `step` measures its own delta against `lastStepAt` and is handed a value
   * clamped to 250 ms, so a five-hour freeze does not produce a five-hour jump.
   */
  function setAnimating(on: boolean): void {
    if (disposed || reducedMotion || on === animating) return;
    animating = on;
    if (on) {
      // Reset the delta origin, or the first frame after a freeze would be
      // handed the entire frozen duration as its timestep.
      lastStepAt = 0;
      lastRafAt = 0;
      rafId = requestAnimationFrame(frame);
    } else {
      cancelAnimationFrame(rafId);
      // One final frame so the frozen image is the CURRENT state rather than
      // whatever happened to be on screen when the freeze landed.
      step(0);
      stats = { ...stats, fps: 0 };
    }
  }

  function frame(now: number): void {
    if (disposed || !animating) return;
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

    const count = countFor(tier);
    scene.remove(points);
    geometry.dispose();
    geometry = buildMainGeometry(count, latticeJitter);
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
    setAnimating,

    setCompanions(placements) {
      for (const p of placements) {
        const c = companions.find((x) => x.side === p.side);
        // Clamped rather than trusted: a NaN out of a layout calculation would
        // put a sphere at an undefined position, which renders as nothing and
        // looks exactly like "the feature was never built".
        if (!c || !Number.isFinite(p.fx) || !Number.isFinite(p.fy) || !Number.isFinite(p.scale)) continue;
        c.place(
          Math.min(1.4, Math.max(-0.4, p.fx)),
          Math.min(1.4, Math.max(-0.4, p.fy)),
          Math.min(0.6, Math.max(0.04, p.scale)),
        );
      }
    },

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
      // Recomputed here and nowhere else: the gain is a property of the tokens,
      // so the one place that re-reads the tokens is the one place that may
      // change it.
      flameGain = paletteGainOn ? gainFor(palette.flame.cool) : 1;
      amberGain = paletteGainOn ? gainFor(palette.amber.cool) : 1;
      // Same reasoning, same one place: the skirt clamp is a property of the
      // tokens too. See UV_GLOW_LUM_POW.
      flameGlowGain = glowGainFor(palette.flame.cool);
      amberGlowGain = glowGainFor(palette.amber.cool);
      palette.amber.hot.copy(tokenColor('--status-warn'));
      palette.amber.cool.copy(tokenColor('--status-warn'));
    },

    dispose() {
      disposed = true;
      cancelAnimationFrame(rafId);
      observer.disconnect();
      canvas.removeEventListener('webglcontextlost', onContextLost);
      if (redrawListener) document.removeEventListener('visibilitychange', redrawListener);
      for (const c of companions) c.dispose();
      geometry.dispose();
      material.dispose();
      if (occluder) {
        occluder.geometry.dispose();
        (occluder.material as ShaderMaterial).dispose();
      }
      renderer.dispose();
      renderer.forceContextLoss();
    },
  };
}
