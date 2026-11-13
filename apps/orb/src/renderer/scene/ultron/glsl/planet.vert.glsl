#version 300 es
precision highp float;

layout(location = 0) in vec2 aC;
layout(location = 1) in vec4 aP0;
layout(location = 2) in vec4 aP1;
layout(location = 3) in vec4 aP2;
layout(location = 4) in vec4 aP3;
layout(location = 5) in vec4 aP4;
layout(location = 6) in vec4 aP5;

uniform vec2 uRes;

out vec2 vQ;
out float vRad;
out vec4 vL;
out vec4 vA;
out vec4 vB;
out vec4 vAtm;
out vec4 vMisc;

void main() {
  float ext = aP0.w;
  vec2 px = aP0.xy + aC * ext * aP0.z;
  vQ = vec2(aC.x, -aC.y) * ext;
  vRad = aP0.z;
  vL = aP1;
  vA = aP2;
  vB = aP3;
  vAtm = aP4;
  vMisc = aP5;
  gl_Position = aP1.w > 0.004 ? vec4(px.x / uRes.x * 2.0 - 1.0, 1.0 - px.y / uRes.y * 2.0, 0.0, 1.0) : vec4(2.0, 2.0, 2.0, 1.0);
}
