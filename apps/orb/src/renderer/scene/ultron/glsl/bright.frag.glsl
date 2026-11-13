#version 300 es
precision highp float;

in vec2 vUv;
uniform sampler2D uSrc;
uniform vec2 uTexel;
uniform float uThreshold;
uniform float uScale;
out vec4 outColor;

void main() {
  vec3 c = texture(uSrc, vUv + vec2(-0.5, -0.5) * uTexel).rgb;
  c += texture(uSrc, vUv + vec2(0.5, -0.5) * uTexel).rgb;
  c += texture(uSrc, vUv + vec2(-0.5, 0.5) * uTexel).rgb;
  c += texture(uSrc, vUv + vec2(0.5, 0.5) * uTexel).rgb;
  c *= 0.25 * uScale;
  float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
  float k = smoothstep(uThreshold, uThreshold + 0.25, l);
  outColor = vec4(c * k, 1.0);
}
