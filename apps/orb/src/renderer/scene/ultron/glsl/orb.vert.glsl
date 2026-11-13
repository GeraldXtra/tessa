#version 300 es
precision highp float;

layout(location = 0) in vec4 aPos;
layout(location = 1) in vec4 aSpin;
layout(location = 2) in vec4 aLook;

uniform vec2 uRes;
uniform vec2 uCentre;
uniform float uUnit;
uniform float uDist;
uniform mat3 uOrb;
uniform mat3 uCore;
uniform float uFlow;
uniform float uTime;
uniform float uBoot;
uniform float uVoice;
uniform float uCoreScale;
uniform float uSurge;
uniform vec2 uBand;
uniform vec3 uRingRate;
uniform vec2 uArc;
uniform vec2 uRipple;
uniform float uSize;
uniform float uDpr;
uniform float uPmax;
uniform float uGain;
uniform vec3 uMid;
uniform vec3 uBright;
uniform vec3 uHot;
uniform vec3 uWhite;
uniform vec4 uPush[4];
uniform float uPushR;

out vec3 vCol;

const float TAU = 6.28318530718;

float rnd(float x) {
  x = fract(x * 0.1031);
  x *= x + 33.33;
  x *= x + x;
  return fract(x);
}

vec3 spinAbout(vec3 p, vec3 axis, float ang) {
  vec3 par = axis * dot(axis, p);
  vec3 per = p - par;
  return par + per * cos(ang) + cross(axis, per) * sin(ang);
}

float fade(float lo, float hi, float x) {
  return clamp((x - lo) / (hi - lo), 0.0, 1.0);
}

vec3 skin(vec3 base, float phase, out float a, out vec3 col) {
  float lift = 1.0 + uVoice * 0.025 * (0.6 + 0.4 * sin(phase * TAU + uTime * 7.0)) + 0.008 * sin(uTime * 1.6 + base.y * 7.0);
  vec3 p = uOrb * (base * lift);
  vec3 n = normalize(p);
  float limb = pow(1.0 - abs(n.z), 1.7);
  float face = 0.62 + 0.38 * fade(-0.3, 0.3, n.z);
  float glint = max(0.0, 1.0 - abs(fract(uTime * 0.37 + phase * 11.0) - 0.5) * 80.0);
  float bandD = (p.y - uBand.x) / 0.028;
  float band = exp(-bandD * bandD) * uBand.y;
  a = (0.1 + 0.9 * limb) * face * (0.82 + 0.18 * sin(uTime * (1.5 + 2.0 * phase) + phase * TAU)) + glint * 1.2 + band * (0.35 + 0.65 * limb);
  col = mix(mix(uMid, uBright, 0.5 + 0.5 * limb), uWhite, clamp(glint + band * 0.9, 0.0, 1.0) * 0.7);
  return p;
}

