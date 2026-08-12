// Zoey Orb — particle sphere, vertex stage.
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

varying float vRim;
varying float vSeed;

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

  float noise  = wobble(position * 1.6 + aSeed, uTime);
  float ripple = sin(dir.y * 9.0 - uTime * 6.0 + aSeed * 6.2831);

  float radius = uRadius
               + uBreath
               + uTurbulence * noise
               + uAmpGain * uAmplitude * ripple * 0.35;

  vec4 viewPos = modelViewMatrix * vec4(dir * radius, 1.0);

  // How far this particle sits outside the nominal shell. Drives the hot→cool
  // gradient in the fragment stage: displaced particles read as the rim.
  vRim = clamp((radius / max(uRadius, 0.0001)) - 0.92, 0.0, 1.0);
  vSeed = aSeed;

  gl_Position = projectionMatrix * viewPos;

  // Perspective-correct point size, then CLAMPED. The clamp is a performance
  // guard, not an aesthetic one: additive blending means every pixel of every
  // point is a blend operation, and unbounded point size on an HD 620 turns
  // 8,000 particles into a full-screen overdraw storm.
  float size = uPointScale * uSizeScale / max(-viewPos.z, 0.001);
  gl_PointSize = clamp(size, 1.0, 7.0);
}
