/**
 * THE TWO BACKGROUND SPHERES — the reference's other companions.
 *
 * ─── what they are, and why they are not fabricated data ───
 * Gerald ruled: "The 2 orb spheres at the back should be there and I want to
 * later make them switching and have different things to do. But for now, make
 * sure to add them." They assert nothing — no count, no status, no number. They
 * are the visual presence of two other companions in a system designed for
 * several, and a shape is not a claim. I agree with the ruling; the
 * no-fabricated-data rule is about asserting values the daemon has not sent,
 * and these assert none.
 *
 * ─── their colours are MEASURED, and they do NOT follow the theme ───
 * Sampled across the sixteen reference photographs — none of which is a direct
 * capture, so the method is agreement across independent shots rather than
 * trust in any one:
 *
 *   left   hue 293-307 deg   magenta, dim      (measured at the sphere itself)
 *   right  hue 152.7 deg     green, dim        (measured at the sphere itself)
 *   main   hue 307.5-312.5   magenta, bright   (16/16 agree, spread 10 deg)
 *
 * Those are their identities, not the palette's — switching the active theme
 * must not repaint them, or three companions become one companion three times.
 *
 * ─── they are ONE MORE DRAW CALL EACH, not one more context ───
 * Added as extra `Points` in the SAME scene with the SAME shader program and
 * their own uniform objects. Three WebGL contexts would have been the obvious
 * shape and the wrong one: browsers cap live contexts and drop the oldest,
 * which would be the main sphere's.
 */

import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  LinearSRGBColorSpace,
  Points,
  ShaderMaterial,
  Vector3,
  type Scene,
} from 'three';

import vertexShader from './shaders/particles.vert.glsl?raw';
import fragmentShader from './shaders/particles.frag.glsl?raw';

/**
 * ─── 2,200 -> 1,150, AND THE ARITHMETIC HAD THE SIGN RIGHT AND THE SCALE WRONG ───
 *
 * The old note reasoned: "they are roughly a fifth of the main sphere's
 * diameter on screen, so the same density needs a twenty-fifth of the count."
 * That is the correct rule and 2,200 is not what it produces. A twenty-fifth of
 * the count would have been density-matched to the OLD 8,000-particle main
 * sphere; against today's 15,600 at a 0.24 scale it is 2.7x too many, and 2.7x
 * the density of a field that is already at the edge of resolving is a field
 * that does not resolve at all. That is the "fuzzy patches with no visible
 * structure" complaint, measured.
 *
 * ─── WHAT THE REFERENCE'S COMPANIONS ACTUALLY ARE ───
 * Measured on image7, the only frame where both are fully in shot, with the
 * same connected-component instrument as the main sphere and everything
 * converted back to REAL screen pixels:
 *
 *                     diameter   particle fwhm   density per 100x100 px
 *   main sphere         490 px       4.8 px              61
 *   right companion     156 px       4.0 px              52
 *   left  companion     120 px       3.5 px              18
 *
 * The companions keep the main sphere's ABSOLUTE grain. They are not scaled-
 * down copies of it — a scaled-down copy shrinks its dots with the sphere and
 * turns to mush, which is precisely what this build was doing. They are the
 * same material at a smaller extent, which is what "the main sphere seen from
 * further away" actually looks like.
 *
 * So the count is set by AREA, not by taste: 15,600 particles over the main
 * sphere's projected area, at the companions' mean projected area (scales 0.24
 * and 0.30 of the main radius), is ~1,130. 1,150.
 */
const COMPANION_PARTICLES = 1_150;

/**
 * Measured from the reference and held in tokens.json, not here.
 *
 * CONTRACT §9: no surface hard-codes a hex. These four went into the token file
 * under the round's scoped exception precisely because they ARE colour values
 * — the gate caught them as literals and it was right to.
 */
const COMPANION_TOKENS = {
  left: { hot: '--companion-left-hot', cool: '--companion-left-cool' },
  right: { hot: '--companion-right-hot', cool: '--companion-right-cool' },
} as const;

