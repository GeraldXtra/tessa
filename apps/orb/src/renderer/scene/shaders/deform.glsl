// Tessa Orb — ROUND U: the main shell's DEFORMATION. Squash, organic field,
// and the right-side fold. Prepended to the particle vertex stage (under
// TESSA_DEFORM) and to the depth-only occluder; never to the companions.
//
// Every number lives in FIB_DEFORM_* in sphere-engine.ts, with the
// measurements off reference/third-orb.png that produced it. Everything here
// is a function of the dot's OBJECT-SPACE direction expressed in the
// rest-pose SCREEN frame (right, up, toward-camera), the same construction
// round Q used for the gradient — so the shape is a property of the surface
// and turns with the shell, rather than a screen-space distortion painted
// over a rotating lattice.
//
// The unit-sphere direction goes in; the deformed unit-shell position comes
// out. Radius, breath and the pulse multiply it afterwards exactly as they
// multiplied the round shell, so nothing about size or animation changes.

uniform float uDeformOn;      // 1 on, 0 identity (the round build, exactly)
uniform vec3  uDeformRight;   // object-space direction that is screen-right at rest
uniform vec3  uDeformUp;      // screen-up at rest
uniform vec3  uDeformFwd;     // toward the camera at rest (equals uGradAxis)
uniform vec2  uDeformAspect;  // (sx, sy): the squash, as scale on screen x and y
uniform vec4  uDeformBump;    // (amplitude, frequency, phase, field value at the face centre)
uniform vec3  uDeformBumpOff; // offset into the organic field
uniform vec4  uDeformHarmA;   // limb harmonics a1 b1 a2 b2
uniform vec4  uDeformHarmB;   // limb harmonics a3 b3 a4 b4
uniform vec4  uDeformFold;    // (shift K, ramp W, back-edge start, back-edge end)
uniform vec4  uDeformCurve;   // crease x(up) = c0 + c1 up + c2 up^2 + c3 up^3
uniform float uDeformInset;   // occluder scale, < 1 so surface dots sit in front of it

// The same three detuned sines the turbulence uses — but evaluated on the
// SMOOTH direction with no per-dot seed and a FROZEN phase, so it is a
// continuous field over the shell (a lumpy blob) rather than per-dot scatter.
float deformWobble(vec3 p, float t) {
  return sin(p.x * 3.1 + t * 1.70)
       * sin(p.y * 2.7 - t * 1.30)
       * sin(p.z * 3.9 + t * 2.10);
}

vec3 deformToLocal(vec3 v) {
  return vec3(dot(v, uDeformRight), dot(v, uDeformUp), dot(v, uDeformFwd));
}

vec3 deformFromLocal(vec3 v) {
  return uDeformRight * v.x + uDeformUp * v.y + uDeformFwd * v.z;
}

// d is a unit direction in the rest screen frame: x right, y up, z toward the eye.
vec3 deformLocal(vec3 d) {
  float rx = d.x;
  float up = d.y;
  float f  = d.z;

  // 1. THE ORGANIC FIELD — radial, low frequency, with the face-centre value
  //    subtracted so the centre dot spacing is untouched by construction.
  float b = uDeformBump.x * (deformWobble(d * uDeformBump.y + uDeformBumpOff, uDeformBump.z) - uDeformBump.w);
  // 2. THE LIMB HARMONICS — a fitted correction to the silhouette, fading
  //    with (1 - f^2) so it is full on the limb and zero at the face centre.
  float phi = atan(up, rx + 1.0e-5);
  float hh = uDeformHarmA.x * cos(phi)       + uDeformHarmA.y * sin(phi)
           + uDeformHarmA.z * cos(2.0 * phi) + uDeformHarmA.w * sin(2.0 * phi)
           + uDeformHarmB.x * cos(3.0 * phi) + uDeformHarmB.y * sin(3.0 * phi)
           + uDeformHarmB.z * cos(4.0 * phi) + uDeformHarmB.w * sin(4.0 * phi);
  b += (1.0 - f * f) * hh;
  vec3 p = d * (1.0 + b);

  // 3. THE SQUASH — an anisotropic scale in the screen plane.
  p.xy *= uDeformAspect;

  // 4. THE FOLD. Dots whose rest position lies right of the crease curve are
  //    carried screen-LEFT: the ramp W is wide, so the sheet foreshortens
  //    progressively and its rows converge before it turns under; the shift
  //    lets go over the back band, so the surface behind the limb comes back
  //    out to the silhouette as the second layer. See sphere-engine.ts.
  float xc = uDeformCurve.x + up * (uDeformCurve.y + up * (uDeformCurve.z + up * uDeformCurve.w));
  float w = smoothstep(0.0, uDeformFold.y, rx - xc) * smoothstep(uDeformFold.z, uDeformFold.w, f);
  p.x -= uDeformFold.x * w;
  return p;
}

vec3 deformShape(vec3 dir) {
  return deformFromLocal(deformLocal(deformToLocal(dir)));
}

// 1 on the FAR WALL of the fold — the surface behind the limb that the fold
// exposes — 0 everywhere else. The rim shrink (round T) sizes a dot by the
// foreshortening of a round shell; the far wall is not foreshortened that
// way (its rows are 5-7 px apart on screen, measured) and the reference
// shows it as full-size beads, so the vertex stage exempts it.
float deformFoldBack(vec3 dir) {
  vec3 d = deformToLocal(dir);
  float up = d.y;
  float xc = uDeformCurve.x + up * (uDeformCurve.y + up * (uDeformCurve.z + up * uDeformCurve.w));
  return smoothstep(0.0, 0.1, d.x - xc) * (1.0 - smoothstep(uDeformFold.w, uDeformFold.w + 0.25, d.z));
}

// The deformed surface normal by finite differences on a tangent basis that
// is right-handed with the outward direction, so the sign is the sphere's.
vec3 deformNormal(vec3 dir, vec3 p0) {
  vec3 ref = abs(dir.y) > 0.9 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0);
  vec3 t1 = normalize(cross(ref, dir));
  vec3 t2 = cross(dir, t1);
  float e = 1.0e-3;
  vec3 pu = deformShape(dir + t1 * e);
  vec3 pv = deformShape(dir + t2 * e);
  vec3 n = cross(pu - p0, pv - p0);
  float len = length(n);
  return len > 0.0 ? n / len : dir;
}
