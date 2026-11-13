#version 300 es
precision highp float;

in vec2 vQ;
in float vRad;
in vec4 vL;
in vec4 vA;
in vec4 vB;
in vec4 vAtm;
in vec4 vMisc;

uniform vec3 uSun;
uniform float uGain;

out vec4 outColor;

float cellHash(vec3 p) {
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}

float valueNoise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  float n000 = cellHash(i);
  float n100 = cellHash(i + vec3(1.0, 0.0, 0.0));
  float n010 = cellHash(i + vec3(0.0, 1.0, 0.0));
  float n110 = cellHash(i + vec3(1.0, 1.0, 0.0));
  float n001 = cellHash(i + vec3(0.0, 0.0, 1.0));
  float n101 = cellHash(i + vec3(1.0, 0.0, 1.0));
  float n011 = cellHash(i + vec3(0.0, 1.0, 1.0));
  float n111 = cellHash(i + vec3(1.0, 1.0, 1.0));
  return mix(mix(mix(n000, n100, f.x), mix(n010, n110, f.x), f.y), mix(mix(n001, n101, f.x), mix(n011, n111, f.x), f.y), f.z);
}

float layered(vec3 p) {
  return valueNoise(p) * 0.55 + valueNoise(p * 2.03 + 7.1) * 0.3 + valueNoise(p * 4.1 + 13.7) * 0.15;
}

void main() {
  vec2 q = vQ;
  float r = length(q);
  float aa = 1.4 / max(vRad, 1.0);
  vec3 L = vL.xyz;
  float ringOn = vMisc.z;
  float ringY = q.y / 0.32;
  float ringR = length(vec2(q.x, ringY));
  float ringMask = ringOn * smoothstep(1.38, 1.38 + aa * 2.0, ringR) * (1.0 - smoothstep(2.2 - aa * 2.0, 2.2, ringR));
  float stripes = 0.55 + 0.45 * sin(ringR * 36.0) * sin(ringR * 9.0 + 1.3);
  vec3 ringCol = mix(vA.rgb, vB.rgb, 0.45) * stripes * (0.35 + 0.65 * clamp(0.5 - 0.5 * L.z, 0.0, 1.0));
  float back = step(0.0, q.y) * ringMask * 0.82;
  float frontRing = step(q.y, 0.0) * ringMask * 0.82;
  vec3 col = ringCol;
  float alpha = back;
  if (r < 1.0 + aa) {
    float z = sqrt(max(1.0 - r * r, 0.0));
    vec3 n = vec3(q, z);
    float lat = n.y;
    float cl = sqrt(max(1.0 - lat * lat, 0.0));
    float lon = atan(n.z, n.x) + vMisc.x;
    vec3 sp = vec3(cl * cos(lon), lat, cl * sin(lon));
    float field = layered(sp * vB.a + vMisc.y);
    float stripe = 0.5 + 0.5 * sin(lat * vA.a + (layered(sp * 2.4 + vMisc.y) - 0.5) * 4.0);
    float pick = vA.a > 0.5 ? stripe * 0.68 + field * 0.32 : field;
    vec3 surface = mix(vA.rgb, vB.rgb, clamp(pick * 1.25 - 0.12, 0.0, 1.0));
    float ndl = dot(n, L);
    float day = smoothstep(-0.1, 0.3, ndl) * max(ndl, 0.0) * 1.2 + 0.04;
    vec3 lit = surface * day;
    float rim = pow(1.0 - z, 2.4);
    vec2 toward = L.xy / max(length(L.xy), 0.0001);
    float lean = max(dot(q / max(r, 0.0001), toward), 0.0);
    float backlit = pow(max(-L.z, 0.0), 3.0);
    lit += vAtm.rgb * vAtm.a * rim * (0.25 + 0.85 * lean * max(ndl + 0.3, 0.0) + 1.4 * backlit);
    float cover = 1.0 - smoothstep(1.0 - aa, 1.0 + aa, r);
    col = mix(col * alpha, lit, cover);
    alpha = max(alpha, cover);
    col = alpha > 0.0 ? col / max(alpha, 0.0001) : col;
  }
  col = mix(col, ringCol, frontRing);
  alpha = max(alpha, frontRing);
  if (vMisc.w > 0.5) alpha *= smoothstep(uSun.z - 1.0, uSun.z + 1.5, length(gl_FragCoord.xy - uSun.xy));
  alpha *= vL.w;
  outColor = vec4(col * alpha * uGain, alpha);
}