void main() {
  float kind = aPos.w;
  float phase = aLook.w;
  float a = aLook.y;
  float size = aLook.x;
  vec3 col = uBright;
  vec3 p;
  float on = 1.0;
  float ripple = 0.0;
  if (kind < 0.5) {
    on = fade(0.3, 0.8, uBoot);
    float sa;
    p = skin(spinAbout(aPos.xyz, aSpin.xyz, aSpin.w * uFlow), phase, sa, col);
    a *= sa;
    p *= mix(0.55, 1.0, on);
    ripple = 1.0;
  } else if (kind < 1.5) {
    float ring = aLook.z;
    on = fade(0.12 + 0.13 * ring, 0.58 + 0.13 * ring, uBoot);
    float rate = ring < 0.5 ? uRingRate.x : (ring < 1.5 ? uRingRate.y : uRingRate.z);
    float theta = phase * TAU;
    float h1 = uFlow * rate * TAU + ring * 2.1;
    float tailA = mod(h1 - theta, TAU) / TAU;
    float tailB = mod(h1 + 3.3 - theta, TAU) / TAU;
    float glowA = exp(-tailA * 7.0) * (1.0 - fade(0.8, 1.0, tailA));
    float glowB = exp(-tailB * 7.0) * (1.0 - fade(0.8, 1.0, tailB));
    float spot = exp(-tailA * tailA * 5200.0) + exp(-tailB * tailB * 5200.0);
    float glow = glowA + glowB;
    a *= 0.38 + 2.3 * glow + 3.4 * spot;
    size *= 1.0 + 1.5 * spot;
    col = mix(mix(uMid, uBright, 0.7), mix(uHot, uWhite, clamp(spot, 0.0, 1.0)), clamp(glow * 0.75 + spot, 0.0, 1.0));
    p = uOrb * (spinAbout(aPos.xyz, aSpin.xyz, aSpin.w * uFlow) * mix(0.6, 1.0, on));
  } else if (kind < 2.5) {
    on = fade(0.05, 0.5, uBoot);
    p = uCore * (spinAbout(aPos.xyz, vec3(0.0, 1.0, 0.0), aSpin.w * uFlow) * uCoreScale * mix(0.25, 1.0, on));
    float r = aLook.z;
    a *= 0.6 * (0.74 + 0.26 * sin(uTime * (1.1 + 2.6 * phase) + phase * 37.0)) * (1.0 + 0.7 * uSurge + 0.35 * uVoice);
    col = r < 0.12 ? mix(uWhite, uHot, r / 0.12) : mix(uHot, mix(uMid, uBright, 0.55), clamp((r - 0.12) / 0.5, 0.0, 1.0));
    ripple = 0.35;
  } else if (kind < 3.5) {
    on = fade(0.0, 0.3, uBoot);
    p = uCore * (spinAbout(aPos.xyz, aSpin.xyz, aSpin.w * uFlow) * uCoreScale * mix(0.2, 1.0, on));
    float tw = 0.6 + 0.4 * sin(uTime * (1.4 + 3.1 * phase) + phase * 41.0);
    a *= tw;
    col = mix(uHot, uWhite, 0.5 + 0.5 * tw);
  } else if (kind < 4.5) {
    on = fade(0.45, 1.0, uBoot);
    p = uOrb * (spinAbout(aPos.xyz, aSpin.xyz, aSpin.w * uFlow) * mix(0.5, 1.0, on));
    a *= 0.5 + 0.5 * sin(uTime * (0.6 + 1.6 * phase) + phase * 53.0);
    col = mix(uMid, uBright, 0.45);
  } else if (kind < 5.5) {
    on = fade(0.2, 0.75, uBoot);
    p = uOrb * (spinAbout(aPos.xyz, aSpin.xyz, aSpin.w * uFlow) * mix(0.4, 1.0, on));
    a *= (0.82 + 0.18 * sin(uTime * (0.5 + phase) + phase * 29.0)) * (1.0 + 0.4 * uSurge + 0.25 * uVoice);
    col = mix(mix(uHot, uBright, 0.5), uMid, aLook.z);
    ripple = 0.4;
  } else {
    on = fade(0.7, 1.0, uBoot);
    float k = aPos.x;
    float s = aPos.y;
    float period = 0.55 + 0.4 * rnd(k * 17.0 + 3.0);
    float c = (uFlow + rnd(k * 29.0 + 7.0) * period) / period;
    float id = floor(c);
    float age = fract(c);
    float flash = age < 0.08 ? age / 0.08 : exp(-(age - 0.08) * 6.5);
    float pick = step(rnd(id * 7.7 + k * 13.3), uArc.y);
    vec3 dir = normalize(vec3(rnd(id * 3.1 + k) - 0.5, rnd(id * 5.9 + k * 2.0) - 0.5, rnd(id * 8.3 + k * 3.0) - 0.4));
    vec3 e1 = normalize(cross(dir, abs(dir.y) < 0.9 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0)));
    vec3 e2 = cross(dir, e1);
    float shape = sin(3.14159 * s);
    float q1 = rnd(id * 1.3 + k * 4.0) * TAU;
    float q2 = rnd(id * 2.7 + k * 9.0) * TAU;
    float jag = rnd(floor(s * 38.0) + id * 11.0 + k * 3.0) - 0.5;
    float ox = (sin(s * 11.0 + q1) * 0.6 + sin(s * 27.0 + q2) * 0.3) * 0.09 * shape + jag * 0.018;
    float oy = (sin(s * 13.0 + q2) * 0.6 + sin(s * 31.0 + q1) * 0.3) * 0.09 * shape - jag * 0.012;
    p = uOrb * (dir * mix(0.06, 0.86, s) + e1 * ox + e2 * oy);
    float tip = exp(-(1.0 - s) * 26.0);
    a *= flash * pick * uArc.x * (0.55 + 0.45 * pow(shape, 0.4) + 2.0 * tip) * (0.8 + 0.4 * rnd(s * 97.0 + floor(uTime * 30.0)));
    size *= 1.0 + 1.4 * tip;
    col = mix(uHot, uWhite, 0.4 + 0.6 * flash);
  }
  if (kind < 2.5 || (kind > 3.5 && kind < 5.5)) a *= 0.62 + 0.5 * fade(-0.7, 0.7, p.z);
  float rr = (length(p.xy) - uRipple.x) / 0.04;
  float rw = ripple * uRipple.y * exp(-rr * rr);
  a += rw;
  col = mix(col, uWhite, clamp(rw, 0.0, 1.0));
  a *= on;
  float dz = uDist - p.z;
  float per = uDist / max(dz, 0.1);
  if ((kind > 0.5 && kind < 1.5) || (kind > 3.5 && kind < 4.5)) {
    float behind = 1.0 - fade(-0.15, 0.05, p.z);
    float covered = 1.0 - fade(0.8, 0.9, length(p.xy) * per);
    a *= 1.0 - 0.66 * covered * behind;
  }
  vec2 px = uCentre + vec2(p.x, -p.y) * uUnit * per;
  for (int i = 0; i < 4; i++) {
    vec4 push = uPush[i];
    if (push.z <= 0.0) continue;
    float age = push.w - rnd(phase * 997.0 + float(i) * 13.0) * 0.3;
    if (age <= 0.0) continue;
    vec2 dv = px - push.xy;
    float dist = length(dv);
    float env = (1.0 - exp(-age * 14.0)) * exp(-age * 1.8);
    px += (dv / max(dist, 1.0)) * push.z * exp(-(dist * dist) / (uPushR * uPushR)) * env * uPushR * 0.9;
  }
  float want = size * uSize * per * uDpr;
  float drawn = clamp(want, 1.5, uPmax);
  a *= min(1.0, (want * want) / (drawn * drawn)) * step(0.1, dz);
  vCol = col * a * uGain;
  gl_PointSize = drawn;
  gl_Position = a > 0.002 ? vec4(px.x / uRes.x * 2.0 - 1.0, 1.0 - px.y / uRes.y * 2.0, 0.0, 1.0) : vec4(2.0, 2.0, 2.0, 1.0);
}
