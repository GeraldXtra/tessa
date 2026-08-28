// Tessa Orb — particle sphere, vertex stage.
//
// One draw call, GL_POINTS, no index buffer. Every per-state difference is a
// uniform, so switching agent state never rebuilds geometry — CONTRACT §4's
// budget gives 80 ms p95 from state change to visible, and a buffer upload on
// an HD 620 is not the way to spend it.
//
// `position` arrives as a unit-sphere point (Fibonacci lattice, built once on
// the JS side). Everything below displaces it radially.
//
// three.js compiles this as GLSL1 and rewrites attribute/varying for WebGL2, so
// there is no #version directive and no `in`/`out` here on purpose.

attribute float aSeed;

uniform float uTime;        // seconds, frozen for `blocked`
uniform float uAmplitude;   // [0,1] from the amplitude signal
uniform float uRadius;
uniform float uTurbulence;
uniform float uBreath;      // signed breathing offset, already phased on the CPU
uniform float uAmpGain;
uniform float uPointScale;  // world units
uniform float uSizeScale;   // canvasHeight / (2 * tan(fov/2)) — set on resize

// §R.1 equatorial pulse. uPulse runs 0->1 once per received heartbeat.
//
// uPulseGain is 1 while a beat is in flight and 0 otherwise, and it is NOT
// redundant with uPulse == 0. uPulse == 0 is the START of the travel, where the
// band sits on the equator at full amplitude — the single most visible frame of
// the whole animation. Using it as the resting value left a dead daemon showing
// a permanently brightened, permanently bulged equator: MEASURED at +14.8%
// column luminance over the unlit shell, held across 457 consecutive samples.
// "No beat in flight" is a different fact from "the beat is at phase zero", and
// it needs its own uniform.
uniform float uPulse;
uniform float uPulseGain;

// The turbulence clock. Advances at 1x normally and faster while `thinking` is
// sustained — see THINKING_TAU_MS in sphere-engine.
uniform float uNoiseTime;

/** How much larger a silhouette particle draws than a face-on one. */
uniform float uRimSize;

/** The crescent's radial width, as an exponent on the fresnel. LOWER IS WIDER. */
uniform float uRimPow;

/**
 * The overdraw guard, in pixels. NOT an aesthetic choice.
 *
 * Every pixel of every point is an additive blend, so unbounded point size on
 * an HD 620 turns 20,000 particles into a full-screen overdraw storm. Raised
 * from 9 to 20 once the fragment stage started conserving energy across the
 * growth — see vSpread — because a bigger sprite no longer means a brighter
 * one, so the cost of raising it is fill rate alone and that was measured.
 */
const float POINT_SIZE_MAX = 20.0;

/**
 * The light direction, in VIEW space, unit length, set once on the CPU.
 *
 * View space and not object space, and that is the whole point: the shell
 * spins, and a light fixed to the object would drag its bright side around
 * with it, which reads as a rotating pattern rather than as a lit thing. Fixed
 * in view space, the particles rotate THROUGH a stationary highlight, which is
 * what a solid object does.
 */
uniform vec3 uLightDir;

/** Peak-to-peak radial jitter as a fraction of the radius. Breaks the lattice. */
uniform float uJitter;

/**
 * EVEN LIGHTING, 1 = on. The MAIN sphere sets it; the companions never declare
 * it, so WebGL leaves it at 0 and they keep the directional shading they were
 * fitted with. That is why this is a uniform and not a constant.
 *
 * reference/main-orb.png has no bright side and no crescent — measured, its left
 * half and right half differ by a factor of 1.114 where a directionally lit
 * sphere differs by several — so the rim's POINT GROWTH has to go with the rim's
 * brightness. Leaving the growth on would keep 3x sprites on one limb of an
 * otherwise evenly lit ball.
 */
uniform float uEvenLight;

varying float vRim;
varying float vSeed;
varying float vPulse;

/**
 * 0 at the nearest point of the shell, 1 at the farthest. VIEW depth, which is
 * the thing this sphere has never had.
 *
 * `vRim` looks like a depth cue and is not: it is driven by radial
 * DISPLACEMENT, so a particle thrown outward reads as "rim" whether it is in
 * front of the shell or behind it. With depthTest and depthWrite both off and
 * additive blending, the front and back halves have been shaded identically
 * since this file was written, which is why the shell reads as a flat speckle
 * with a bright limb rather than as a volume.
 */
varying float vDepth;