/**
 * Read off the document's computed style, the same single source the sphere's
 * own palette uses — so a token change repaints them with no code change.
 */
function tokenColour(name: string): Color {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const c = new Color();
  if (raw) {
    // LinearSRGBColorSpace, not the default: a raw ShaderMaterial writing
    // gl_FragColor never runs three's <colorspace_fragment>, so converting here
    // would double-apply the transfer function.
    c.setStyle(raw, LinearSRGBColorSpace);
    return c;
  }
  // The token is missing, which means the generated stylesheet did not load —
  // a build fault, not a runtime condition. Numeric mid-grey rather than a hex
  // literal, because CONTRACT §9 admits no exception for a fallback and the
  // contract gate is right to refuse one.
  return c.setRGB(0.5, 0.5, 0.5);
}

export type CompanionSide = 'left' | 'right';

export interface Companion {
  readonly side: CompanionSide;
  /** Where its centre sits, as a fraction of the canvas. Set by the caller. */
  place(fx: number, fy: number, scale: number): void;
  /** Advance its own clock. Called once per accepted frame. */
  step(dtMs: number, sizeScale: number): void;
  dispose(): void;
}

function lattice(count: number, jitter: number): BufferGeometry {
  const positions = new Float32Array(count * 3);
  const seeds = new Float32Array(count);
  // Same Fibonacci lattice as the main sphere, and the same TANGENTIAL jitter —
  // one construction, so the two read as the same KIND of object at a different
  // size. A companion built on a clean lattice beside a jittered main sphere
  // would read as a different material, which is exactly the "fuzzy patch with
  // no structure" complaint arriving from the other direction.
  const spacing = 3.8093 / Math.sqrt(Math.max(count, 1));
  const sigma = Math.max(0, jitter) * spacing;
  let rngState = 0x85ebca6b;
  const next = (): number => {
    rngState = (Math.imul(rngState, 1664525) + 1013904223) >>> 0;
    return (rngState >>> 8) / 16777216;
  };
  const gauss = (): number => {
    const u = Math.max(next(), 1e-9);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * next());
  };
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = i * Math.PI * (3 - Math.sqrt(5));
    let px = Math.cos(theta) * r;
    let py = y;
    let pz = Math.sin(theta) * r;
    if (sigma > 0) {
      const ax = Math.abs(py) > 0.9 ? 1 : 0;
      const ay = Math.abs(py) > 0.9 ? 0 : 1;
      let t1x = ay * pz;
      let t1y = -ax * pz;
      let t1z = ax * py - ay * px;
      const t1n = Math.hypot(t1x, t1y, t1z) || 1;
      t1x /= t1n;
      t1y /= t1n;
      t1z /= t1n;
      const t2x = py * t1z - pz * t1y;
      const t2y = pz * t1x - px * t1z;
      const t2z = px * t1y - py * t1x;
      const g1 = gauss() * sigma;
      const g2 = gauss() * sigma;
      px += t1x * g1 + t2x * g2;
      py += t1y * g1 + t2y * g2;
      pz += t1z * g1 + t2z * g2;
      const n = Math.hypot(px, py, pz) || 1;
      px /= n;
      py /= n;
      pz /= n;
    }
    positions[i * 3] = px;
    positions[i * 3 + 1] = py;
    positions[i * 3 + 2] = pz;
    seeds[i] = (i * 0.618033988749895) % 1;
  }
  const g = new BufferGeometry();
  g.setAttribute('position', new BufferAttribute(positions, 3));
  g.setAttribute('aSeed', new BufferAttribute(seeds, 1));
  return g;
}

/**
 * @param worldPerPixelAt a getter, not a value: the caller's projection changes
 * on every resize and a captured number would silently go stale.
 */
