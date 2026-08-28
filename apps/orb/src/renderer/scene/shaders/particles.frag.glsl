// Tessa Orb — particle sphere, fragment stage.
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
 * HOW SATURATED THE FACE IS. 1.0 keeps the theme's hue everywhere, which is
 * what this shader did; 0 makes the unlit face pure white.
 *
 * Measured, and it is the last visible difference in the magnified side-by-side.
 * Mean colour of the brightest 5% of pixels, one patch mid-face and one on the
 * lit limb, both discs normalised:
 *
 *                     mid-face            limb
 *   reference    rgb( 81, 76, 84) s0.09   rgb(232, 97,215) s0.58
 *   build        rgb( 83, 20, 75) s0.76   rgb(175, 29,155) s0.83
 *
 * The reference's face particles are NEUTRAL and only its limb carries the hue.
 * This build was saturated everywhere, which is why the crop reads as a magenta
 * field beside a white one.
 *
 * ─── AND THE CAUSE IS GENUINELY UNCERTAIN, SO THIS DOES NOT GO ALL THE WAY ───
 * A note further down this file already argued the opposite case and it is not
 * silly: the reference is a JPEG, 4:2:0 chroma subsampling averages colour over
 * 2x2 blocks, and an isolated bright dot on black has its chroma diluted by the
 * black around it. At the limb the dots merge into a band and the chroma
 * survives — which would produce exactly this saturated-limb, neutral-face
 * pattern with no design intent at all.
 *
 * I cannot separate those two from a photograph. What I can say is that the
 * IMAGE he is holding up shows white dots on the face, and that is what he has
 * judged five times. So this goes half way — 0.45, which measures a face
 * saturation near 0.38 against the reference's 0.09 and this build's 0.76 —
 * rather than to a value that would be right only if the compression theory is
 * wrong. Going to 0.11 would match the photograph exactly and would render the
 * shell nearly white under every theme, which is the failure the coolMix
 * correction in states.ts exists to prevent.
 *
 * The limb is untouched: the term is driven by `vFresnel`, which is ~0 across
 * the face and 1 at the silhouette.
 */
uniform float uFaceSat;

/**
 * EVEN LIGHTING, 1 = on. Set by the MAIN sphere only; the companions do not
 * declare it and WebGL defaults it to 0, so they keep their crescent.
 *
 * It removes the two DIRECTIONAL terms and nothing else:
 *   - the wrapped lambert becomes 1, so there is no dark side
 *   - rimAdd becomes 0, so there is no crescent
 * `depthFade` is DELIBERATELY KEPT. Depth on this sphere reads front-to-back by
 * distance from the camera, not by a light direction — symmetric left/right and
 * top/bottom — and it is also the only lever available for calming the
 * front/back grid interference as the shell rotates.
 */
uniform float uEvenLight;

/**
 * The most one sprite may contribute. See the note at the end of main().
 *
 * Not a uniform: it is a property of the framebuffer, not of the look, and
 * exposing it as a tuning knob would invite raising it back to 1.0 to make the
 * crescent "brighter" — which is the change that produced the white wall.
 */
/**
 * ─── WHAT THIS CEILING COSTS THE FACE, MEASURED, AND WHY IT STAYS ANYWAY ───
 *
 * It is the hard cap on how bright ONE particle can be, and with the squared
 * blend (`gl_FragColor = vec4(tint * out_, out_)` under SrcAlphaFactor) it caps
 * a single sprite's rendered red channel at 255 * 0.55^2 = 77.
 *
 * That is exactly where the face plateaus. Sweeping bodyBright 1.25 / 1.40 /
 * 1.55 / 1.70 with everything else held moved the face particle not at all —
 * rgb(77-78, 67-68, 11-12), luminance 65-66 in every one — because past
 * bodyBright ~1.25 the face is sitting on this ceiling. The reference's face
 * particles measure rgb(100,96,103), luminance 97.3, which is ABOVE it.
 *
 * Raising it to 0.75 was tried and measured. At the SHIPPED bodyBright of 1.0
 * it changes nothing (face luminance 47.8 against 48.7) because the face is not
 * on the ceiling there — it only binds once brightness is raised. So lifting
 * the face needs BOTH a higher bodyBright and a higher ceiling, and the same
 * pair also lifts the limb, which is where the clipping lives: at bodyBright
 * 1.70 with this ceiling the crescent already clipped 20.3% of its band.
 *
 * It stays at 0.55 because the fault reported this round is the face having no
 * COLOUR, and that is `uFaceSat`, fixed. Face LUMINANCE is a separate, adjacent
 * gap — 48.7 against 97.3 — and moving two globals to close it is exactly the
 * kind of un-asked-for retune that removed the crescent last round.
 */
