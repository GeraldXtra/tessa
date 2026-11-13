#version 300 es
precision mediump float;

in vec2 vUv;
in float vShow;
uniform sampler2D uAtlas;
uniform vec3 uCol;
uniform float uInt;
out vec4 outColor;

void main() {
  float a = texture(uAtlas, vUv).a;
  outColor = vec4(uCol * (a * vShow * uInt), 0.0);
}
