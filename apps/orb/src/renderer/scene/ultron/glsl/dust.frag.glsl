#version 300 es
precision mediump float;

in vec3 vCol;
in float vKind;
out vec4 outColor;

void main() {
  vec2 q = gl_PointCoord * 2.0 - 1.0;
  float d2 = dot(q, q);
  float m;
  if (vKind < 0.5) {
    m = exp(-d2 * 9.0) + 0.22 * max(0.0, 1.0 - d2);
  } else if (vKind < 1.5) {
    m = 1.0 - smoothstep(0.35, 1.0, d2);
  } else {
    m = exp(-d2 * 3.6) * step(d2, 1.0);
  }
  outColor = vec4(vCol * m, 0.0);
}
