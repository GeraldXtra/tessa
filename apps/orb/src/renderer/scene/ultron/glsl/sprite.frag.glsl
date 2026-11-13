#version 300 es
precision mediump float;

in vec2 vK;
in vec4 vShape;
in vec3 vCol;
out vec4 outColor;

void main() {
  float d = length(vK);
  float shape = vShape.w;
  float m;
  if (shape < 0.5) {
    float t = clamp(1.0 - d, 0.0, 1.0);
    m = pow(t, vShape.z);
  } else if (shape < 1.5) {
    float x = (d - 0.82) / 0.06;
    m = exp(-x * x) * (1.0 - smoothstep(0.94, 1.0, d));
  } else {
    m = (1.0 - smoothstep(0.6, 1.0, d)) * (0.7 + 0.3 * (1.0 - d * d));
  }
  outColor = vec4(vCol * (m * vShape.y), 0.0);
}
