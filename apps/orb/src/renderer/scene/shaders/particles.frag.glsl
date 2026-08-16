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

/** Extra brightness at the silhouette. See vFresnel in the vertex stage. */
uniform float uRimGain;

varying float vRim;
varying float vSeed;
varying float vPulse;
varying float vDepth;
varying float vFresnel;

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

  // THE RIM. Squared so it stays off the face of the disc and climbs steeply
  // only in the last few degrees before the silhouette — a linear ramp lifts
  // the whole shell and just makes it uniformly brighter, which is the state it
  // was already in and the state he rejected.
  float rim = 1.0 + uRimGain * vFresnel * vFresnel;

  float alpha = falloff * uBrightness * grain * depthFade * rim * (1.0 + vPulse * 1.6);
  gl_FragColor = vec4(tint * alpha, alpha);
}