export function createCompanion(
  scene: Scene,
  side: CompanionSide,
  worldPerPixelAt: () => number,
  canvasSize: () => { w: number; h: number },
  /** DEV ONLY. `--force-count=<main>,<companion>`; null uses the constant. */
  countOverride: number | null = null,
  /** DEV ONLY. Multiplier on the companion point size, for the sweep. */
  sizeMul: number | null = null,
  /** Tangential lattice jitter, as a multiple of the lattice's own spacing. */
  latticeJitter = 0,
): Companion {
  const tok = COMPANION_TOKENS[side];
  /**
   * THE SHELL PARAMETERS TRACK THE MAIN SPHERE'S, and they were drifting.
   *
   * These were copied by hand from the main sphere's defaults and then the main
   * sphere's were refitted twice without them. A companion running the old
   * rim gain, the old lambert and the old energy spread beside a refitted main
   * sphere reads as a different MATERIAL, not as the same object further away —
   * which is exactly the "fuzzy patches with no visible structure" complaint.
   * Every value below now matches the corresponding *_DEFAULT in
   * sphere-engine.ts. If those move again, these move with them.
   */
  const uniforms = {
    uTime: { value: 0 },
    uAmplitude: { value: 0 },
    uRadius: { value: 1 },
    uTurbulence: { value: 0.02 },
    uBreath: { value: 0 },
    uAmpGain: { value: 0 },
    uPointScale: { value: 0.011 },
    uSizeScale: { value: 600 },
    uPulse: { value: 0 },
    uPulseGain: { value: 0 },
    uNoiseTime: { value: 0 },
    uColorHot: { value: tokenColour(tok.hot) },
    uColorCool: { value: tokenColour(tok.cool) },
    uCoolMix: { value: 0.55 },
    // DIM, but not invisible. The reference's background spheres are barely
    // above the void, and that is what puts them behind the panels rather than
    // competing with the one sphere that carries state — 0.78 against the main
    // sphere's 1.10, with their real recessiveness coming from being a fifth
    // its size rather than from being turned down until they vanish.
    uBrightness: { value: 0.78 },
    uDepthFar: { value: 0.5 },
    uRimGain: { value: 0.24 },
    uRimSize: { value: 2.4 },
    uDarkSide: { value: 0.38 },
    uLambertPow: { value: 0.85 },
    uJitter: { value: 0 },
    uRimPow: { value: 0.2 },
    uSpreadPow: { value: 0.85 },
    // 1.0 = inert, matching FACE_SAT_DEFAULT in sphere-engine.ts. The face
    // desaturation was fitted to a JPEG chroma-subsampling artefact; see the
    // note there. These carry their own fixed identities and must not be
    // washed either.
    uFaceSat: { value: 1.0 },
    /**
     * ─── THIS LINE IS LOAD-BEARING. WITHOUT IT THE COMPANIONS LOSE THEIR CRESCENT ───
     *
     * `uEvenLight` belongs to the MAIN sphere, which is a UV grid and is lit
     * evenly. The companions are not, and must keep their directional shading.
     *
     * It would be tempting to leave it undeclared and trust WebGL's zero
     * initialisation. THAT IS WRONG HERE AND IT WAS MEASURED WRONG. three.js
     * caches one compiled WebGLProgram per unique shader source, and both spheres
     * use the same source — so they SHARE the program, and uniform values live on
     * the program, not on the material. A uniform a material does not declare is
     * simply not uploaded, so it keeps whatever the previous draw left there. The
     * main sphere draws first with uEvenLight = 1, and the companions inherited it.
     *
     * Caught by direct box photometry rather than by reading the code: covered
     * area in the companion boxes fell from 32.87% / 32.66% to 18.29% / 12.40%
     * and their peak brightness rose 19.45 -> 26.58 and 25.52 -> 42.83 — smaller,
     * hotter sprites, which is exactly what losing the rim's point growth and its
     * energy-spread divisor does. Declaring it explicitly pins it back to 0.
     */
    uEvenLight: { value: 0 },
    /**
     * ALPHA_MAX became a uniform so the main sphere could raise its ceiling.
     * 0.55 is the value the companions were fitted with and it is pinned here
     * for the same reason uEvenLight is: three.js shares one compiled program
     * between the two sphere kinds, and a uniform this material does not declare
     * keeps whatever the previous draw call left on it.
     */
    uAlphaMax: { value: 0.55 },
    /**
     * NO GLOW ON THE COMPANIONS. With gain 0 and tight 1 the compound falloff
     * reduces to sqrt(sharp^2) = sharp — bit-identical to what they had. Pinned
     * for the same reason uEvenLight and uAlphaMax are: three.js shares one
     * compiled program between the two sphere kinds, and an undeclared uniform
     * keeps whatever the previous draw left on it.
     */
    uGlowGain: { value: 0 },
    uCoreTight: { value: 1 },
    // The SAME light as the main sphere, flipped to the left with it. Three
    // objects in one scene lit from two directions would read as three
    // different rooms. See LIGHT_DIR in sphere-engine.ts for the count across
    // the sixteen reference frames that decided the side.
    uLightDir: { value: new Vector3(-1.0, -0.28, 0.3).normalize() },
  };

  const material = new ShaderMaterial({
    uniforms,
    vertexShader,
    fragmentShader,
    transparent: true,
    depthWrite: false,
    depthTest: false,
    blending: AdditiveBlending,
  });

  const geometry = lattice(countOverride ?? COMPANION_PARTICLES, latticeJitter);
  const points = new Points(geometry, material);
  // Behind the main sphere in the draw order. With depthTest off the order IS
  // the stacking, so this is the whole of "they sit behind".
  points.renderOrder = -1;
  scene.add(points);

  let t = 0;
  // Each drifts at its own rate so they never look like one object mirrored.
  const spin = side === 'left' ? 0.035 : -0.028;
  let fx = 0.5;
  let fy = 0.5;
  let scale = 0.2;

  return {
    side,
    place(nfx, nfy, nscale) {
      fx = nfx;
      fy = nfy;
      scale = nscale;
    },
    step(dtMs, sizeScale) {
      t += dtMs / 1000;
      uniforms.uTime.value = t;
      uniforms.uNoiseTime.value = t * 0.5;
      uniforms.uSizeScale.value = sizeScale;
      uniforms.uRadius.value = scale;
      /**
       * THE PARTICLE SIZE IS THE MAIN SPHERE'S, FLAT — not a fraction of it.
       *
       * The first version scaled point size with the sphere and the companions
       * rendered as 0.6 px, clamped up to 1 px at 46% brightness: present in
       * the buffer, invisible on screen. The fix was `0.30 + 0.50 * scale`,
       * which made them visible and still 0.48x the main sphere's dots.
       *
       * The reference does neither. Its companions' particles measure 3.5 and
       * 4.0 px against its main sphere's 4.8 — the SAME dots, on a smaller
       * ball. `gl_PointSize` is `uPointScale * uSizeScale / -viewPos.z` and
       * `uRadius` does not enter it, so matching the main sphere's uPointScale
       * matches its screen size exactly.
       *
       * 0.0083 is the main sphere's idle pointScale (0.00952) times the fit it
       * runs at on this display (0.868). The fit moves with the window, so this
       * tracks the main sphere to within about 13% rather than exactly; a
       * companion that re-read the engine's smoothed state every frame would be
       * exact and would also couple two objects that are deliberately
       * independent.
       */
      uniforms.uPointScale.value = 0.0083 * (sizeMul ?? 1);
      points.rotation.y = t * spin;

      // Placed in world units from a canvas fraction, using the caller's live
      // projection. The sphere's own placement does the same arithmetic — see
      // setCentreOffset — and doing it from the same getter is what keeps the
      // two from drifting apart on a resize.
      const wpp = worldPerPixelAt();
      const { w, h } = canvasSize();
      points.position.x = (fx - 0.5) * w * wpp;
      points.position.y = -(fy - 0.5) * h * wpp;
    },
    dispose() {
      scene.remove(points);
      geometry.dispose();
      material.dispose();
    },
  };
}
