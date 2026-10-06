vec4 nwrap4(vec4 x) {
  return x - floor(x * (1.0 / 289.0)) * 289.0;
}

vec3 nwrap3(vec3 x) {
  return x - floor(x * (1.0 / 289.0)) * 289.0;
}

vec4 nperm(vec4 x) {
  return nwrap4((x * 34.0 + 10.0) * x);
}

float simplex3(vec3 v) {
  vec3 s = floor(v + (v.x + v.y + v.z) * (1.0 / 3.0));
  vec3 d0 = v - s + (s.x + s.y + s.z) * (1.0 / 6.0);
  vec3 g = step(d0.yzx, d0.xyz);
  vec3 rk = g + 1.0 - g.zxy;
  vec3 c1 = step(1.5, rk);
  vec3 c2 = step(0.5, rk);
  vec3 d1 = d0 - c1 + (1.0 / 6.0);
  vec3 d2 = d0 - c2 + (1.0 / 3.0);
  vec3 d3 = d0 - 0.5;
  vec3 si = nwrap3(s);
  vec4 h = nperm(si.z + vec4(0.0, c1.z, c2.z, 1.0));
  h = nperm(h + si.y + vec4(0.0, c1.y, c2.y, 1.0));
  h = nperm(h + si.x + vec4(0.0, c1.x, c2.x, 1.0));
  vec4 gz = 1.0 - (2.0 * h + 1.0) * (1.0 / 289.0);
  vec4 gr = sqrt(max(1.0 - gz * gz, 0.0));
  vec4 ga = fract(h * 0.381966011) * 6.28318531;
  vec4 gx = gr * cos(ga);
  vec4 gy = gr * sin(ga);
  vec4 w = max(0.6 - vec4(dot(d0, d0), dot(d1, d1), dot(d2, d2), dot(d3, d3)), 0.0);
  w *= w;
  w *= w;
  vec4 pr = vec4(
    gx.x * d0.x + gy.x * d0.y + gz.x * d0.z,
    gx.y * d1.x + gy.y * d1.y + gz.y * d1.z,
    gx.z * d2.x + gy.z * d2.y + gz.z * d2.z,
    gx.w * d3.x + gy.w * d3.y + gz.w * d3.z
  );
  return 39.81 * dot(w, pr);
}
