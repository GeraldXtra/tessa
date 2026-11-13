#version 300 es
precision mediump float;

in vec2 vUv;
in float vShow;
in float vTint;
uniform sampler2D uAtlas;
uniform vec3 uTintA;
uniform vec3 uTintB;
uniform float uInt;
out vec4 outColor;

void main() {
  float a = texture(uAtlas, vUv).a;
  outColor = vec4(mix(uTintA, uTintB, vTint) * (a * vShow * uInt), 0.0);
}