/**
 * ─── 0.55 WAS A CONSTANT AND IT WAS CLIPPING EVERY FRONT DOT FLAT ───
 *
 * It is now a UNIFORM so the two sphere kinds can differ, because they must.
 * The companions pin it to 0.55 (companions.ts) and keep exactly the behaviour
 * they were fitted with. The MAIN sphere raises it, and here is why.
 *
 * The main sphere is an evenly lit UV grid: no crescent, no lambert, so every
 * front-facing dot arrives at the same alpha. That alpha is
 * `uBrightness * grain * depthFade`, which at idle is 1.10 x 0.693 x ~0.9 = 0.686
 * — ABOVE 0.55. So every front dot was being clamped to the identical value and
 * the sphere had no tonal range at all: measured dot peak 32.91 against
 * reference/main-orb.png's 84.32, and coverage above luminance 8 of 1.58%
 * against the reference's 24.99%.
 *
 * Raising the ceiling does not brighten by fiat; it stops discarding range the
 * shell already had. Under the squared blend a dot renders at `tint * out^2`, so
 * for gold (tint luminance 208) the reference's 84.32 needs out = sqrt(84.32/208)
 * = 0.636. 0.70 clears that with headroom and leaves the back hemisphere, which
 * carries depthFade 0.42, at 208 * (0.686*0.42)^2 = 17 — still clearly dimmer
 * than the front, which is the depth cue.
 */
uniform float uAlphaMax;

