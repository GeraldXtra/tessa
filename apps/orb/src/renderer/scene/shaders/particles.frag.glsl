// Zoey Orb — particle sphere, fragment stage.
//
// Colours arrive as uniforms, never as literals. CONTRACT §9: neither surface
// hard-codes a hex value. The JS side reads --sphere-hot / --sphere-cool /
// --status-warn off the document's computed style — the same generated
// custom properties the rest of the UI uses — so retuning tokens.json retints
// the sphere with no shader change.
//
// This stage is the expensive one on an HD 620. Every pixel of every point is
// an additive blend, so the work per fragment is kept to a discard, one mix and
// two multiplies, and the vertex stage clamps point size to bound the count.

uniform vec3  uColorHot;
uniform vec3  uColorCool;
uniform float uCoolMix;
uniform float uBrightness;

/**
 * How much of its brightness the FARTHEST particle keeps. 1.0 disables the
 * effect entirely and reproduces the pre-depth shell exactly, which is what
 * `--force-depth=1` does — so before/after is one flag on one binary rather
 * than two builds, and a mismatched comparison is not possible.
 */
uniform float uDepthFar;

/** Extra brightness at the LIT silhouette. See vFresnel in the vertex stage. */
uniform float uRimGain;

/** How much larger a silhouette point draws. Shared with the vertex stage. */
uniform float uRimSize;

/**
 * What fraction of its brightness the side facing AWAY from the light keeps.
 *
 * Measured: the reference's dark limb is 0.0% lit coverage against 34.8% at the
 * bright one. Copying that literally would give a shell with a hard terminator
 * and half of it missing, which reads as a crescent moon rather than a sphere —
 * so this is a floor, not zero, and the number is judged rather than measured.
 * See the report for what it cost and why it stopped where it did.
 */
uniform float uDarkSide;

/**
 * The exponent on the wrapped lambert. 1.0 is the plain half-angle cosine.
 *
 * Measured, not chosen. At 1.0 the crescent was too broad angularly and too
 * narrow radially — it wrapped over the top of the shell (top patch 4.9%
 * against the reference's 1.5%) while leaving the lit half's inboard region
 * too dark (litMid 3.8% against 7.0%). Raising the exponent steepens the
 * terminator, which pushes light off the top and poles and concentrates it
 * across the lit face, moving both errors the right way at once.
 */
uniform float uLambertPow;

/**
 * The crescent's radial width, as an exponent on the fresnel. LOWER IS WIDER.
 *
 * Shared with the vertex stage's point-size term, deliberately — brightness and
 * size have to peak on the same particles or the band is a bright thin line
 * sitting inside a wide dim one, which is two rims.
 */
uniform float uRimPow;

/**
 * ENERGY SPREAD. How much of its per-particle brightness a widened point gives
 * back. 0 disables it and reproduces the un-conserved shell exactly.
 *
 * This is the term that makes "broader" and "not brighter" compatible, and
 * without it they are not. Growing a sprite multiplies its AREA, and with
 * additive blending every overlapping pixel sums — so widening the band raised
 * the limb's mean lit luminance to 0.39 against the reference's 0.061, a factor
 * of 6.4, while the rim GAIN was only 0.4. The brightness was never coming from
 * the gain; it was coming from overlap.
 *
 * Dividing by the size growth gives the light back as area instead of as
 * intensity, which is what spreading a fixed amount of light over a larger spot
 * physically means. At 2.0 it is full conservation (area goes as the square);
 * below that the band still gains some intensity as it widens, which is what
 * keeps it reading as a lit edge rather than as a flat wash.
 */
uniform float uSpreadPow;

/**
 * The most one sprite may contribute. See the note at the end of main().
 *
 * Not a uniform: it is a property of the framebuffer, not of the look, and
 * exposing it as a tuning knob would invite raising it back to 1.0 to make the
 * crescent "brighter" — which is the change that produced the white wall.
 */
const float ALPHA_MAX = 0.55;

varying float vRim;
varying float vSeed;
varying float vPulse;
varying float vDepth;
varying float vFresnel;
varying float vLight;
varying float vSpread;

