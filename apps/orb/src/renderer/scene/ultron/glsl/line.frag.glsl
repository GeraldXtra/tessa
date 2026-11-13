#version 300 es
precision mediump float;

in vec3 vCol;
in float vEdge;
in float vHalf;
out vec4 outColor;

void main() {
  float cover = clamp(vHalf + 0.6 - abs(vEdge), 0.0, 1.0);
  outColor = vec4(vCol * cover, 0.0);
}
