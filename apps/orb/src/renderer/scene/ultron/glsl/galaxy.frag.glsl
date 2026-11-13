#version 300 es
precision highp float;

in vec2 vUv;
out vec4 outColor;

const float PI = 3.14159265;

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

float turb(vec2 p) {
  float v = 0.0;
  float w = 0.55;
  for (int i = 0; i < 4; i++) {
    v += w * noise2(p);
    p = p * 2.07 + vec2(5.2, 1.3);
    w *= 0.48;
  }
  return v;
}

float armDistance(float th, float r, float twist) {
  float base = log(max(r, 0.02)) * twist;
  float d = mod(th - base + PI * 4.0, PI) - PI * 0.5;
  return d;
}

void main() {
  vec2 f = (vUv - 0.5) * 3.2;
  float r = length(f);
  float th = atan(f.y, f.x);
  float wob = (turb(f * 1.7 + 2.2) - 0.5) * 0.9;
  float d = armDistance(th + wob, r, 3.6);
  float width = 0.22 + 0.32 * smoothstep(0.2, 1.4, r);
  float armGlow = exp(-(d * d) / (width * width));
  float dustLane = exp(-pow((d + width * 0.55) / (width * 0.35), 2.0));
  float disk = exp(-r / 0.42);
  float bulge = exp(-7.67 * (pow(max(r, 0.0005) / 0.11, 0.25) - 1.0)) * 0.0045;
  float inner = smoothstep(0.06, 0.32, r);
  float reach = 1.0 - smoothstep(1.25, 1.55, r);
  vec3 warmCore = vec3(1.0, 0.86, 0.62);
  vec3 armBlue = vec3(0.78, 0.82, 0.95);
  vec3 diskTone = mix(vec3(1.0, 0.9, 0.78), vec3(0.82, 0.88, 1.0), smoothstep(0.35, 1.1, r));
  vec3 gal = warmCore * min(bulge, 1.4) * 0.5;
  gal += diskTone * disk * 0.32 * (0.85 + 0.3 * turb(f * 11.0));
  gal += armBlue * armGlow * smoothstep(0.12, 0.5, r) * exp(-r / 0.85) * 0.32 * (0.6 + 0.8 * turb(f * 6.3 + 9.1));
  float soot = clamp(dustLane * inner * (0.55 + 0.6 * turb(f * 9.0 + 4.0)) + smoothstep(0.55, 0.85, turb(f * 3.1 + 7.7)) * 0.1 * inner, 0.0, 1.0);
  gal *= mix(vec3(1.0), vec3(0.78, 0.46, 0.32), soot * 0.9);
  vec2 kp = f * 70.0;
  vec2 kc = floor(kp);
  vec2 kj = vec2(h21(kc + 2.9), h21(kc + 5.3));
  vec2 kd = kp - kc - 0.2 - 0.6 * kj;
  float hasKnot = step(1.0 - 0.35 * armGlow * smoothstep(0.3, 0.7, r) * reach, h21(kc + 7.7));
  float kTone = h21(kc + 3.3);
  vec3 kCol = kTone < 0.1 ? vec3(1.0, 0.6, 0.7) : mix(armBlue, vec3(0.92, 0.95, 1.0), kTone);
  gal += kCol * hasKnot * exp(-dot(kd, kd) * 30.0) * (0.08 + 0.2 * h21(kc + 1.9));
  vec2 sp = floor(f * 210.0);
  vec2 so = fract(f * 210.0) - 0.5;
  float star = step(1.0 - (0.025 + 0.12 * disk + 0.05 * armGlow), h21(sp + 13.1));
  gal += vec3(0.95, 0.93, 1.0) * star * exp(-dot(so, so) * 9.0) * 0.32 * inner;
  gal += diskTone * exp(-r / 1.1) * 0.035;
  gal *= reach;
  outColor = vec4(gal, soot * reach);
}