void main() {
  // Round the point. gl_PointCoord is [0,1] across the sprite; work in squared
  // distance so there is no sqrt per fragment.
  vec2 offset = gl_PointCoord - vec2(0.5);
  float dist2 = dot(offset, offset);
  if (dist2 > 0.25) discard;

  // Soft edge. Without it the particles read as square-ish pixels at tier LOW,
  // where each one is only 1–2 px.
  float falloff = 1.0 - smoothstep(0.02, 0.25, dist2);

  // Displaced particles drift toward the cool rim colour, which is what gives
  // the shell depth without a second draw call or any post-processing.
  // The rim carries the SATURATION as well as the light. Measured on the
  // reference's direct capture, the only region bright enough for 4:2:0 chroma
  // to survive is the limb, at #B54E46 - hsl(4, 44%, 49%). The body reads as
  // neutral grey there, and that is a compression artefact rather than a design
  // fact, so it is not copied.
  float mixAmount = clamp(uCoolMix + vRim * 0.55 + vFresnel * 0.45, 0.0, 1.0);
  vec3 tint = mix(uColorHot, uColorCool, mixAmount);

  // Per-particle brightness jitter so the shell does not look like a printed
  // dot screen. Cheap hash on the seed; deterministic across frames.
  float grain = 0.78 + 0.22 * fract(sin(vSeed * 91.7) * 43758.5453);

  // The heartbeat band brightens the particles it passes through rather than
  // adding a separate ring: the pulse IS the shell reacting, not an overlay
  // drawn on top of it. Zero when no beat is in flight — enforced by uPulseGain
  // in the vertex stage, because phase zero is NOT the resting state.
  // DEPTH. The far side recedes, so the shell reads as a volume instead of a
  // flat speckle. This is the one visual property the sphere was missing that
  // is neither a rate nor a displacement: the three retired intensification
  // levers all tried to encode a scalar through a rate the eye has no reference
  // for, whereas this is a SPATIAL gradient with both of its ends visible in
  // the same frame, which is self-referencing and needs no memory to read.
  //
  // Applied to alpha rather than to `tint`, so it dims without desaturating —
  // the far side must recede, not change hue, or it would fight the colour
  // temperature for the same channel.
  float depthFade = mix(1.0, uDepthFar, vDepth);

  // THE CRESCENT — the broad half of it.
  //
  // A wrapped lambertian: brightest where the surface faces the light, falling
  // to `uDarkSide` where it faces away. This is what produces the reference's
  // 0.1% -> 1.8% -> 7.0% run from the dark side across the middle to the bright
  // side, and it is the term that gives the shell a dark side at all. Without
  // it the sphere is lit from within and has no volume to read.
  float lambert = mix(uDarkSide, 1.0, pow(0.5 + 0.5 * vLight, uLambertPow));

  // THE CRESCENT — the sharp half.
  //
  // The exponent keeps it off the face of the disc: `vFresnel` is near zero
  // across the middle whatever the power, so this lifts the edge and not the
  // shell. A linear ramp would brighten everything uniformly, which is the
  // state it was already in and the state he rejected.
  //
  // 1.3 rather than 2.0, and the side-by-side is what changed it: squared drew
  // a thin bright wire ON the silhouette, where the reference has a broad
  // granular band reaching well inboard — 12.9 px mean blob at the lit
  // mid-radius against 4.1 px at the square.
  //
  // The SAME exponent as the vertex stage's size term, so brightness and size
  // peak on the same particles and the band is bright exactly where it is also
  // merged. Two different falloffs put a bright thin line inside a wide dim
  // one, which is two rims.
  //
  // Gated on `face`, again matching the vertex stage. Ungated this is a fresnel
  // ring: it lights the dark limb too and turns the object back into a bubble.
  float face = smoothstep(-0.15, 0.85, vLight);
  float grow = pow(vFresnel, uRimPow);

  // The growth the vertex stage ACTUALLY delivered, clamp included. Recomputing
  // it here from uRimSize would reproduce the intended growth rather than the
  // realised one, and past the overdraw clamp those two diverge — which made
  // the crescent dim as it was widened. See vSpread.
  float spread = pow(max(vSpread, 1.0), -uSpreadPow);

  // THE RIM IS SUB-LINEAR IN THE STATE'S BRIGHTNESS, and that is a fix rather
  // than a preference.
  //
  // The crescent was fitted to the reference at `idle`, whose brightness is
  // 1.10, and at that setting the hottest pixel on the limb already sits at
  // R=255. Every other state is brighter — `listening` was 2.00, 1.8x — and
  // multiplying the rim by the state brightness took all five of them past the
  // framebuffer's ceiling: MEASURED, the peak crescent pixel was rgb(255,255,255)
  // in listening, thinking, working, blocked, and under both the violet and
  // cyan themes.
  //
  // That is two failures at once, and the second is worse than the first. White
  // is white in every palette, so the brightest part of the sphere stopped
  // carrying the theme; and every state saturating to the same white flattens
  // the ordering the six states exist to express.
  //
  // `sqrt` keeps the rim responsive to state — a brighter state still has a
  // brighter edge — while compressing 1.8x down to 1.35x. The BODY keeps the
  // full multiplier, so "listening brightens" still reads where it always did.
  float rimAdd = uRimGain * grow * face * sqrt(max(uBrightness, 0.0));
  float lit = uBrightness + rimAdd;

  float alpha = falloff * lit * grain * depthFade * lambert * spread * (1.0 + vPulse * 1.6);

  // THE CEILING. One sprite may not, on its own, saturate the framebuffer.
  //
  // This is the honest limit of an additive point cloud and it is worth stating
  // plainly rather than tuning around: with `blending: AdditiveBlending` the
  // framebuffer SUMS every overlapping sprite, and the crescent exists
  // precisely because sprites overlap there. The reference does not do this —
  // its limb peaks at rgb(180,90,82), which no additive stack of merged points
  // reaches without either being sparse enough not to merge (and then it is not
  // a band) or dim enough to vanish.
  //
  // So the ceiling is the compromise, and here is exactly what it buys: it
  // bounds ONE sprite below saturation, so a white pixel now requires two or
  // more particles to land on it rather than one being bright enough alone.
  // MEASURED across the six states and five themes, that took the worst case
  // from 1,946 near-white pixels to the figure in the report, out of ~270,000
  // in the disc.
  //
  // It cannot reach zero and claiming otherwise would be false. What it does
  // reach is a crescent whose saturated pixels are a sparse specular glint
  // inside a coloured band, rather than a white edge on the sphere.
  float out_ = min(alpha, ALPHA_MAX);
  gl_FragColor = vec4(tint * out_, out_);
}
