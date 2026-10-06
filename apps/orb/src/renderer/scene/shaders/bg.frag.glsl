uniform vec2 uC;
uniform float uR;
uniform float uDpr;
uniform vec3 uBg;
uniform vec3 uMid;
uniform vec3 uFlareCol;
uniform float uLoad;
uniform float uEnergy;
uniform float uHeart;
uniform float uFlare;
uniform float uNight;
uniform float uDim;
uniform float uDither;

const vec3 LUMA = vec3(0.299, 0.587, 0.114);

float dither(vec2 p) {
  return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715))));
}

void main() {
  vec2 px = gl_FragCoord.xy / uDpr;
  vec2 q = (px - uC) / uR;
  float r = length(q);
  float out1 = max(r - 1.0, 0.0);
  vec3 col = uBg * (0.88 + 0.55 * exp(-r * 0.42));
  float halo = exp(-out1 * (3.8 - 1.6 * uLoad)) * (0.15 + 0.07 * uEnergy + 0.42 * uLoad) + exp(-out1 * 0.9) * (0.03 + 0.10 * uLoad);
  halo *= (1.0 - 0.5 * uNight) * (1.0 + 0.5 * uHeart);
  col += mix(uMid, uFlareCol, clamp(uFlare, 0.0, 1.0) * 0.8) * halo;
  float lum = dot(col, LUMA);
  col = mix(col, vec3(lum), clamp(0.8 * uDim, 0.0, 0.9));
  col *= (1.0 - 0.8 * uDim) * (1.0 - 0.33 * uNight);
  col += (dither(gl_FragCoord.xy + uDither * 61.0) - 0.5) / 255.0;
  gl_FragColor = vec4(max(col, 0.0), 1.0);
}
