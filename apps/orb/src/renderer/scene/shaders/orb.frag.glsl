varying vec2 vQ;
uniform float uRp;
uniform mat3 uSurf;
uniform mat3 uSurf2;
uniform vec4 uOff;
uniform vec3 uOffSpark;
uniform float uSweep;
uniform float uSweepAmt;
uniform float uEnergy;
uniform float uRim;
uniform float uCore;
uniform float uDetail;
uniform float uTight;
uniform float uTightPhase;
uniform float uVoice;
uniform float uVoiceMix;
uniform float uVoiceFlow;
uniform float uSpark;
uniform float uHollow;
uniform float uTick;
uniform float uHeart;
uniform float uFlare;
uniform float uFlareWhite;
uniform float uDim;
uniform float uMuted;
uniform float uNight;
uniform float uAlphaFromColor;
uniform vec3 uTickCol;
uniform vec3 uDeep;
uniform vec3 uMid;
uniform vec3 uHot;
uniform vec3 uFlareCol;

const float PI = 3.14159265;
const vec3 LUMA = vec3(0.299, 0.587, 0.114);

float beam(vec3 n) {
  vec3 m = vec3(n.x * 0.955 - n.y * 0.296, n.x * 0.296 + n.y * 0.955, n.z);
  float lon = atan(m.x, max(m.z, 1e-4));
  float d = mod(lon - uSweep + 0.5 * PI, PI) - 0.5 * PI;
  return exp(-d * d * 30.0);
}

vec3 pal(float h) {
  vec3 a = mix(uDeep * 0.3, uMid, smoothstep(0.0, 0.55, h));
  vec3 b = mix(a, uHot, smoothstep(0.55, 1.0, h));
  return b + max(h - 1.0, 0.0) * 0.45;
}

vec3 plasma(float r, vec3 n) {
  float nz = n.z;
  float det = uDetail;
  vec3 s = uSurf * n;
  vec3 p = s * (1.25 + 0.5 * det);
  float wx = simplex3(p * 0.8 + vec3(0.0, 0.0, uOff.x));
  float wy = simplex3(p * 0.8 + vec3(4.7, 2.1, -uOff.y));
  float a1 = simplex3(p * 1.35 + vec3(wx, wy, 0.0) * (0.55 + 0.9 * det) + vec3(0.0, 0.0, uOff.z));
  float a2 = simplex3((uSurf2 * n) * (2.3 + 1.2 * det) + vec3(wy, -wx, 0.0) * 0.6 + vec3(0.0, 0.0, uOff.w));
  float f1 = pow(max(1.0 - abs(a1), 0.0), 6.0 - 2.0 * det);
  float f2 = pow(max(1.0 - abs(a2), 0.0), 9.0);
  float fil = f1 * 0.85 + f2 * (0.25 + 0.45 * det);
  float core = pow(nz, 2.2 - 1.2 * uVoice * uVoiceMix);
  float cloud = 0.5 + 0.5 * wx;
  float heat = core * (0.26 + 0.26 * uCore) * (0.75 + 0.35 * cloud) + fil * (0.26 + 0.50 * nz);
  heat = heat * uEnergy + 0.08;
  heat += uTight * 0.10 * (0.5 + 0.5 * sin(r * 21.0 + uTightPhase * 6.5)) * (1.0 - r);
  heat += uVoiceMix * uVoice * 0.16 * (0.5 + 0.5 * sin(r * 15.0 - uVoiceFlow * 9.0)) * nz;
  if (uSweepAmt > 0.01) heat += uSweepAmt * 0.45 * beam(n) * (0.45 + 0.55 * nz);
  if (uSpark > 0.01) heat += uSpark * 0.6 * smoothstep(0.58, 0.92, simplex3(s * 7.0 + uOffSpark));
  heat *= mix(1.0, 0.12 + 0.55 * pow(1.0 - nz, 2.0), uHollow);
  vec3 col = pal(heat);
  float fr = 1.0 - nz;
  col *= 1.0 - 0.22 * smoothstep(0.55, 0.88, fr) * (1.0 - smoothstep(0.9, 1.0, fr));
  col += uMid * pow(fr, 3.0) * 0.5 * uRim + uHot * pow(fr, 9.0) * 0.6 * uRim;
  float spec = pow(max(dot(n, normalize(vec3(-0.42, 0.55, 1.0))), 0.0), 90.0);
  col += spec * 0.35 * (1.0 - 0.7 * uHollow);
  col += uHot * pow(max(dot(n, normalize(vec3(0.5, -0.55, 0.9))), 0.0), 24.0) * 0.07;
  return col;
}

void main() {
  vec2 q = vQ;
  float r = length(q);
  float a = clamp((1.0 - r) * uRp + 0.5, 0.0, 1.0);
  vec3 col = vec3(0.0);
  if (a > 0.0) {
    float rc = min(r, 1.0);
    vec3 n = vec3(q * (rc / max(r, 1e-5)), sqrt(max(1.0 - rc * rc, 0.0)));
    col = plasma(rc, n);
  }
  float out1 = max(r - 1.0, 0.0);
  vec3 g = uMid * exp(-out1 * 7.0) * 0.32;
  float ePx = abs(r - 1.0) * uRp;
  float edge = exp(-ePx / 2.2);
  vec3 fx = uTickCol * edge * uTick * 0.9 + mix(uMid, uHot, 0.5) * edge * uHeart * 0.6;
  vec3 fc = mix(uFlareCol, uFlareCol + (1.0 - uFlareCol) * 0.92, uFlareWhite * smoothstep(0.5, 1.0, uFlare));
  fx += fc * (exp(-ePx / 4.5) * 1.25 + exp(-out1 * 6.0) * 0.4 * smoothstep(0.98, 1.02, r)) * uFlare;
  vec3 o = col * a + fx + g * (1.0 - a);
  float lum = dot(o, LUMA);
  o = mix(o, vec3(lum), clamp(0.8 * uDim + 0.35 * uMuted, 0.0, 0.9));
  o *= (1.0 - 0.8 * uDim) * (1.0 - 0.33 * uNight);
  o = max(o, 0.0);
  float glowAlpha = min(max(o.r, max(o.g, o.b)), 1.0);
  gl_FragColor = vec4(o, mix(a, max(a, glowAlpha), uAlphaFromColor));
}
