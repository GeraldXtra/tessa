#version 300 es
precision highp float;

in vec2 vUv;
uniform sampler2D uScene;
uniform sampler2D uBloom;
uniform sampler2D uSpace;
uniform sampler2D uGalaxy;
uniform vec2 uCanvas;
uniform float uDpr;
uniform vec2 uOrbUv;
uniform float uOrbR;
uniform float uAspect;
uniform mat2 uGalM;
uniform float uGalK;
uniform float uSceneK;
uniform float uBloomK;
uniform float uSpaceK;
uniform vec3 uGrade;
uniform float uAberr;
uniform float uFlicker;
uniform float uOverlays;
uniform float uFade;
out vec4 outColor;

float h21(vec2 p) {
  vec3 q = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
  q += dot(q, q.yzx + 33.33);
  return fract((q.x + q.y) * q.z);
}

void main() {
  vec2 uv = vUv;
  vec4 sc = texture(uScene, uv);
  vec3 s = sc.rgb;
  if (uAberr > 0.0) {
    vec2 off = (uv - 0.5) * uAberr * length(uv - 0.5);
    s.r = texture(uScene, uv + off).r;
    s.b = texture(uScene, uv - off * 0.5).b;
  }
  vec3 b = texture(uBloom, uv).rgb;
  vec3 c = (s * uSceneK + b * uBloomK) * uGrade * uFlicker;
  c = c / (1.0 + max(c - 0.85, 0.0) * 0.9);
  c = min(c, vec3(1.0));
  vec2 dd = (uv - uOrbUv) * vec2(uAspect, 1.0);
  float rq = length(dd) / max(uOrbR, 0.0001);
  vec3 g = texture(uSpace, uv).rgb * uSpaceK;
  vec2 gq = uGalM * (dd / max(uOrbR, 0.0001));
  vec2 guv = gq / 3.2 + 0.5;
  vec4 gx = texture(uGalaxy, clamp(guv, 0.0, 1.0));
  float inside = step(0.0, guv.x) * step(guv.x, 1.0) * step(0.0, guv.y) * step(guv.y, 1.0);
  g = g * (1.0 - 0.6 * gx.a * uGalK * inside) + gx.rgb * uGalK * inside;
  g *= 1.0 - clamp(sc.a, 0.0, 1.0);
  g *= 1.0 - 0.5 * (1.0 - smoothstep(0.7, 1.0, rq));
  c += g * (1.0 - c);
  if (uOverlays > 0.5) {
    vec2 vq = (uv - 0.5) * 2.0;
    float v = length(vq) / 1.41421;
    c *= 1.0 - 0.42 * clamp((v - 0.55) / 0.45, 0.0, 1.0);
    vec2 fp = gl_FragCoord.xy;
    float grain = h21(floor(fp / 1.5) + 0.37);
    c = 1.0 - (1.0 - c) * (1.0 - 0.06 * grain);
    float row = mod(floor((uCanvas.y - fp.y) / max(uDpr, 0.5)), 4.0);
    c *= row >= 2.0 ? 0.979 : 1.0;
  }
  c *= uFade;
  c += (h21(gl_FragCoord.xy * 1.37 + 0.5) - 0.5) / 255.0;
  outColor = vec4(c, 1.0);
}
