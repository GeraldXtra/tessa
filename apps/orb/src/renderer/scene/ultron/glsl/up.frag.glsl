#version 300 es
precision highp float;

in vec2 vUv;
uniform sampler2D uSrc;
uniform vec2 uTexel;
uniform float uGain;
out vec4 outColor;

void main() {
  vec2 t = uTexel;
  vec3 c = texture(uSrc, vUv + vec2(-2.0, 0.0) * t).rgb;
  c += texture(uSrc, vUv + vec2(2.0, 0.0) * t).rgb;
  c += texture(uSrc, vUv + vec2(0.0, -2.0) * t).rgb;
  c += texture(uSrc, vUv + vec2(0.0, 2.0) * t).rgb;
  c += texture(uSrc, vUv + vec2(-1.0, -1.0) * t).rgb * 2.0;
  c += texture(uSrc, vUv + vec2(1.0, -1.0) * t).rgb * 2.0;
  c += texture(uSrc, vUv + vec2(-1.0, 1.0) * t).rgb * 2.0;
  c += texture(uSrc, vUv + vec2(1.0, 1.0) * t).rgb * 2.0;
  outColor = vec4(c / 12.0 * uGain, 1.0);
}