/**
 * FRESNEL. 0 facing the camera, 1 at the silhouette.
 *
 * This is the property the reference build has and this one did not, and it is
 * what "dense and thick" actually meant. Measured against the reference's own
 * direct capture, per 100x100px inside the shell:
 *
 *                      reference        this sphere (before)
 *   body coverage        1.9%             9.1%
 *   rim  coverage       40.8%            15.8%
 *   rim  mean blob      214.8 px          6.3 px
 *
 * The reference is SPARSER and DIMMER than this sphere almost everywhere — it
 * simply concentrates its light into the limb, where the particles merge into
 * continuous ribbons and read as a surface. A shell of even density has no
 * boundary and reads as a translucent cloud, which is exactly the complaint.
 *
 * So the answer was never more particles. It is a rim.
 */
varying float vFresnel;

/**
 * WHICH WAY THIS PARTICLE FACES RELATIVE TO THE LIGHT. -1 away, +1 toward.
 *
 * A SECOND CORRECTION, and it overturns the first one. The rim above was
 * modelled as a fresnel, which is symmetric: both limbs equally bright. The
 * reference is not symmetric. Re-measured against its own fitted disc —
 * centre (708,457), r=264, fitted by least squares through the right-edge arc
 * rather than by a hand-placed patch — coverage across the shell runs:
 *
 *   left limb   0.0%    max blob     0 px
 *   left mid    0.1%    max blob     4 px
 *   centre      1.8%    max blob    16 px
 *   right mid   7.0%    max blob    40 px
 *   right limb 34.8%    max blob  3417 px
 *   top         1.5%  ·  bottom  12.3%
 *
 * That is a monotone gradient from a DARK side to a blazing one, peaking at a
 * single limb, with a 3417 px continuous ribbon at the peak. It is a crescent,
 * not a ring — a directional light from the lower right — and a fresnel term
 * cannot produce it, because a fresnel term does not know which way the light
 * is.
 *
 * The distinction matters more than it sounds. A shell lit evenly from within
 * is a lamp; it has no dark side, so it has no volume, and no amount of edge
 * brightness fixes that. A shell with a dark side is an OBJECT. That is what
 * "dense and thick" was describing.
 */
varying float vLight;

/** How much bigger this point actually drew than its unlit size, after the
 *  clamp. The fragment stage divides brightness by it. */
varying float vSpread;

// Three detuned sines instead of a hash. No texture fetch, no branching, and on
// an integrated part the vertex stage has headroom that the fragment stage does
// not.
float wobble(vec3 p, float t) {
  return sin(p.x * 3.1 + t * 1.70)
       * sin(p.y * 2.7 - t * 1.30)
       * sin(p.z * 3.9 + t * 2.10);
}

