#version 300 es
precision highp float;

layout(location = 0) in vec4 aA;
layout(location = 1) in vec4 aB;

uniform vec2 uRes;
uniform vec2 uCentre;
uniform float uUnit;
uniform float uDist;
uniform float uGalUnit;
uniform float uOrbPx;
uniform mat3 uView;
uniform mat3 uBelt;
uniform float uDustSpin;
uniform float uOrbit;
uniform float uFlowT;
uniform float uGal;
uniform float uGalSpin;
uniform float uBoot;
uniform float uThink;
uniform float uListen;
uniform float uGalaxy;
uniform float uBeltK;
uniform float uEmber;
uniform float uDim;
uniform float uDpr;
uniform float uPmax;
uniform vec3 uBright;
uniform vec3 uMid;
uniform vec3 uDimC;
uniform vec3 uHot;
uniform vec3 uStarA;
uniform vec3 uStarB;

out vec3 vCol;
out float vKind;

const float PI = 3.14159265;

vec3 aroundY(vec3 v, float a) {
  float c = cos(a);
  float s = sin(a);
  return vec3(c * v.x + s * v.z, v.y, -s * v.x + c * v.z);
}

void main() {
  float kind = aA.w;
  vKind = kind;
  vec2 px = uCentre;
  float a = 0.0;
  float size = 2.0;
  vec3 col = uBright;
  float ok = 1.0;
  if (kind < 0.5) {
    vec3 p = uView * aroundY(aA.xyz, uDustSpin);
    float dz = uDist - p.z;
    ok = step(0.1, dz);
    float per = uDist / max(dz, 0.1);
    px = uCentre + vec2(p.x, -p.y) * uUnit * per;
    a = 0.55 * smoothstep(0.15, 0.85, uBoot) * (1.0 - 0.5 * uGalaxy);
    a *= smoothstep(uOrbPx * 1.05, uOrbPx * 1.6, length(px - uCentre));
    size = 0.12 * uUnit / max(dz, 0.1);
    col = mix(uStarA, uStarB, aB.x);
  } else if (kind < 1.5) {
    float ang = aA.z - uOrbit * aB.y;
    vec3 p = uBelt * vec3(aA.x * cos(ang), aA.y, aA.x * sin(ang));
    float dz = uDist - p.z;
    ok = step(0.1, dz);
    float per = uDist / max(dz, 0.1);
    px = uCentre + vec2(p.x, -p.y) * uUnit * per;
    a = aB.z * uBeltK;
    if (p.z < 0.0) a *= smoothstep(uOrbPx - 1.0, uOrbPx + 2.0, length(px - uCentre));
    size = (0.7 + aB.x) * per;
    col = mix(uMid, vec3(0.74, 0.7, 0.66), 0.3 + 0.5 * aB.w);
  } else if (kind < 2.5) {
    float f = fract(aB.x + uFlowT * aB.y);
    float r = mix(3.0, 0.16, pow(f, 0.75));
    vec3 p = uView * (aroundY(aA.xyz, f * 2.6 * aB.w) * r);
    float dz = uDist - p.z;
    ok = step(0.1, dz);
    float per = uDist / max(dz, 0.1);
    px = uCentre + vec2(p.x, -p.y) * uUnit * per;
    a = pow(max(sin(PI * f), 0.0), 1.4) * (uThink + 0.35 * uListen) * 0.45 * (0.5 + 0.5 * aB.z);
    size = (1.1 + 1.2 * aB.z) * per;
    col = mix(uBright, uHot, f);
  } else if (kind < 3.5) {
    float f = fract(aA.z + uFlowT * aB.y);
    float th = aA.x + (aB.x - 0.5) * 0.8 * f;
    float rr = 0.14 + f * (0.55 + 1.25 * aA.y);
    px = uCentre + vec2(cos(th), -sin(th)) * rr * uUnit;
    a = smoothstep(0.0, 0.15, f) * pow(1.0 - f, 1.5) * uEmber * (0.4 + 0.6 * aB.z);
    size = (1.2 + 2.2 * aB.z) * (1.0 - 0.5 * f);
    col = mix(uHot, mix(uBright, uMid, f), smoothstep(0.0, 0.5, f));
  } else {
    float r = aB.y * mix(0.62, 1.0, uGalaxy);
    float ang = aA.y + uGal * 0.2 / sqrt(aB.y) + uGalSpin;
    vec3 q = vec3(cos(ang) * r, aA.x * 0.06, sin(ang) * r);
    q = vec3(q.x, q.y * 0.9004 - q.z * 0.435, q.y * 0.435 + q.z * 0.9004);
    float per = 12.0 / (12.0 - q.z);
    px = uCentre + vec2(q.x, -q.y) * uGalUnit * per;
    float arm = pow(0.5 + 0.5 * cos(2.0 * ang - 3.0 * log(aB.y) - uGal * 0.05), 2.0);
    float b = 0.25 + 0.5 * aB.x;
    col = mix(uDimC, uBright, b * (0.5 + 0.5 * arm));
    a = (0.1 + 0.4 * b) * (0.3 + 0.7 * arm) * uGalaxy * 1.3;
    float inside = 1.0 - smoothstep(uOrbPx * 0.9, uOrbPx * 1.1, length(px - uCentre));
    a *= 1.0 - 0.95 * inside * step(q.z, 0.0);
    size = (1.2 + 1.4 * aB.x) * per;
  }
  float want = size * uDpr;
  float drawn = clamp(want, 1.5, uPmax);
  a *= uDim * ok * min(1.0, (want * want) / (drawn * drawn));
  vCol = col * a;
  gl_PointSize = drawn;
  gl_Position = a > 0.002 ? vec4(px.x / uRes.x * 2.0 - 1.0, 1.0 - px.y / uRes.y * 2.0, 0.0, 1.0) : vec4(2.0, 2.0, 2.0, 1.0);
}
