#version 300 es
precision highp float;

in vec2 vUv;
uniform sampler2D uSrc;
uniform vec2 uCore;
uniform float uAspect;
uniform float uReach;
uniform float uHole;
uniform float uGain;
out vec4 outColor;

void main() {
  vec2 stride = (vUv - uCore) * (uReach / 16.0);
  vec2 uv = vUv;
  vec3 acc = vec3(0.0);
  float w = 1.0;
  float total = 0.0;
  for (int i = 0; i < 16; i++) {
    float keep = 1.0 - smoothstep(uHole * 0.5, uHole * 1.9, length((uv - uCore) * vec2(uAspect, 1.0)));
    acc += texture(uSrc, uv).rgb * (w * keep);
    total += w;
    w *= 0.93;
    uv -= stride;
  }
  outColor = vec4(acc / total * uGain, 1.0);
}
