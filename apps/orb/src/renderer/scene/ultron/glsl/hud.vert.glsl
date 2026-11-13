#version 300 es
precision highp float;

layout(location = 0) in vec2 aC;
layout(location = 1) in vec4 aT;
layout(location = 2) in vec4 aU;

uniform vec2 uRes;
uniform vec2 uCentre;
uniform float uUnit;
uniform float uRot;
uniform float uTrace;
uniform float uLift;

out vec2 vUv;
out float vShow;

const float TAU = 6.28318530718;

void main() {
  float group = aU.y;
  float th = aT.x + (group < 0.5 ? uRot : 0.0);
  vec2 dir = vec2(cos(th), sin(th));
  vec2 up = group < 0.5 ? dir : vec2(0.0, 1.0);
  vec2 right = vec2(up.y, -up.x);
  float h = aT.w * uLift;
  vec2 local = dir * aT.y + up * (aC.y * h * 0.5) + right * (aT.z * h * 0.56 + aC.x * h * 0.39);
  vec2 px = uCentre + vec2(local.x, -local.y) * uUnit;
  float cell = aU.x;
  float col = mod(cell, 16.0);
  float row = floor(cell / 16.0 + 0.001);
  vUv = vec2((col + 0.5 + aC.x * 0.5) / 16.0, (row + 0.5 - aC.y * 0.5) / 3.0);
  vShow = step(fract(0.25 - atan(dir.y, dir.x) / TAU), uTrace);
  gl_Position = vec4(px.x / uRes.x * 2.0 - 1.0, 1.0 - px.y / uRes.y * 2.0, 0.0, 1.0);
}
