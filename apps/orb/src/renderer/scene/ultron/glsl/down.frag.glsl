#version 300 es
precision highp float;

in vec2 vUv;
uniform sampler2D uSrc;
uniform vec2 uTexel;
out vec4 outColor;

void main() {
  vec3 c = texture(uSrc, vUv).rgb * 4.0;
  c += texture(uSrc, vUv + vec2(-1.0, -1.0) * uTexel).rgb;
  c += texture(uSrc, vUv + vec2(1.0, -1.0) * uTexel).rgb;
  c += texture(uSrc, vUv + vec2(-1.0, 1.0) * uTexel).rgb;
  c += texture(uSrc, vUv + vec2(1.0, 1.0) * uTexel).rgb;
  outColor = vec4(c * 0.125, 1.0);
}
