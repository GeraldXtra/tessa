#version 300 es
precision highp float;

in vec2 vUv;
uniform vec2 uSize;
uniform float uDpr;
uniform vec3 uBase;
uniform vec3 uCool;
uniform vec3 uWarm;
out vec4 outColor;

float h21(vec2 p) {
  vec3 q = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
  q += dot(q, q.yzx + 33.33);
  return fract((q.x + q.y) * q.z);
}

float noise2(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), f.x), mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), f.x), f.y);
}

float clouds(vec2 p) {
  float v = 0.0;
  float w = 0.5;
  for (int i = 0; i < 5; i++) {
    v += w * noise2(p);
    p = vec2(p.x * 1.7 - p.y * 1.1, p.x * 1.1 + p.y * 1.7) + 4.3;
    w *= 0.5;
  }
  return v;
}

vec3 tint(float t) {
  vec3 warm = vec3(1.0, 0.62, 0.38);
  vec3 white = vec3(1.0, 0.95, 0.9);
  vec3 blue = vec3(0.62, 0.74, 1.0);
  return t < 0.35 ? mix(warm, white, t / 0.35) : mix(white, blue, smoothstep(0.35, 0.9, t));
}

vec3 layer(vec2 px, float cell, float chance, float seed, float sharp, float glow, float gain) {
  vec2 c = floor(px / cell);
  if (h21(c + seed) >= chance) return vec3(0.0);
  vec2 at = (c + 0.5 + 0.7 * (vec2(h21(c + seed + 3.1), h21(c + seed + 6.7)) - 0.5)) * cell;
  vec2 d = (px - at) * uDpr;
  float r2 = dot(d, d);
  float b = h21(c + seed + 9.9);
  return tint(h21(c + seed + 12.3)) * gain * (0.35 + 0.65 * b * b) * (exp(-r2 / sharp) + glow * exp(-sqrt(r2) / (sharp * 3.0)));
}

void main() {
  vec2 px = vUv * uSize;
  vec3 col = uBase;
  float big = clouds(px / 520.0 + 2.7);
  float mid = clouds(px / 190.0 + 9.1);
  float lane = clouds(px / 260.0 + 21.3);
  col += uCool * smoothstep(0.42, 0.86, big) * 0.9;
  col += uWarm * smoothstep(0.5, 0.92, mid) * 0.35;
  col *= 1.0 - 0.55 * smoothstep(0.58, 0.82, lane) * smoothstep(0.4, 0.7, big);
  vec3 stars = vec3(0.0);
  stars += layer(px, 3.0, 0.045, 1.7, 0.6, 0.0, 0.22);
  stars += layer(px, 7.0, 0.09, 11.3, 0.8, 0.0, 0.45);
  stars += layer(px, 17.0, 0.16, 23.9, 1.1, 0.05, 0.7);
  stars += layer(px, 58.0, 0.3, 37.1, 1.8, 0.12, 0.85);
  stars += layer(px, 190.0, 0.42, 51.7, 3.2, 0.2, 1.0);
  col += stars * (0.75 + 0.5 * smoothstep(0.4, 0.8, big));
  col += (h21(px * 1.31 + 0.7) - 0.5) / 255.0;
  outColor = vec4(col, 1.0);
}
