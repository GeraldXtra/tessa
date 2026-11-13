export type M3 = Float32Array;

export function ident(): M3 {
  return new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1]);
}

export function rotX(a: number): M3 {
  const c = Math.cos(a);
  const s = Math.sin(a);
  return new Float32Array([1, 0, 0, 0, c, s, 0, -s, c]);
}

export function rotY(a: number): M3 {
  const c = Math.cos(a);
  const s = Math.sin(a);
  return new Float32Array([c, 0, -s, 0, 1, 0, s, 0, c]);
}

export function rotZ(a: number): M3 {
  const c = Math.cos(a);
  const s = Math.sin(a);
  return new Float32Array([c, s, 0, -s, c, 0, 0, 0, 1]);
}

export function mul(a: M3, b: M3): M3 {
  const o = new Float32Array(9);
  for (let col = 0; col < 3; col++) {
    for (let row = 0; row < 3; row++) {
      o[col * 3 + row] = (a[row] ?? 0) * (b[col * 3] ?? 0) + (a[3 + row] ?? 0) * (b[col * 3 + 1] ?? 0) + (a[6 + row] ?? 0) * (b[col * 3 + 2] ?? 0);
    }
  }
  return o;
}

export function apply(m: M3, x: number, y: number, z: number): [number, number, number] {
  return [
    (m[0] ?? 0) * x + (m[3] ?? 0) * y + (m[6] ?? 0) * z,
    (m[1] ?? 0) * x + (m[4] ?? 0) * y + (m[7] ?? 0) * z,
    (m[2] ?? 0) * x + (m[5] ?? 0) * y + (m[8] ?? 0) * z,
  ];
}

export function spinAbout(x: number, y: number, z: number, ax: number, ay: number, az: number, ang: number): [number, number, number] {
  const d = ax * x + ay * y + az * z;
  const px = ax * d;
  const py = ay * d;
  const pz = az * d;
  const qx = x - px;
  const qy = y - py;
  const qz = z - pz;
  const c = Math.cos(ang);
  const s = Math.sin(ang);
  const cx = ay * qz - az * qy;
  const cy = az * qx - ax * qz;
  const cz = ax * qy - ay * qx;
  return [px + qx * c + cx * s, py + qy * c + cy * s, pz + qz * c + cz * s];
}