/** Amplitude of the wide un-squared skirt. 0 = off, which companions.ts pins. */
uniform float uGlowGain;
/** Scales dist2 for the CORE term only, so a bigger quad keeps the same core. */
uniform float uCoreTight;

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

  // THE EDGE, and it was far too soft.
  //
  // smoothstep(0.02, 0.25) puts full brightness only inside 28% of the sprite
  // radius, so the outer 72% is gradient — a particle with almost no solid core
  // and a wide halo. Measured, the edge ran 3 px from 90% to 10% against the
  // reference's 2 px, and combined with sprites 2.3x too large it is most of
  // why the field reads as haze rather than as points.
  //
  // smoothstep(0.15, 0.25) holds full brightness out to 77% of the radius and
  // falls off over the last quarter. Still antialiased — a hard cut would make
  // each point a square at small sizes, which is what the original comment was
  // guarding against and is still true.
  /**
   * ─── COMPOUND FALLOFF: A SHARP SQUARED CORE PLUS A WIDE UN-SQUARED SKIRT ───
   *
   * The reference's sphere sits in a soft wash: bright sharp dot cores with the
   * black between them filled by diffuse glow. Two mechanisms were killing that
   * here, and both are named in REPORT-F:
   *
   *   - the quad is discarded beyond dist2 > 0.25, so a dot can never glow past
   *     0.5 * gl_PointSize; and
   *   - AdditiveBlending is SrcAlphaFactor, so a fragment contributes
   *     `tint * out^2` — a skirt at alpha 0.1 renders at 0.01 and vanishes.
   *
   * UN-SQUARING THE BLEND IS THE WRONG FIX: the squaring is what makes the cores
   * sharp. So the two are separated INSIDE the falloff instead. Because the blend
   * squares whatever `out_` carries, emitting
   *
   *     falloff = sqrt( sharp^2  +  uGlowGain * wide )
   *
   * makes the rendered contribution proportional to `sharp^2 + uGlowGain * wide`:
   * the CORE still arrives squared and stays sharp, and the SKIRT arrives
   * UN-squared and survives. No change to the blend, no softening of the cores.
   *
   * `uCoreTight` scales dist2 for the core term only, so the quad can be enlarged
   * to hold the skirt while the core keeps the same size IN PIXELS.
   *
   * WITH uGlowGain = 0 AND uCoreTight = 1 THIS IS EXACTLY THE OLD EXPRESSION —
   * sqrt(sharp^2) = sharp — which is what `companions.ts` pins, so they are
   * untouched to the last bit.
   *
   * AND THE HAZE IS A RENDERED EFFECT, NOT THE CAMERA — measured before building
   * it. In reference/main-orb.png the wash STOPS DEAD at the silhouette (annulus
   * medians 15.76 at r/R 0.88-0.90, 0.50 at 0.98-1.00, 0.000 from 1.00 outward,
   * exactly zero by 1.10) where a lens bloom would smear outward; and the local
   * between-dot floor correlates +0.688 with the local dot brightness, which is
   * what a sum of per-dot skirts does. So this is the reference's own mechanism,
   * not an imitation of an artefact.
   */
  float d2core = dist2 * uCoreTight;
  float sharp = 1.0 - smoothstep(0.08, 0.25, d2core);
  float wide = exp(-dist2 * 12.0);
  float falloff = sqrt(sharp * sharp + uGlowGain * wide);

  // Displaced particles drift toward the cool rim colour, which is what gives
  // the shell depth without a second draw call or any post-processing.
  // The rim carries the SATURATION as well as the light. Measured on the
  // reference's direct capture, the only region bright enough for 4:2:0 chroma
  // to survive is the limb, at #B54E46 - hsl(4, 44%, 49%). The body reads as
  // neutral grey there, and that is a compression artefact rather than a design
  // fact, so it is not copied.
  float mixAmount = clamp(uCoolMix + vRim * 0.55 + vFresnel * 0.45, 0.0, 1.0);
  vec3 tint = mix(uColorHot, uColorCool, mixAmount);

  // THE HUE LIVES ON THE LIMB. See uFaceSat. `vFresnel` is ~0 across the face
  // and 1 at the silhouette, so the face desaturates and the crescent keeps the
  // palette at full strength. Mixing toward white rather than toward the
  // ladder's core because the core is a light TINT (72-80% lightness), not a
  // neutral — no value of uCoolMix can reach a neutral face.
  //
  // AT CONSTANT LUMINANCE, and the first version was not. Mixing toward white
  // BRIGHTENS: white carries more luminance than any saturated hue, so raising
  // the whitening raised the shell, and the measured crescent went from 0.00%
  // clipped back to 17.09% with a peak of rgb(255,152,255) — the green channel
  // climbing from 29 to 152 was the whitening, not the palette. That undid the
  // one thing the rim refit had actually secured.
  //
  // Rescaling to the pre-mix luminance makes "desaturate" mean only desaturate.
  // It also matches the reference more closely than the naive mix did: its face
  // particles measure rgb(81,76,84) — neutral AND dim, not neutral and bright.
  vec3 hued = tint;
  float sat = clamp(uFaceSat + (1.0 - uFaceSat) * vFresnel, 0.0, 1.0);
  tint = mix(vec3(1.0), hued, sat);
  const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
  float lHued = dot(hued, LUMA);
  float lTint = dot(tint, LUMA);
  tint *= (lTint > 0.0001) ? (lHued / lTint) : 1.0;

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
  lambert = mix(lambert, 1.0, uEvenLight);

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
  float rimAdd = uRimGain * grow * face * sqrt(max(uBrightness, 0.0)) * (1.0 - uEvenLight);
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
  float out_ = min(alpha, uAlphaMax);
  gl_FragColor = vec4(tint * out_, out_);
}
