varying vec2 vQ;
uniform float uRp;
uniform float uPass;
uniform float uHollow;
uniform float uMuted;
uniform float uDim;
uniform float uNight;
uniform float uAlphaFromColor;
uniform vec3 uMid;
uniform vec3 uHot;
uniform vec3 uAmber;
uniform vec3 uAmberHead;
uniform mat3 uRingM[4];
uniform vec4 uRingA[4];
uniform vec4 uRingH[4];

const float TAU = 6.28318531;
const vec3 LUMA = vec3(0.299, 0.587, 0.114);

void main() {
  vec2 q = vQ;
  float r = length(q);
  vec3 acc = vec3(0.0);
  float front = step(0.5, uPass);
  for (int i = 0; i < 4; i++) {
    vec4 A = uRingA[i];
    if (A.z < 0.002) continue;
    mat3 M = uRingM[i];
    vec3 nr = M[1];
    float nz = nr.z;
    if (abs(nz) < 0.06) nz = (nz < 0.0) ? -0.06 : 0.06;
    float z = -(nr.x * q.x + nr.y * q.y) / nz;
    vec3 P = vec3(q, z);
    float side = step(0.0, P.z);
    float rr = length(P);
    vec2 gr = (q - P.z * nr.xy / nz) / max(rr, 1e-4);
    float dpx = abs(rr - A.x) / max(length(gr) / uRp, 1e-5);
    float line = clamp(1.3 - dpx, 0.0, 1.0) * (1.0 - abs(side - front));
    if (line > 0.0) {
      vec3 L = P * M;
      float ang = atan(L.z, L.x + 1e-6);
      float len = A.y * TAU;
      float rel = mod(uRingH[i].z - ang, TAU);
      float inArc = 1.0 - step(len, rel);
      float tail = clamp(1.0 - rel / max(len, 1e-3), 0.0, 1.0);
      float inten = 0.17 + inArc * (0.33 + 0.67 * tail);
      inten = mix(inten, 1.0, step(0.999, A.y));
      vec3 rc = mix(mix(uMid, uHot, 0.4 * inArc), uAmber, A.w);
      acc += rc * line * inten * mix(0.6, 1.0, front) * A.z;
    }
    float hside = step(0.0, uRingH[i].w);
    vec2 hd = (q - uRingH[i].xy) * uRp;
    float hg = exp(-dot(hd, hd) / 14.0) * (1.0 - step(0.999, A.y)) * (1.0 - abs(hside - front));
    acc += mix(uHot, uAmberHead, A.w) * hg * A.z;
  }
  if (uPass < 0.5) {
    acc += mix(uMid, uHot, 0.35) * clamp(1.4 - abs(r - 1.17) * uRp, 0.0, 1.0) * uHollow * 0.9;
    if (uMuted > 0.001) {
      float dash = step(0.45, fract(atan(q.y, q.x + 1e-6) * 10.18592));
      acc += uMid * clamp(1.25 - abs(r - 1.09) * uRp, 0.0, 1.0) * dash * uMuted * 0.6;
    }
  }
  float lum = dot(acc, LUMA);
  acc = mix(acc, vec3(lum), clamp(0.8 * uDim, 0.0, 0.9)) * (1.0 - 0.8 * uDim) * (1.0 - 0.33 * uNight);
  gl_FragColor = vec4(acc, min(max(acc.r, max(acc.g, acc.b)), 1.0) * uAlphaFromColor);
}
