#version 300 es
precision highp float;

layout(location = 0) in vec2 aC;
layout(location = 1) in vec4 aA;
layout(location = 2) in vec4 aB;
layout(location = 3) in vec4 aG;

uniform vec2 uRes;
uniform vec2 uCentre;
uniform float uUnit;
uniform mat3 uGM[12];
uniform vec4 uGS[12];
uniform vec4 uGX[12];
uniform vec3 uPal[5];
uniform float uTrace;
uniform vec2 uPulse;
uniform vec2 uSpec;
uniform vec3 uOcc;
uniform float uWidth;

out vec3 vCol;
out float vEdge;
out float vHalf;

const float TAU = 6.28318530718;

float rnd(float x) {
  x = fract(x * 0.1031);
  x *= x + 33.33;
  x *= x + x;
  return fract(x);
}

vec3 project(vec3 local, int g, out float z) {
  vec4 gs = uGS[g];
  vec4 gx = uGX[g];
  if (gx.w > 0.5) {
    z = 0.0;
    return vec3(local.xy, 1.0);
  }
  vec3 p = uGM[g] * (local * gs.x);
  z = p.z;
  float dz = gs.y - p.z;
  float per = gs.y / max(dz, 0.1);
  return vec3(uCentre + vec2(p.x, -p.y) * uUnit * gx.x * per, step(0.1, dz));
}

void main() {
  int g = int(aG.x + 0.5);
  float kind = aG.y;
  vec3 a = aA.xyz;
  vec3 b = aB.xyz;
  float level = 0.0;
  if (kind > 4.5 && kind < 5.5) {
    float ang = atan(b.y, b.x);
    float bin = floor(fract(ang / TAU + 1.0) * 144.0);
    float e = 0.5 + 0.5 * sin(ang * 3.0 + uSpec.y * 5.3) * sin(ang * 7.0 - uSpec.y * 3.1 + rnd(bin) * 6.0);
    level = uSpec.x * (0.25 + 0.75 * e * e) * (0.6 + 0.4 * rnd(bin + 17.0));
    b += normalize(b) * (0.012 + level * 0.05);
  }
  float za;
  float zb;
  vec3 pa = project(a, g, za);
  vec3 pb = project(b, g, zb);
  vec2 d = pb.xy - pa.xy;
  float len = length(d);
  vec2 dir = len > 0.0001 ? d / len : vec2(1.0, 0.0);
  vec2 nrm = vec2(-dir.y, dir.x);
  float hw = uWidth * 0.5;
  vec2 px = mix(pa.xy, pb.xy, aC.x) + nrm * aC.y * (hw + 1.0);
  vEdge = aC.y * (hw + 1.0);
  vHalf = hw;
  int tone = int(aA.w + 0.5);
  vec3 col = uPal[0];
  if (tone == 1) col = uPal[1];
  else if (tone == 2) col = uPal[2];
  else if (tone == 3) col = uPal[3];
  else if (tone == 4) col = uPal[4];
  vec4 gs = uGS[g];
  vec4 gx = uGX[g];
  float z = mix(za, zb, aC.x);
  vec3 lp = mix(a, b, aC.x);
  float r3 = max(length(uGM[g] * (lp * gs.x)), 0.0001);
  float front = smoothstep(-0.85, 0.85, z / r3);
  float depth = mix(1.0, 0.45 + 0.6 * front, gs.w);
  col *= mix(vec3(1.0), vec3(0.95, 0.7, 0.55), (1.0 - front) * gs.w);
  float boost = 0.0;
  float shown = 1.0;
  float occ = 1.0;
  if (kind > 2.5 && kind < 3.5 && uPulse.y > 0.001) {
    float at = fract(atan(lp.y, lp.x) / TAU + 0.5);
    float gap = fract(fract(uPulse.x) - at);
    boost = exp(-gap * 14.0) * clamp(gap * 60.0, 0.0, 1.0) * uPulse.y;
  }
  if (kind > 2.5 && kind < 5.5) {
    vec2 rel = vec2(lp.x, lp.y);
    shown = step(fract(0.25 - atan(rel.y, rel.x) / TAU), uTrace);
  }
  float trail = 0.0;
  if (kind > 5.5) {
    vec2 here = mix(pa.xy, pb.xy, aC.x);
    if (z < 0.0) occ = clamp((length(here - uOcc.xy) - uOcc.z) / 4.0 + 0.4, 0.0, 1.0);
    float va = atan(lp.z, lp.x);
    if (gx.y > -50.0) trail += exp(-mod(va - gx.y, TAU) / TAU * 9.0);
    if (gx.z > -50.0) trail += exp(-mod(va - gx.z, TAU) / TAU * 9.0);
  }
  col = mix(col, uPal[4], clamp(boost + level * 0.9 + trail * 0.6, 0.0, 1.0));
  float alpha = aB.w * gs.z * depth * shown * occ * pa.z * pb.z * (1.0 + 2.0 * boost + 1.5 * level + 2.6 * trail);
  vCol = col * alpha;
  gl_Position = alpha > 0.0005 ? vec4(px.x / uRes.x * 2.0 - 1.0, 1.0 - px.y / uRes.y * 2.0, 0.0, 1.0) : vec4(2.0, 2.0, 2.0, 1.0);
}
