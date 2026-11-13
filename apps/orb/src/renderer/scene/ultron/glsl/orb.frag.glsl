#version 300 es
precision mediump float;

in vec3 vCol;
out vec4 outColor;

void main() {
  vec2 q = gl_PointCoord * 2.0 - 1.0;
  float k = max(0.0, 1.0 - dot(q, q));
  outColor = vec4(vCol * (k * k), 0.0);
}