void main() {
  vec3 dir = normalize(position);

  // uNoiseTime, not uTime. The turbulence clock runs at its own RATE so that
  // `thinking` can churn faster the longer it is held, and it is ACCUMULATED on
  // the CPU rather than derived as uTime * factor — multiplying a shared clock
  // by a changing factor jumps the phase on every frame the factor moves, which
  // reads as a glitch rather than as acceleration.
  float noise  = wobble(position * 1.6 + aSeed, uNoiseTime);
  float ripple = sin(dir.y * 9.0 - uTime * 6.0 + aSeed * 6.2831);

  // PER-PARTICLE RADIAL JITTER — the lattice-breaker.
  //
  // `position` is a Fibonacci lattice, which is near-optimally even, and that
  // evenness is visible: projected to the screen it produces concentric arcs
  // of dots. Beside the reference, whose particles scatter irregularly, that
  // regularity is the single largest remaining difference, and it reads as a
  // printed pattern rather than as a cloud of matter.
  //
  // A fixed per-particle offset breaks the arcs without touching the count,
  // the fill rate or the frame budget: it is one multiply-add on an attribute
  // the shader already reads. Static in object space, so it does not shimmer —
  // it thickens the shell into a thin spherical SHELL rather than a surface,
  // which is also closer to what the reference looks like.
  float jitter = 1.0 + uJitter * (fract(sin(aSeed * 127.1) * 43758.5453) - 0.5);

  float radius = uRadius * jitter
               + uBreath
               + uTurbulence * noise
               + uAmpGain * uAmplitude * ripple * 0.35;

  // A band travelling from equator to poles once per heartbeat. dir.y is 0 at
  // the equator and +/-1 at the poles, so |dir.y| is latitude; the band is a
  // narrow window around a latitude that sweeps outward as uPulse goes 0->1.
  // With no beat in flight uPulseGain is 0 and the shell is unaffected.
  float latitude = abs(dir.y);
  float band = 1.0 - smoothstep(0.0, 0.18, abs(latitude - uPulse));
  float pulse = uPulseGain * band * (1.0 - uPulse);   // fades toward the poles
  vPulse = pulse;

  radius += pulse * 0.05;

  vec4 viewPos = modelViewMatrix * vec4(dir * radius, 1.0);

  // How far this particle sits outside the nominal shell. Drives the hot→cool
  // gradient in the fragment stage: displaced particles read as the rim.
  vRim = clamp((radius / max(uRadius, 0.0001)) - 0.92, 0.0, 1.0);
  vSeed = aSeed;

  // VIEW-ANGLE fresnel, which is a different quantity from vRim: vRim measures
  // radial DISPLACEMENT, so a particle thrown outward reads as "rim" wherever
  // it is on the sphere. This measures the angle between the surface normal and
  // the eye, so it is 1 exactly on the silhouette and 0 in the middle of the
  // disc — the geometry the reference's bright limb comes from.
  //
  // `dir` is the unit normal in object space; the normal matrix takes it to
  // view space. The sphere is uniformly scaled, so normalize() of the rotated
  // direction is sufficient and cheaper than a full inverse-transpose.
  vec3 nView = normalize((modelViewMatrix * vec4(dir, 0.0)).xyz);
  vec3 eye   = normalize(-viewPos.xyz);
  vFresnel   = 1.0 - clamp(abs(dot(nView, eye)), 0.0, 1.0);
  vLight     = dot(nView, uLightDir);

  // View depth, normalised across the shell's own diameter rather than against
  // a camera constant. Taking the centre from the modelView matrix means this
  // stays correct if the camera or the sphere's transform ever changes, and
  // costs one mat4*vec4 on a stage that has headroom (the fragment stage is the
  // expensive one here, not this).
  float centreZ = -(modelViewMatrix * vec4(0.0, 0.0, 0.0, 1.0)).z;
  float span = max(uRadius, 0.0001);
  vDepth = clamp(((-viewPos.z) - (centreZ - span)) / (2.0 * span), 0.0, 1.0);

  gl_Position = projectionMatrix * viewPos;

  // Perspective-correct point size, then CLAMPED. The clamp is a performance
  // guard, not an aesthetic one: additive blending means every pixel of every
  // point is a blend operation, and unbounded point size on an HD 620 turns
  // 8,000 particles into a full-screen overdraw storm.
  // SIZE GROWS AT THE LIT LIMB, AND ONLY THERE.
  //
  // The reference's bright limb reaches a single CONNECTED blob of 3417 px.
  // That is not dots at all, it is a continuous ribbon, and the merging is most
  // of why its edge reads as a surface. Brightness alone cannot produce it:
  // separate points stay separate points however bright they are. Size can,
  // once the point diameter exceeds the projected spacing between neighbours,
  // which at the limb is compressed by the same 1/sqrt(1-r²) foreshortening
  // that puts the particles there in the first place.
  //
  // Gated on `face` so it is the CRESCENT that thickens. Growing both limbs
  // would put a bright ring around a hollow middle, which is a bubble.
  //
  // The clamp stays; it is the overdraw guard, not an aesthetic choice. Every
  // pixel of every point is an additive blend, and unbounded point size on an
  // HD 620 turns 8,000 particles into a full-screen overdraw storm.
  // THE EXPONENT, and it is the difference between a band and a line.
  //
  // Squared put all the growth in the last few degrees before the silhouette,
  // which drew a thin bright wire on the edge. Measured against the reference
  // side by side, its crescent is a BROAD GRANULAR BAND reaching well inboard:
  // mean blob area at the lit mid-radius is 12.9 px there against 4.1 px at
  // the square. `pow(f, 1.3)` starts the growth earlier and still leaves the
  // middle of the disc alone, because f is near zero there whatever the power.
  float face = smoothstep(-0.15, 0.85, vLight);
  float grow = pow(vFresnel, uRimPow);
  float base = uPointScale * uSizeScale / max(-viewPos.z, 0.001);
  float want = base * (1.0 + uRimSize * grow * face * (1.0 - uEvenLight));
  float got  = clamp(want, 1.0, POINT_SIZE_MAX);
  gl_PointSize = got;

  // THE REALISED GROWTH, POST-CLAMP — and it has to be measured here, not
  // assumed in the fragment stage.
  //
  // The fragment stage divides a particle's brightness by how much it grew, so
  // that spreading light over a bigger sprite does not also multiply it (see
  // uSpreadPow). The first version of that divided by the INTENDED growth,
  // `1 + uRimSize * grow * face`, and was wrong in a way that only showed up at
  // the extremes: the clamp is the overdraw guard, so past it the sprite stops
  // getting bigger while the divisor keeps getting larger. The result was a
  // crescent that went DARKER the harder it was pushed — measured, litLimb
  // coverage collapsing from 45.9% to 9.1% while every setting said "wider".
  //
  // Passing the ratio the clamp actually delivered makes the two agree by
  // construction, whatever the clamp is set to.
  vSpread = got / max(base, 0.0001);
}
