#version 300 es
precision highp float;

layout(location = 0) in vec2 aC;
layout(location = 1) in vec4 aS;
layout(location = 2) in vec4 aW;

uniform vec2 uRes;
uniform vec2 uCentre;
uniform float uUnit;
uniform float uWind;
uniform float uSwap;
uniform float uWords;
uniform float uRows;

out vec2 vUv;
out float vShow;
out float vTint;

void main() {
  float f = fract(aS.z + uWind * aS.y);
  float r = 0.75 + f * 2.0;
  vec2 dir = vec2(cos(aS.x), sin(aS.x));
  vec2 centre = uCentre + vec2(dir.x, -dir.y) * r * uUnit;
  vec2 half_ = vec2(aS.w * 4.6, aS.w * 0.75) * uUnit * 0.62;
  vec2 px = centre + vec2(aC.x, -aC.y) * half_;
  float flick = fract(sin(aS.x * 41.3 + aS.z * 17.9) * 9931.7);
  float cell = mod(aW.x + step(0.6, flick) * floor(uSwap * (0.4 + 1.6 * flick) + flick * 23.0), uWords);
  float col = mod(cell, 4.0);
  float row = floor(cell / 4.0 + 0.001);
  vUv = vec2((col + 0.5 + aC.x * 0.5) / 4.0, (row + 0.5 - aC.y * 0.5) / uRows);
  vShow = aW.y * smoothstep(0.0, 0.15, f) * (1.0 - smoothstep(0.45, 1.0, f));
  vTint = aW.z;
  gl_Position = vec4(px.x / uRes.x * 2.0 - 1.0, 1.0 - px.y / uRes.y * 2.0, 0.0, 1.0);
}
