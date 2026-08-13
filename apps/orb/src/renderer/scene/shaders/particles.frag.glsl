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

varying float vRim;
varying float vSeed;
varying float vPulse;

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
  float mixAmount = clamp(uCoolMix + vRim * 0.55, 0.0, 1.0);
  vec3 tint = mix(uColorHot, uColorCool, mixAmount);

  // Per-particle brightness jitter so the shell does not look like a printed
  // dot screen. Cheap hash on the seed; deterministic across frames.
  float grain = 0.78 + 0.22 * fract(sin(vSeed * 91.7) * 43758.5453);

  // The heartbeat band brightens the particles it passes through rather than
  // adding a separate ring: the pulse IS the shell reacting, not an overlay
  // drawn on top of it. Zero when no beat is in flight — enforced by uPulseGain
  // in the vertex stage, because phase zero is NOT the resting state.
  float alpha = falloff * uBrightness * grain * (1.0 + vPulse * 1.6);
  gl_FragColor = vec4(tint * alpha, alpha);
}
