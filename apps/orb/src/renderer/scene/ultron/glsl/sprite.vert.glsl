#version 300 es
precision highp float;

layout(location = 0) in vec2 aC;
layout(location = 1) in vec4 aS0;
layout(location = 2) in vec4 aS1;
layout(location = 3) in vec4 aS2;

uniform vec2 uRes;

out vec2 vK;
out vec4 vShape;
out vec3 vCol;

void main() {
  vec2 q = aC * aS0.zw;
  float c = cos(aS1.x);
  float s = sin(aS1.x);
  vec2 px = aS0.xy + vec2(c * q.x - s * q.y, s * q.x + c * q.y);
  vK = aC;
  vShape = aS1;
  vCol = aS2.rgb;
  gl_Position = aS1.y > 0.0005 ? vec4(px.x / uRes.x * 2.0 - 1.0, 1.0 - px.y / uRes.y * 2.0, 0.0, 1.0) : vec4(2.0, 2.0, 2.0, 1.0);
}
