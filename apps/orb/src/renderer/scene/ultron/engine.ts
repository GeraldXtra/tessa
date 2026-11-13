import type { AgentState } from '@tessa/protocol';

import { tokenValue } from '../../design-tokens.ts';
import type { Followers, JobRingInput, MarkReport, PlasmaEngine, PlasmaEngineOptions, PlasmaStats } from '../plasma-engine.ts';
import { shortRenderer } from '../plasma-gl.ts';
import {
  CAPTION_GAP_PX,
  CAPTION_H_PX,
  CAPTION_W_R,
  CHIP_ABOVE_PX,
  COMPACT_CAPTION_W_PX,
  COMPACT_CHIP_ABOVE_PX,
  COMPACT_QUICK_W_PX,
  COMPACT_RIBBON_H_PX,
  COMPACT_RIBBON_W_PX,
  LAYOUT_SNAP_PX,
  LAYOUT_TAU_S,
  QUICK_H_PX,
  QUICK_W_MAX_PX,
  RIBBON_GAP_PX,
  ribbonBox,
} from '../plasma-model.ts';
import { boxOf, placeCallouts, type Box, type PlanetOnScreen, type Slot } from './callouts.ts';
import { GpuClock, SOFTWARE_RENDERER, dropTarget, pickFormat, program, rendererString, target, type Fmt, type Target } from './gl.ts';
import { FrameLoop } from './loop.ts';
import { apply, ident, mul, rotX, rotY, rotZ, spinAbout, type M3 } from './mat3.ts';
import {
  BOOT_S,
  GALAXY_CAM,
  GALAXY_RINGS,
  ORBITS,
  ORBIT_TILT,
  ORB_CAM,
  ORB_R,
  PITCH,
  SCENE_CAM,
  TAU,
  UNIT_PER_R,
  WorkingDwell,
  advanceClocks,
  approach,
  blendPalette,
  clamp01,
  mixRgb,
  newClocks,
  orbitPeriod,
  parseColour,
  scenePalette,
  settledStill,
  smooth,
  stepMix,
  targetMix,
  voiceStep,
  voiceTarget,
  type Mix,
  type Rgb,
  type ScenePalette,
} from './model.ts';
import { NO_DATA, PLANETS, nameLine, orbitLine, planetStatus, type PlanetSources } from './planets.ts';
import {
  DUST_STRIDE,
  HUD_CHARS,
  HUD_STRIDE,
  LG,
  LINE_STRIDE,
  ORB_STRIDE,
  RINGS,
  RING_RATE,
  STREAM_COUNT,
  STREAM_STRIDE,
  STREAM_WORDS,
  buildDust,
  buildHudText,
  buildLines,
  buildOrb,
  buildStream,
  ringBasis,
} from './points.ts';
import { LOW, QualityGovernor, TIERS } from './quality.ts';
import brightFrag from './glsl/bright.frag.glsl?raw';
import compositeFrag from './glsl/composite.frag.glsl?raw';
import downFrag from './glsl/down.frag.glsl?raw';
import dustFrag from './glsl/dust.frag.glsl?raw';
import dustVert from './glsl/dust.vert.glsl?raw';
import galaxyFrag from './glsl/galaxy.frag.glsl?raw';
import glyphFrag from './glsl/glyph.frag.glsl?raw';
import hudVert from './glsl/hud.vert.glsl?raw';
import lineFrag from './glsl/line.frag.glsl?raw';
import lineVert from './glsl/line.vert.glsl?raw';
import orbFrag from './glsl/orb.frag.glsl?raw';
import orbVert from './glsl/orb.vert.glsl?raw';
import planetFrag from './glsl/planet.frag.glsl?raw';
import planetVert from './glsl/planet.vert.glsl?raw';
import quadVert from './glsl/quad.vert.glsl?raw';
import raysFrag from './glsl/rays.frag.glsl?raw';
import spaceFrag from './glsl/space.frag.glsl?raw';
import spriteFrag from './glsl/sprite.frag.glsl?raw';
import spriteVert from './glsl/sprite.vert.glsl?raw';
import streamFrag from './glsl/stream.frag.glsl?raw';
import streamVert from './glsl/stream.vert.glsl?raw';
import upFrag from './glsl/up.frag.glsl?raw';

export interface SceneStats {
  tier: number;
  tierName: string;
  pinned: boolean;
  onBattery: boolean;
  changes: number;
  points: number;
  frozen: boolean;
  galaxy: number;
  gpuTimer: boolean;
  format: string;
  overlays: 'shader' | 'css';
  callouts: string[];
  unit: number;
  shellPx: number;
  bootDone: boolean;
}

export interface SceneExtras {
  setStage(box: Box | null): void;
  setCallouts(els: readonly (HTMLElement | null)[]): void;
  setPlanetSources(src: PlanetSources): void;
  setOnBattery(on: boolean): void;
  pinQuality(tier: number | null): void;
  setOverlays(inShader: boolean): void;
  push(x: number, y: number, strength: number): void;
  sceneStats(): SceneStats;
}

export type UltronEngine = PlasmaEngine & SceneExtras;

export class SceneUnavailable extends Error {}

const RAF_WINDOW = 120;
const IV_WINDOW = 240;
const GPU_WINDOW = 90;
const COST_WINDOW = 120;
const CHANGE_WINDOW = 60;
const HIST_BINS = 128;
const MAX_SPRITES = 72;
const SPRITE_STRIDE = 12;
const PLANET_STRIDE = 24;
const MAX_DYN_LINES = 96;
const PUSH_LIFE_S = 4;
const PALETTE_TAU_S = 0.35;
const FREEZE_MAX_MS = 1500;

function pct(src: Float64Array, n: number, scratch: Float64Array, p: number): number {
  if (n <= 0) return 0;
  for (let i = 0; i < n; i++) scratch[i] = src[i] ?? 0;
  const view = scratch.subarray(0, n);
  view.sort();
  return view[Math.min(n - 1, Math.floor(n * p))] ?? 0;
}

function token(name: string, fallback: Rgb): Rgb {
  return parseColour(tokenValue(name)) ?? fallback;
}

function rgbCss(c: Rgb, a: number): string {
  return `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${a})`;
}

interface Push {
  x: number;
  y: number;
  s: number;
  t0: number;
}

interface Mark {
  live: boolean;
  label: string;
  t0: number;
  budget: number;
  first: number;
  settle: number;
  dropped: boolean;
}

export function createUltronEngine(options: PlasmaEngineOptions): UltronEngine {
  const { canvas, getState } = options;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ctx = canvas.getContext('webgl2', {
    alpha: false,
    antialias: false,
    depth: false,
    stencil: false,
    premultipliedAlpha: true,
    preserveDrawingBuffer: false,
    powerPreference: 'default',
    failIfMajorPerformanceCaveat: true,
  });
  if (!ctx) throw new SceneUnavailable('no hardware WebGL 2 context (unavailable, or only with a performance caveat)');
  const gl: WebGL2RenderingContext = ctx;
  const renderer = rendererString(gl);
  if (SOFTWARE_RENDERER.test(renderer)) throw new SceneUnavailable(`software renderer (${shortRenderer(renderer)})`);

  const fmt: Fmt = pickFormat(gl);
  const pmax = Math.max(8, Math.min(64, ((gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE) as Float32Array | null) ?? [1, 64])[1] ?? 64));
  let clock: GpuClock | null = GpuClock.create(gl);

  const P = {
    orb: program(gl, 'orb', orbVert, orbFrag, ['uRes', 'uCentre', 'uUnit', 'uDist', 'uOrb', 'uCore', 'uFlow', 'uTime', 'uBoot', 'uVoice', 'uCoreScale', 'uSurge', 'uBand', 'uRingRate', 'uArc', 'uRipple', 'uSize', 'uDpr', 'uPmax', 'uGain', 'uMid', 'uBright', 'uHot', 'uWhite', 'uPush', 'uPushR']),
    dust: program(gl, 'dust', dustVert, dustFrag, ['uRes', 'uCentre', 'uUnit', 'uDist', 'uGalUnit', 'uOrbPx', 'uView', 'uBelt', 'uDustSpin', 'uOrbit', 'uFlowT', 'uGal', 'uGalSpin', 'uBoot', 'uThink', 'uListen', 'uGalaxy', 'uBeltK', 'uEmber', 'uDim', 'uDpr', 'uPmax', 'uBright', 'uMid', 'uDimC', 'uHot', 'uStarA', 'uStarB']),
    line: program(gl, 'line', lineVert, lineFrag, ['uRes', 'uCentre', 'uUnit', 'uGM', 'uGS', 'uGX', 'uPal', 'uTrace', 'uPulse', 'uSpec', 'uOcc', 'uWidth']),
    hud: program(gl, 'hud', hudVert, glyphFrag, ['uRes', 'uCentre', 'uUnit', 'uRot', 'uTrace', 'uLift', 'uAtlas', 'uCol', 'uInt']),
    stream: program(gl, 'stream', streamVert, streamFrag, ['uRes', 'uCentre', 'uUnit', 'uWind', 'uSwap', 'uWords', 'uRows', 'uAtlas', 'uTintA', 'uTintB', 'uInt']),
    sprite: program(gl, 'sprite', spriteVert, spriteFrag, ['uRes']),
    planet: program(gl, 'planet', planetVert, planetFrag, ['uRes', 'uSun', 'uGain']),
    space: program(gl, 'space', quadVert, spaceFrag, ['uSize', 'uDpr', 'uBase', 'uCool', 'uWarm']),
    galaxy: program(gl, 'galaxy', quadVert, galaxyFrag, []),
    bright: program(gl, 'bright', quadVert, brightFrag, ['uSrc', 'uTexel', 'uThreshold', 'uScale']),
    down: program(gl, 'down', quadVert, downFrag, ['uSrc', 'uTexel']),
    up: program(gl, 'up', quadVert, upFrag, ['uSrc', 'uTexel', 'uGain']),
    rays: program(gl, 'rays', quadVert, raysFrag, ['uSrc', 'uCore', 'uAspect', 'uReach', 'uHole', 'uGain']),
    composite: program(gl, 'composite', quadVert, compositeFrag, ['uScene', 'uBloom', 'uSpace', 'uGalaxy', 'uCanvas', 'uDpr', 'uOrbUv', 'uOrbR', 'uAspect', 'uGalM', 'uGalK', 'uSceneK', 'uBloomK', 'uSpaceK', 'uGrade', 'uAberr', 'uFlicker', 'uOverlays', 'uFade']),
  };

  const orbCloud = buildOrb();
  const dust = buildDust();
  const lines = buildLines();
  const hudText = buildHudText();
  const stream = buildStream();

  const buf = (data: Float32Array | null, bytes: number, usage: number): WebGLBuffer => {
    const b = gl.createBuffer();
    if (!b) throw new Error('createBuffer failed');
    gl.bindBuffer(gl.ARRAY_BUFFER, b);
    if (data) gl.bufferData(gl.ARRAY_BUFFER, data, usage);
    else gl.bufferData(gl.ARRAY_BUFFER, bytes, usage);
    return b;
  };
  const vao = (): WebGLVertexArrayObject => {
    const v = gl.createVertexArray();
    if (!v) throw new Error('createVertexArray failed');
    gl.bindVertexArray(v);
    return v;
  };
  const attr = (loc: number, size: number, stride: number, offset: number, divisor: number): void => {
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, stride, offset);
    gl.vertexAttribDivisor(loc, divisor);
  };

  const quadBuf = buf(new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), 0, gl.STATIC_DRAW);
  const segBuf = buf(new Float32Array([0, -1, 1, -1, 0, 1, 1, 1]), 0, gl.STATIC_DRAW);

  const vaoFull = vao();
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
  attr(0, 2, 8, 0, 0);

  const orbBuf = buf(orbCloud.data, 0, gl.STATIC_DRAW);
  const vaoOrb = vao();
  gl.bindBuffer(gl.ARRAY_BUFFER, orbBuf);
  attr(0, 4, ORB_STRIDE * 4, 0, 0);
  attr(1, 4, ORB_STRIDE * 4, 16, 0);
  attr(2, 4, ORB_STRIDE * 4, 32, 0);

  const dustBuf = buf(dust.data, 0, gl.STATIC_DRAW);
  const vaoDust = vao();
  gl.bindBuffer(gl.ARRAY_BUFFER, dustBuf);
  attr(0, 4, DUST_STRIDE * 4, 0, 0);
  attr(1, 4, DUST_STRIDE * 4, 16, 0);

  const lineBuf = buf(lines.data, 0, gl.STATIC_DRAW);
  const vaoLine = vao();
  gl.bindBuffer(gl.ARRAY_BUFFER, segBuf);
  attr(0, 2, 8, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, lineBuf);
  attr(1, 4, LINE_STRIDE * 4, 0, 1);
  attr(2, 4, LINE_STRIDE * 4, 16, 1);
  attr(3, 4, LINE_STRIDE * 4, 32, 1);

  const dynLines = new Float32Array(MAX_DYN_LINES * LINE_STRIDE);
  const dynBuf = buf(null, dynLines.byteLength, gl.DYNAMIC_DRAW);
  const vaoDyn = vao();
  gl.bindBuffer(gl.ARRAY_BUFFER, segBuf);
  attr(0, 2, 8, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, dynBuf);
  attr(1, 4, LINE_STRIDE * 4, 0, 1);
  attr(2, 4, LINE_STRIDE * 4, 16, 1);
  attr(3, 4, LINE_STRIDE * 4, 32, 1);

  const hudBuf = buf(hudText.data, 0, gl.STATIC_DRAW);
  const vaoHud = vao();
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
  attr(0, 2, 8, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, hudBuf);
  attr(1, 4, HUD_STRIDE * 4, 0, 1);
  attr(2, 4, HUD_STRIDE * 4, 16, 1);

  const streamBuf = buf(stream.data, 0, gl.STATIC_DRAW);
  const vaoStream = vao();
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
  attr(0, 2, 8, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, streamBuf);
  attr(1, 4, STREAM_STRIDE * 4, 0, 1);
  attr(2, 4, STREAM_STRIDE * 4, 16, 1);

  const sprites = new Float32Array(MAX_SPRITES * SPRITE_STRIDE);
  const spriteBuf = buf(null, sprites.byteLength, gl.DYNAMIC_DRAW);
  const vaoSprite = vao();
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
  attr(0, 2, 8, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, spriteBuf);
  attr(1, 4, SPRITE_STRIDE * 4, 0, 1);
  attr(2, 4, SPRITE_STRIDE * 4, 16, 1);
  attr(3, 4, SPRITE_STRIDE * 4, 32, 1);

  const planetData = new Float32Array(PLANETS.length * PLANET_STRIDE);
  const planetBuf = buf(null, planetData.byteLength, gl.DYNAMIC_DRAW);
  const vaoPlanet = vao();
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
  attr(0, 2, 8, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, planetBuf);
  for (let i = 0; i < 6; i++) attr(1 + i, 4, PLANET_STRIDE * 4, i * 16, 1);
  gl.bindVertexArray(null);

  const atlasTexture = (w: number, h: number, draw: (c: CanvasRenderingContext2D) => void): WebGLTexture => {
    const cv = document.createElement('canvas');
    cv.width = w;
    cv.height = h;
    const c2 = cv.getContext('2d');
    if (c2) draw(c2);
    const tex = gl.createTexture();
    if (!tex) throw new Error('createTexture failed');
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, cv);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindTexture(gl.TEXTURE_2D, null);
    return tex;
  };
  const ink = tokenValue('--theme-void') || tokenValue('--bg-void');
  const mono = tokenValue('--font-mono') || 'monospace';
  const hudAtlas = atlasTexture(512, 128, (c) => {
    c.fillStyle = ink;
    c.font = `bold 36px ${mono}`;
    c.textAlign = 'center';
    c.textBaseline = 'middle';
    for (let i = 0; i < HUD_CHARS.length; i++) c.fillText(HUD_CHARS[i] ?? ' ', (i % 16) * 32 + 16, Math.floor(i / 16) * (128 / 3) + 128 / 6 + 1);
  });
  const streamRows = 16;
  const streamAtlas = atlasTexture(1024, 512, (c) => {
    c.fillStyle = ink;
    c.font = `bold 19px ${mono}`;
    c.textAlign = 'center';
    c.textBaseline = 'middle';
    STREAM_WORDS.forEach((w, i) => c.fillText(w, (i % 4) * 256 + 128, Math.floor(i / 4) * 32 + 16));
  });

  let galaxyTex: WebGLTexture | null = null;
  let spaceT: Target | null = null;
  let spaceDirty = true;
  let sceneT: Target | null = null;
  const bloom: Target[] = [];

  const buildGalaxy = (): void => {
    const n = Math.min(1024, gl.getParameter(gl.MAX_TEXTURE_SIZE) as number);
    const tex = gl.createTexture();
    const fb = gl.createFramebuffer();
    if (!tex || !fb) return;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, n, n, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.viewport(0, 0, n, n);
    gl.disable(gl.BLEND);
    gl.useProgram(P.galaxy.p);
    gl.bindVertexArray(vaoFull);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.deleteFramebuffer(fb);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindTexture(gl.TEXTURE_2D, null);
    galaxyTex = tex;
  };

  let themePal: ScenePalette = scenePalette({ core: [1, 0.79, 0.63], body: [1, 0.45, 0.11], idle: [0.78, 0.31, 0.05], deep: [0.29, 0.11, 0.02] });
  let blockedPal: ScenePalette = themePal;
  let pal: ScenePalette = themePal;
  let spaceBase: Rgb = [0.01, 0.012, 0.02];

  const readPalettes = (): void => {
    const id = document.documentElement.dataset['theme'] ?? 'ultron';
    const body = token(`--theme-${id}-body`, token('--accent', [1, 0.45, 0.11]));
    const core = token(`--theme-${id}-core`, body);
    const idle = token(`--theme-${id}-idle`, body);
    const deep = token(`--theme-${id}-deep`, idle);
    themePal = scenePalette({ core, body, idle, deep });
    const bm = token('--blocked-mid', [1, 0.64, 0.1]);
    const bh = token('--blocked-hot', [1, 0.89, 0.71]);
    const bd = token('--blocked-deep', [0.29, 0.14, 0]);
    blockedPal = scenePalette({ core: bh, body: bm, idle: mixRgb(bm, bd, 0.4), deep: bd });
    spaceBase = token('--bg-ambient', [0.05, 0.08, 0.14]);
    spaceDirty = true;
  };
  readPalettes();
  pal = themePal;

  let cssW = 1;
  let cssH = 1;
  let dpr = 1;
  let bufW = 1;
  let bufH = 1;

  const governor = new QualityGovernor(undefined, (tier, why) => {
    window.tessa?.reportMetrics?.(`SCENE quality ${TIERS[tier]?.name ?? tier} (tier ${tier}): ${why}`);
    loop.poke();
  });
  let compact = false;
  let overlaysInShader = true;

  const tierNow = (): number => (compact ? LOW : governor.tier);

  const ensureSized = (): void => {
    const cw = canvas.clientWidth || 1;
    const ch = canvas.clientHeight || 1;
    const d = Math.min(window.devicePixelRatio || 1, 1.5);
    const bw = Math.max(1, Math.round(cw * d));
    const bh = Math.max(1, Math.round(ch * d));
    if (bw !== bufW || bh !== bufH || cw !== cssW || ch !== cssH) {
      cssW = cw;
      cssH = ch;
      dpr = d;
      bufW = bw;
      bufH = bh;
      canvas.width = bw;
      canvas.height = bh;
      spaceDirty = true;
      ribbonDirty = true;
    }
    const tier = TIERS[tierNow()] ?? TIERS[0];
    const s = tier?.scale ?? 1;
    sceneT = target(gl, bufW * s, bufH * s, fmt, sceneT);
    let w = sceneT.w;
    let h = sceneT.h;
    for (let i = 0; i < 5; i++) {
      w = Math.max(1, Math.round(w / 2));
      h = Math.max(1, Math.round(h / 2));
      bloom[i] = target(gl, w, h, fmt, bloom[i] ?? null);
    }
  };

  let shown: AgentState = getState();
  const mix: Mix = targetMix(shown, false);
  const goal: Mix = { ...mix };
  const dwell = new WorkingDwell();
  const k = newClocks();
  let voice = 0;
  let voiceIn = 0;
  let voiceAt = -1e9;
  let voiceTrace: number[] | null = null;
  let connected = true;
  let dimA = 1;
  let night = false;
  let boot = options.hold === true && !reduced ? 0 : 1;
  let bootAt = boot >= 1 ? -1 : 0;
  let rippleAt = -1e9;
  let surgePrev = 0;
  let renderedPending = false;
  let flareAt = -1e9;
  let jobs: readonly JobRingInput[] = [];
  const pushes: Push[] = [];
  let pushR = 120;

  let placeSet = false;
  let pcx = 0;
  let pcy = 0;
  let pr = 100;
  let pcxT = 0;
  let pcyT = 0;
  let prT = 100;
  let offX = 0;
  let offY = 0;
  let fitT = 1;
  let fitC = 1;
  let cx = 0;
  let cy = 0;
  let R = 100;

  let followers: Followers = {};
  let chipX = -1e9;
  let chipY = -1e9;
  let capX = -1e9;
  let capY = -1e9;
  let capW = -1;
  let quickX = -1e9;
  let quickY = -1e9;
  let quickW = -1;

  const ribbon = options.ribbon ?? null;
  const rib2 = ribbon ? ribbon.getContext('2d') : null;
  let ribbonDirty = true;
  let ribW = 0;
  let ribH = 0;
  let ribX = -1e9;
  let ribY = -1e9;
  let ribA = 0;
  let ribClear = false;

  let stage: Box | null = null;
  let callEls: (HTMLElement | null)[] = [];
  let slots: (Slot | null)[] = [null, null];
  const slotShown: (number | null)[] = [null, null];
  const slotAlpha = [0, 0];
  const slotX = [-1e9, -1e9];
  const slotY = [-1e9, -1e9];
  let sources: PlanetSources = { calendar: null, linkUp: false };
  let sourcesStamp = 0;
  const slotStamp = [-1, -1];
  const onScreen: PlanetOnScreen[] = PLANETS.map(() => ({ x: 0, y: 0, r: 0, vis: 0, front: false }));
  const planetZ = new Float32Array(PLANETS.length);

  const marks: Mark[] = [];
  for (let i = 0; i < 8; i++) marks.push({ live: false, label: '', t0: 0, budget: 0, first: -1, settle: -1, dropped: false });
  const changeLog = new Float64Array(CHANGE_WINDOW);
  let changeN = 0;
  let changeHead = 0;
  let lastSettle: { label: string; ms: number; budget: number } | null = null;
  let pendingAt = -1;

  const rafRing = new Float64Array(RAF_WINDOW);
  let rafN = 0;
  let rafHead = 0;
  let refreshMs = 1000 / 60;
  const ivRing = new Float64Array(IV_WINDOW);
  let ivN = 0;
  let ivHead = 0;
  const gpuRing = new Float64Array(GPU_WINDOW);
  let gpuN = 0;
  let gpuHead = 0;
  let gpuLast: number | null = null;
  const costRing = new Float64Array(COST_WINDOW);
  let costN = 0;
  let costHead = 0;
  const scratch = new Float64Array(IV_WINDOW);
  const ivHist = new Uint32Array(HIST_BINS);
  let lastTs = 0;
  let framesDrawn = 0;
  let frameCap: number | null = null;
  let capSkip = 0;
  let animating = true;
  let freezeAt = 0;
  let frozen = false;
  let disposed = false;
  let lost = false;
  let pointsDrawn = 0;

  const loop = new FrameLoop(
    {
      request: (cb) => requestAnimationFrame(cb),
      cancel: (id) => cancelAnimationFrame(id),
      hidden: () => document.hidden || disposed || lost,
    },
    (ts) => frame(ts),
  );

  const push = (ring: Float64Array, head: number, v: number): number => {
    ring[head] = v;
    return (head + 1) % ring.length;
  };

  const wake = (): void => {
    frozen = false;
    loop.poke();
  };

  const placeFollowers = (): void => {
    const chip = followers.chip;
    if (chip) {
      const y = cy - R - (compact ? COMPACT_CHIP_ABOVE_PX : CHIP_ABOVE_PX);
      if (Math.abs(cx - chipX) > 0.25 || Math.abs(y - chipY) > 0.25) {
        chipX = cx;
        chipY = y;
        chip.style.transform = `translate(${cx.toFixed(1)}px,${y.toFixed(1)}px) translateX(-50%)`;
      }
    }
    const box = ribBox();
    const caption = followers.caption;
    if (caption) {
      const w = compact ? COMPACT_CAPTION_W_PX : Math.round(CAPTION_W_R * R);
      const x = cx - w / 2;
      const y = box.y + box.h + CAPTION_GAP_PX;
      if (w !== capW) {
        capW = w;
        caption.style.width = `${w}px`;
      }
      if (Math.abs(x - capX) > 0.25 || Math.abs(y - capY) > 0.25) {
        capX = x;
        capY = y;
        caption.style.transform = `translate(${x.toFixed(1)}px,${y.toFixed(1)}px)`;
      }
    }
    const quick = followers.quick;
    if (quick) {
      const w = compact ? COMPACT_QUICK_W_PX : Math.round(Math.min(CAPTION_W_R * R, QUICK_W_MAX_PX));
      const x = cx - w / 2;
      const y = box.y + (box.h + CAPTION_GAP_PX + CAPTION_H_PX - QUICK_H_PX) / 2;
      if (w !== quickW) {
        quickW = w;
        quick.style.width = `${w}px`;
      }
      if (Math.abs(x - quickX) > 0.25 || Math.abs(y - quickY) > 0.25) {
        quickX = x;
        quickY = y;
        quick.style.transform = `translate(${x.toFixed(1)}px,${y.toFixed(1)}px)`;
      }
    }
  };

  function ribBox(): { x: number; y: number; w: number; h: number } {
    if (!compact) return ribbonBox(cx, cy, R);
    return { x: cx - COMPACT_RIBBON_W_PX / 2, y: cy + R + RIBBON_GAP_PX, w: COMPACT_RIBBON_W_PX, h: COMPACT_RIBBON_H_PX };
  }

  const drawWave = (dt: number, t: number): void => {
    if (!rib2 || !ribbon) return;
    const want = shown === 'speaking' && connected && !reduced && voice > 0.01 ? 1 : 0;
    ribA = approach(ribA, want, dt, want ? 0.08 : 0.3);
    const box = ribBox();
    if (ribbonDirty || Math.abs(box.w - ribW) > 1 || Math.abs(box.h - ribH) > 1) {
      ribW = box.w;
      ribH = box.h;
      const d = Math.min(window.devicePixelRatio || 1, 2);
      ribbon.style.width = `${box.w.toFixed(1)}px`;
      ribbon.style.height = `${box.h.toFixed(1)}px`;
      ribbon.width = Math.max(1, Math.round(box.w * d));
      ribbon.height = Math.max(1, Math.round(box.h * d));
      ribbonDirty = false;
      ribClear = false;
    }
    if (Math.abs(box.x - ribX) > 0.25 || Math.abs(box.y - ribY) > 0.25) {
      ribX = box.x;
      ribY = box.y;
      ribbon.style.transform = `translate(${box.x.toFixed(1)}px,${box.y.toFixed(1)}px)`;
    }
    const bw = ribbon.width;
    const bh = ribbon.height;
    if (ribA < 0.01) {
      if (!ribClear) {
        rib2.clearRect(0, 0, bw, bh);
        ribClear = true;
      }
      return;
    }
    ribClear = false;
    rib2.clearRect(0, 0, bw, bh);
    const amp = voice * ribA;
    const c = pal.tintA;
    rib2.strokeStyle = rgbCss(c, Math.min(1, 0.85 * Math.min(1, 0.25 + amp * 1.6)));
    rib2.lineWidth = Math.max(1, bw / Math.max(1, ribW));
    rib2.beginPath();
    const n = 96;
    for (let i = 0; i <= n; i++) {
      const x = (i / n) * bw;
      const env = Math.sin((Math.PI * i) / n);
      const y = bh / 2 + Math.sin(i * 0.41 + t * 8.6) * Math.sin(i * 0.12 - t * 2.9) * bh * 0.44 * amp * env;
      if (i === 0) rib2.moveTo(x, y);
      else rib2.lineTo(x, y);
    }
    rib2.stroke();
  };

  const statusFor = (i: number): string => {
    const def = PLANETS[i];
    return def ? planetStatus(def.key, sources).text : NO_DATA;
  };

  const updateCallouts = (dt: number, now: number, shellPx: number, planetsOn: number): number => {
    let dyn = 0;
    const allow = stage !== null && !compact && planetsOn > 0.6 && boot >= 1;
    const next = allow && stage ? placeCallouts(slots, onScreen, stage, { cx, cy, r: shellPx }, now) : [null, null];
    slots = next;
    for (let s = 0; s < 2; s++) {
      const el = callEls[s] ?? null;
      const slot = next[s] ?? null;
      const want = slot ? 1 : 0;
      if (slot && slotShown[s] !== slot.planet && slotAlpha[s] < 0.05) {
        slotShown[s] = slot.planet;
        slotStamp[s] = -1;
      }
      const active = slot && slotShown[s] === slot.planet;
      slotAlpha[s] = approach(slotAlpha[s] ?? 0, active ? want : 0, dt, 0.12);
      if (!el) continue;
      const pi = slotShown[s];
      if (pi !== null && pi !== undefined && slotStamp[s] !== sourcesStamp) {
        slotStamp[s] = sourcesStamp;
        const def = PLANETS[pi];
        const kids = el.children;
        if (def && kids.length >= 3) {
          (kids[0] as HTMLElement).textContent = nameLine(def);
          (kids[1] as HTMLElement).textContent = orbitLine(def);
          const status = statusFor(pi);
          (kids[2] as HTMLElement).textContent = status;
          (kids[2] as HTMLElement).classList.toggle('nodata', status === NO_DATA);
        }
      }
      const on = slotAlpha[s] > 0.02;
      if (el.classList.contains('on') !== on) el.classList.toggle('on', on);
      if (pi === null || pi === undefined) continue;
      const p = onScreen[pi];
      if (!p) continue;
      const side = s === 0 ? -1 : 1;
      const b = boxOf(p, side);
      if (Math.abs(b.x0 - (slotX[s] ?? 0)) > 0.3 || Math.abs(b.y0 - (slotY[s] ?? 0)) > 0.3) {
        slotX[s] = b.x0;
        slotY[s] = b.y0;
        el.style.transform = `translate(${b.x0.toFixed(1)}px,${b.y0.toFixed(1)}px)`;
      }
      const a = (slotAlpha[s] ?? 0) * p.vis;
      if (a < 0.01) continue;
      const anchorX = side < 0 ? b.x1 + 4 : b.x0 - 4;
      const anchorY = b.y0 + 12;
      const ex = anchorX - side * 14;
      const dx = ex - p.x;
      const dy = anchorY - p.y;
      const dl = Math.hypot(dx, dy) || 1;
      const sx = p.x + (dx / dl) * (p.r + 8);
      const sy = p.y + (dy / dl) * (p.r + 8);
      dyn = addDyn(dyn, sx, sy, ex, anchorY, 4, 0.75 * a);
      dyn = addDyn(dyn, ex, anchorY, anchorX, anchorY, 4, 0.75 * a);
      dyn = addDyn(dyn, anchorX, anchorY - 5, anchorX, anchorY + 5, 4, 0.9 * a);
      const rr = p.r + 6;
      const arm = rr * 0.42;
      const rot = k.cam * 1.1 * a;
      for (const [ux, uy] of [[1, 1], [-1, 1], [-1, -1], [1, -1]] as const) {
        const cs = Math.cos(rot);
        const sn = Math.sin(rot);
        const P2 = (x: number, y: number): [number, number] => [p.x + x * cs - y * sn, p.y + x * sn + y * cs];
        const a0 = P2(ux * rr, uy * (rr - arm));
        const a1 = P2(ux * rr, uy * rr);
        const a2 = P2(ux * (rr - arm), uy * rr);
        dyn = addDyn(dyn, a0[0], a0[1], a1[0], a1[1], 4, a);
        dyn = addDyn(dyn, a1[0], a1[1], a2[0], a2[1], 4, a);
      }
    }
    return dyn;
  };

  const addDyn = (n: number, x0: number, y0: number, x1: number, y1: number, tone: number, alpha: number): number => {
    if (n >= MAX_DYN_LINES || alpha <= 0.002) return n;
    dynLines.set([x0, y0, 0, tone, x1, y1, 0, alpha, LG.screen, 0, 0, 0], n * LINE_STRIDE);
    return n + 1;
  };

  let spriteN = 0;
  const sprite = (x: number, y: number, rx: number, ry: number, ang: number, inten: number, pw: number, shape: number, c: Rgb): void => {
    if (spriteN >= MAX_SPRITES || inten <= 0.0005 || rx <= 0.1) return;
    sprites.set([x, y, rx, ry, ang, inten * fmt.gain, pw, shape, c[0], c[1], c[2], 0], spriteN * SPRITE_STRIDE);
    spriteN++;
  };

  const groupM = new Float32Array(12 * 9);
  const groupS = new Float32Array(12 * 4);
  const groupX = new Float32Array(12 * 4);
  const setGroup = (g: number, m: M3, scale: number, dist: number, gain: number, depth: number, unitMul: number, ta: number, tb: number, screen: number): void => {
    groupM.set(m, g * 9);
    groupS.set([scale, dist, gain, depth], g * 4);
    groupX.set([unitMul, ta, tb, screen], g * 4);
  };
  const palFlat = new Float32Array(15);
  const pushU = new Float32Array(16);
  const galM = new Float32Array(4);

  function renderSpace(): void {
    spaceT = target(gl, bufW, bufH, { internal: gl.RGBA8, format: gl.RGBA, type: gl.UNSIGNED_BYTE, name: 'RGBA8', gain: 1 }, spaceT);
    gl.bindFramebuffer(gl.FRAMEBUFFER, spaceT.fb);
    gl.viewport(0, 0, spaceT.w, spaceT.h);
    gl.disable(gl.BLEND);
    gl.useProgram(P.space.p);
    gl.uniform2f(P.space.u['uSize'] ?? null, cssW, cssH);
    gl.uniform1f(P.space.u['uDpr'] ?? null, dpr);
    const base = mixRgb([0.004, 0.0045, 0.008], spaceBase.map((v) => v * 0.16) as Rgb, 0.5);
    gl.uniform3f(P.space.u['uBase'] ?? null, base[0], base[1], base[2]);
    const cool = spaceBase.map((v) => v * 0.09) as Rgb;
    gl.uniform3f(P.space.u['uCool'] ?? null, cool[0], cool[1], cool[2]);
    const warm = themePal.dim.map((v) => v * 0.08) as Rgb;
    gl.uniform3f(P.space.u['uWarm'] ?? null, warm[0], warm[1], warm[2]);
    gl.bindVertexArray(vaoFull);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    spaceDirty = false;
  }

  function draw(t: number, dt: number, nowMs: number): void {
    if (!galaxyTex) buildGalaxy();
    ensureSized();
    if (spaceDirty) renderSpace();
    const sceneTarget = sceneT;
    if (!sceneTarget) return;
    const tier = TIERS[tierNow()] ?? (TIERS[0] as (typeof TIERS)[number]);
    const gaE = smooth(0, 1, mix.ga);
    const bo = boot;
    const blocked = mix.bl;
    const dim = dimA * (night ? 0.8 : 1) * (1 - 0.15 * blocked);
    const unitFull = R * UNIT_PER_R;
    const ign = 1 - Math.pow(1 - smooth(0, 0.75, bo), 3);
    const sunS = (1 - 0.55 * gaE) * (0.22 + 0.78 * ign);
    const U = Math.max(4, unitFull * sunS);
    const G = unitFull * 0.95 * (1 - 0.15 * gaE);
    const shellPx = U * ORB_R * (ORB_CAM / Math.sqrt(ORB_CAM * ORB_CAM - ORB_R * ORB_R));
    const tc = k.clock;
    const lvS = mix.sp * voice;
    const w3 = Math.pow(Math.max(0, Math.sin(tc * 0.37)), 6);
    const w4 = Math.pow(Math.max(0, Math.sin(tc * 0.71 + 2.1)), 9);
    const surge = 0.35 + 1.4 * w3 + 1.8 * w4 + 1.7 * lvS + 0.25 * mix.th + 0.2 * mix.li;
    const flare = clamp01((surge - 1) / 2.6);
    if (surge > 1.05 && surgePrev <= 1.05 && t - rippleAt > 0.9) rippleAt = t;
    surgePrev = surge;
    const pe = (t - rippleAt) / 1.25;
    const ripOn = pe >= 0 && pe < 1 && blocked < 0.5;
    const ripR = ripOn ? 0.1 + 0.98 * (1 - Math.pow(1 - pe, 3)) : -10;
    const ripK = ripOn ? Math.pow(1 - pe, 1.6) * 0.9 : 0;
    const planetsOn = smooth(0.45, 0.95, bo) * (1 - gaE) * (compact ? 0 : 1);
    const yaw = 0.15 * Math.sin(k.cam * 0.065);
    const pitch = PITCH + 0.035 * Math.sin(k.cam * 0.1 + 1.2);
    const V = mul(rotX(pitch), rotY(yaw));
    const OM = mul(V, mul(rotY(k.spin), rotX(0.32 + 0.12 * Math.sin(k.spin * 0.6))));
    const CM = mul(V, mul(rotZ(0.25), rotX(0.6)));
    const orbitM = ORBITS.map((_a, i) => mul(V, rotX(ORBIT_TILT[i] ?? 0)));
    const coreS = 1 + 0.55 * lvS + 0.12 * mix.th + 0.06 * Math.sin(tc * 2.2);

    for (let i = 0; i < PLANETS.length; i++) {
      const def = PLANETS[i];
      const o = onScreen[i];
      if (!def || !o) continue;
      const a = ORBITS[def.orbit];
      const phi = TAU * (def.phase - k.orbit / orbitPeriod(a));
      const v = apply(orbitM[def.orbit] as M3, a * Math.cos(phi), 0, a * Math.sin(phi));
      const per = SCENE_CAM / Math.max(SCENE_CAM - v[2], 0.1);
      o.x = cx + v[0] * U * per;
      o.y = cy - v[1] * U * per;
      o.r = Math.max(2.5, def.size * (U / 272) * per);
      o.front = v[2] >= 0;
      const dc = Math.hypot(o.x - cx, o.y - cy);
      const rc = U * 0.16;
      o.vis = (v[2] < 0 ? smooth(rc - o.r, rc + o.r, dc) : 1) * planetsOn;
      planetZ[i] = v[2];
      const len = Math.hypot(v[0], v[1], v[2]) || 1;
      const off = i * PLANET_STRIDE;
      planetData.set([o.x, o.y, o.r, def.look.ring ? 2.4 : 1.25, -v[0] / len, -v[1] / len, -v[2] / len, o.vis * (0.6 + 0.4 * dim), def.look.a[0], def.look.a[1], def.look.a[2], def.look.bands, def.look.b[0], def.look.b[1], def.look.b[2], def.look.noise, def.look.atm[0], def.look.atm[1], def.look.atm[2], def.look.atmK, k.cam * def.look.spin, i * 7.3, def.look.ring ? 1 : 0, v[2] < 0 ? 1 : 0], off);
    }
    const dynN = updateCallouts(dt, nowMs, shellPx, planetsOn);

    clock?.begin();
    gl.bindFramebuffer(gl.FRAMEBUFFER, sceneTarget.fb);
    gl.viewport(0, 0, sceneTarget.w, sceneTarget.h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendEquation(gl.FUNC_ADD);
    gl.blendColor(0, 0, 0, fmt.gain);
    gl.blendFunc(gl.CONSTANT_ALPHA, gl.ONE);
    const rtDpr = sceneTarget.w / Math.max(1, cssW);
    const grade = (c: Rgb): Rgb => [c[0] * pal.grade[0], c[1] * pal.grade[1], c[2] * pal.grade[2]];
    const white: Rgb = mixRgb(pal.hot, [1, 1, 1], 0.6);
    const orbOn = smooth(0, 0.3, bo);
    const ck = orbOn * dim * (1 + 0.5 * lvS + 0.3 * mix.th + 0.3 * flare) * (1 - 0.25 * blocked);

    const ou = P.orb.u;
    gl.useProgram(P.orb.p);
    gl.uniform2f(ou['uRes'] ?? null, cssW, cssH);
    gl.uniform2f(ou['uCentre'] ?? null, cx, cy);
    gl.uniform1f(ou['uUnit'] ?? null, U);
    gl.uniform1f(ou['uDist'] ?? null, ORB_CAM);
    gl.uniformMatrix3fv(ou['uOrb'] ?? null, false, OM);
    gl.uniformMatrix3fv(ou['uCore'] ?? null, false, CM);
    gl.uniform1f(ou['uFlow'] ?? null, k.flow);
    gl.uniform1f(ou['uTime'] ?? null, tc);
    gl.uniform1f(ou['uBoot'] ?? null, bo);
    gl.uniform1f(ou['uVoice'] ?? null, lvS);
    gl.uniform1f(ou['uCoreScale'] ?? null, coreS);
    gl.uniform1f(ou['uSurge'] ?? null, clamp01((surge - 0.35) / 3));
    gl.uniform2f(ou['uBand'] ?? null, Math.sin(k.scan * 0.9) * ORB_R, mix.li);
    gl.uniform3f(ou['uRingRate'] ?? null, RING_RATE[0] ?? 0.1, RING_RATE[1] ?? -0.08, RING_RATE[2] ?? 0.12);
    const actA = clamp01(Math.max(mix.th, 0.6 * mix.li, mix.sp * (0.4 + 0.6 * voice)));
    gl.uniform2f(ou['uArc'] ?? null, (0.4 + 0.8 * actA + 0.3 * flare) * (1 - 0.6 * gaE), 0.25 + 0.7 * actA);
    gl.uniform2f(ou['uRipple'] ?? null, ripR, ripK);
    gl.uniform1f(ou['uSize'] ?? null, Math.min(1.5, Math.max(0.55, U / 272)));
    gl.uniform1f(ou['uDpr'] ?? null, rtDpr);
    gl.uniform1f(ou['uPmax'] ?? null, pmax);
    const share = tier.points;
    gl.uniform1f(ou['uGain'] ?? null, 1.3 * dim * (1 + 0.25 * mix.th + 0.2 * flare) * (1 - 0.2 * blocked) / Math.pow(share, 0.6));
    const cMid = grade(pal.mid);
    const cBright = grade(pal.bright);
    const cHot = grade(pal.hot);
    const cWhite = grade(white);
    gl.uniform3f(ou['uMid'] ?? null, cMid[0], cMid[1], cMid[2]);
    gl.uniform3f(ou['uBright'] ?? null, cBright[0], cBright[1], cBright[2]);
    gl.uniform3f(ou['uHot'] ?? null, cHot[0], cHot[1], cHot[2]);
    gl.uniform3f(ou['uWhite'] ?? null, cWhite[0], cWhite[1], cWhite[2]);
    pushU.fill(0);
    for (let i = 0; i < pushes.length && i < 4; i++) {
      const p = pushes[i] as Push;
      pushU.set([p.x, p.y, p.s, (nowMs - p.t0) / 1000], i * 4);
    }
    gl.uniform4fv(ou['uPush'] ?? null, pushU);
    gl.uniform1f(ou['uPushR'] ?? null, pushR);
    gl.bindVertexArray(vaoOrb);
    pointsDrawn = orbCloud.fixed + Math.floor((orbCloud.total - orbCloud.fixed) * share);
    gl.drawArrays(gl.POINTS, 0, pointsDrawn);

    if (!compact) {
      const du = P.dust.u;
      gl.useProgram(P.dust.p);
      gl.uniform2f(du['uRes'] ?? null, cssW, cssH);
      gl.uniform2f(du['uCentre'] ?? null, cx, cy);
      gl.uniform1f(du['uUnit'] ?? null, U);
      gl.uniform1f(du['uDist'] ?? null, SCENE_CAM);
      gl.uniform1f(du['uGalUnit'] ?? null, G);
      gl.uniform1f(du['uOrbPx'] ?? null, shellPx);
      gl.uniformMatrix3fv(du['uView'] ?? null, false, V);
      gl.uniformMatrix3fv(du['uBelt'] ?? null, false, V);
      gl.uniform1f(du['uDustSpin'] ?? null, k.dust);
      gl.uniform1f(du['uOrbit'] ?? null, k.orbit);
      gl.uniform1f(du['uFlowT'] ?? null, k.clock);
      gl.uniform1f(du['uGal'] ?? null, k.gal);
      gl.uniform1f(du['uGalSpin'] ?? null, k.gspin);
      gl.uniform1f(du['uBoot'] ?? null, bo);
      gl.uniform1f(du['uThink'] ?? null, mix.th);
      gl.uniform1f(du['uListen'] ?? null, mix.li);
      gl.uniform1f(du['uGalaxy'] ?? null, gaE);
      gl.uniform1f(du['uBeltK'] ?? null, 0.9 * planetsOn);
      gl.uniform1f(du['uEmber'] ?? null, (0.55 + 0.7 * mix.th + 0.6 * flare + 0.5 * lvS) * smooth(0.5, 1, bo) * dim * (1 - 0.6 * gaE));
      gl.uniform1f(du['uDim'] ?? null, dim);
      gl.uniform1f(du['uDpr'] ?? null, rtDpr);
      gl.uniform1f(du['uPmax'] ?? null, pmax);
      gl.uniform3f(du['uBright'] ?? null, pal.bright[0], pal.bright[1], pal.bright[2]);
      gl.uniform3f(du['uMid'] ?? null, pal.mid[0], pal.mid[1], pal.mid[2]);
      gl.uniform3f(du['uDimC'] ?? null, pal.dim[0], pal.dim[1], pal.dim[2]);
      gl.uniform3f(du['uHot'] ?? null, pal.hot[0], pal.hot[1], pal.hot[2]);
      gl.uniform3f(du['uStarA'] ?? null, 1, 0.97, 0.92);
      gl.uniform3f(du['uStarB'] ?? null, 0.8, 0.82, 0.9);
      gl.bindVertexArray(vaoDust);
      gl.drawArrays(gl.POINTS, 0, dust.always);
      if (mix.th + 0.35 * mix.li > 0.01) gl.drawArrays(gl.POINTS, dust.inflowStart, dust.inflowCount);
      if (gaE > 0.004) gl.drawArrays(gl.POINTS, dust.galaxyStart, dust.galaxyCount);
    }

    const hudG = smooth(0.3, 0.62, bo) * dim * (1 - gaE) * (0.85 + 0.5 * mix.li + 0.3 * lvS);
    const ringG = compact ? 0 : 0.3 * planetsOn * dim * (1 + 0.4 * mix.th);
    for (let i = 0; i < 4; i++) {
      const angs: number[] = [];
      PLANETS.forEach((d) => {
        if (d.orbit === i && angs.length < 2) angs.push(TAU * (d.phase - k.orbit / orbitPeriod(ORBITS[i] ?? 1)));
      });
      setGroup(LG.orbit0 + i, orbitM[i] as M3, ORBITS[i] ?? 1, SCENE_CAM, ringG, 0.55, 1, angs[0] ?? -100, angs[1] ?? -100, 0);
    }
    const galRX = rotX(0.45);
    for (let i = 0; i < 3; i++) setGroup(LG.galaxy0 + i, galRX, (GALAXY_RINGS[i] ?? 1) * (0.62 + 0.38 * gaE), GALAXY_CAM, 0.4 * gaE * dim, 0.5, G / U, -100, -100, 0);
    const I3 = ident();
    setGroup(LG.bezel, I3, 1, SCENE_CAM, hudG, 0, 1, -100, -100, 0);
    setGroup(LG.card, rotZ(-k.outer), 1, SCENE_CAM, hudG, 0, 1, -100, -100, 0);
    setGroup(LG.arcA, rotZ(k.hudA), 1, SCENE_CAM, hudG, 0, 1, -100, -100, 0);
    setGroup(LG.arcB, rotZ(-k.hudB), 1, SCENE_CAM, hudG, 0, 1, -100, -100, 0);
    setGroup(LG.screen, I3, 1, SCENE_CAM, dim, 0, 1, -100, -100, 1);
    const lu = P.line.u;
    gl.useProgram(P.line.p);
    gl.uniform2f(lu['uRes'] ?? null, cssW, cssH);
    gl.uniform2f(lu['uCentre'] ?? null, cx, cy);
    gl.uniform1f(lu['uUnit'] ?? null, U);
    gl.uniformMatrix3fv(lu['uGM'] ?? null, false, groupM);
    gl.uniform4fv(lu['uGS'] ?? null, groupS);
    gl.uniform4fv(lu['uGX'] ?? null, groupX);
    palFlat.set([...pal.faint, ...pal.dim, ...pal.mid, ...pal.bright, ...pal.hot]);
    gl.uniform3fv(lu['uPal'] ?? null, palFlat);
    gl.uniform1f(lu['uTrace'] ?? null, smooth(0.3, 0.9, bo));
    gl.uniform2f(lu['uPulse'] ?? null, k.pulse * 0.07, 0.8 + 0.8 * mix.li);
    gl.uniform2f(lu['uSpec'] ?? null, 0.05 + lvS, k.spec);
    gl.uniform3f(lu['uOcc'] ?? null, cx, cy, U * 0.16);
    gl.uniform1f(lu['uWidth'] ?? null, 1);
    gl.bindVertexArray(vaoLine);
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, gaE > 0.004 ? lines.count : lines.galaxyFrom);
    if (dynN > 0) {
      gl.bindBuffer(gl.ARRAY_BUFFER, dynBuf);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, dynLines.subarray(0, dynN * LINE_STRIDE));
      gl.bindVertexArray(vaoDyn);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, dynN);
    }

    if (!compact && tier.glyphs > 0) {
      const su = P.stream.u;
      gl.useProgram(P.stream.p);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, streamAtlas);
      gl.uniform1i(su['uAtlas'] ?? null, 0);
      gl.uniform2f(su['uRes'] ?? null, cssW, cssH);
      gl.uniform2f(su['uCentre'] ?? null, cx, cy);
      gl.uniform1f(su['uUnit'] ?? null, U);
      gl.uniform1f(su['uWind'] ?? null, k.wind);
      gl.uniform1f(su['uSwap'] ?? null, k.glyph);
      gl.uniform1f(su['uWords'] ?? null, STREAM_WORDS.length);
      gl.uniform1f(su['uRows'] ?? null, streamRows);
      gl.uniform3f(su['uTintA'] ?? null, pal.tintA[0], pal.tintA[1], pal.tintA[2]);
      gl.uniform3f(su['uTintB'] ?? null, pal.tintB[0], pal.tintB[1], pal.tintB[2]);
      gl.uniform1f(su['uInt'] ?? null, 0.55 * smooth(0.6, 1, bo) * dim * (1 + 0.6 * mix.th + 0.4 * lvS) * (1 - 0.7 * gaE));
      gl.bindVertexArray(vaoStream);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, Math.round(STREAM_COUNT * tier.glyphs));
    }
    if (hudG > 0.001) {
      const hu = P.hud.u;
      gl.useProgram(P.hud.p);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, hudAtlas);
      gl.uniform1i(hu['uAtlas'] ?? null, 0);
      gl.uniform2f(hu['uRes'] ?? null, cssW, cssH);
      gl.uniform2f(hu['uCentre'] ?? null, cx, cy);
      gl.uniform1f(hu['uUnit'] ?? null, U);
      gl.uniform1f(hu['uRot'] ?? null, -k.outer);
      gl.uniform1f(hu['uTrace'] ?? null, smooth(0.3, 0.9, bo));
      gl.uniform1f(hu['uLift'] ?? null, Math.max(1, 14 / (0.055 * U)));
      gl.uniform3f(hu['uCol'] ?? null, pal.bright[0], pal.bright[1], pal.bright[2]);
      gl.uniform1f(hu['uInt'] ?? null, 0.62 * hudG);
      gl.bindVertexArray(vaoHud);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, hudText.count);
    }

    spriteN = 0;
    sprite(cx, cy, U * 1.1, U * 1.1, 0, 0.16 * ck, 2.2, 0, pal.mid);
    sprite(cx, cy, U * ORB_R * 1.01, U * ORB_R * 1.01, 0, 0.045 * ck, 0, 2, pal.mid);
    sprite(cx, cy, U * 0.42 * coreS, U * 0.42 * coreS, 0, 0.5 * ck, 1.6, 0, grade(pal.hot));
    sprite(cx, cy, U * 0.09 * coreS, U * 0.09 * coreS, 0, 0.8 * ck, 1.3, 0, mixRgb(pal.hot, [1, 1, 1], 0.45));
    if (ripOn) sprite(cx, cy, (U * ripR) / 0.82, (U * ripR) / 0.82, 0, Math.pow(1 - pe, 2) * 0.22 * ck, 1, 1, grade(pal.hot));
    const hs = Math.min(1.5, Math.max(0.6, U / 272));
    const gOn = smooth(0.6, 1, bo) * (1 - gaE);
    const headCol = grade(pal.hot);
    const glint = mixRgb(headCol, [1, 1, 1], 0.4);
    RINGS.forEach((g, ri) => {
      const [u, v] = ringBasis(g.n);
      const ax = g.axis;
      const al = Math.hypot(ax[0], ax[1], ax[2]) || 1;
      const rate = RING_RATE[ri] ?? 0.1;
      const h1 = k.flow * rate * TAU + ri * 2.1;
      for (const h of [h1, h1 + 3.3]) {
        const c = Math.cos(h) * g.r;
        const s = Math.sin(h) * g.r;
        const p0 = spinAbout(u[0] * c + v[0] * s, u[1] * c + v[1] * s, u[2] * c + v[2] * s, ax[0] / al, ax[1] / al, ax[2] / al, g.w * k.flow);
        const q = apply(OM, p0[0], p0[1], p0[2]);
        const per = ORB_CAM / Math.max(ORB_CAM - q[2], 0.1);
        const x = cx + q[0] * U * per;
        const y = cy - q[1] * U * per;
        const occ = q[2] < 0 && Math.hypot(q[0], q[1]) * per < 0.86 ? 0.3 : 1;
        const kk = ck * occ * gOn * (0.8 + 0.2 * Math.sin(tc * 7 + ri * 2 + h));
        sprite(x, y, 9 * hs * per, 9 * hs * per, 0, 0.3 * kk, 2.2, 0, headCol);
        sprite(x, y, 30 * hs * per, 1 * hs, 0.35, 0.5 * kk, 1.6, 0, glint);
        sprite(x, y, 17 * hs * per, 0.9 * hs, 0.35 + Math.PI / 2, 0.36 * kk, 1.6, 0, glint);
      }
    });
    sprite(cx, cy, U * ORB_R * (ORB_CAM / Math.sqrt(ORB_CAM * ORB_CAM - ORB_R * ORB_R)) / 0.82, U * ORB_R * (ORB_CAM / Math.sqrt(ORB_CAM * ORB_CAM - ORB_R * ORB_R)) / 0.82, 0, 0.06 * ck, 1, 1, grade(pal.bright));
    sprite(cx, cy, U * 1.9, U * 1.9, 0, 0.07 * dim * smooth(0.2, 0.9, bo) * (1 + 0.4 * flare + 0.3 * lvS) * (1 - 0.4 * blocked), 2.6, 0, pal.mid);
    const fl2 = Math.min(1, Math.max(0, surge - 0.6) / 3) * 0.75 + 0.25;
    const flK = fl2 * dim * smooth(0.5, 1, bo) * (1 - gaE);
    sprite(cx, cy, U * (1.15 + 0.75 * fl2), 1.2 + 1 * fl2, 0, 0.55 * flK, 1.6, 0, mixRgb(pal.hot, [1, 1, 1], 0.6));
    sprite(cx, cy, U * (0.8 + 0.5 * fl2), 6 + 6 * fl2, 0, 0.07 * flK, 1.6, 0, pal.bright);
    const shockE = (t - flareAt) / 1.4;
    if (shockE >= 0 && shockE < 1) sprite(cx, cy, (U * (0.62 + 2.38 * (1 - Math.pow(1 - shockE, 3)))) / 0.82, (U * (0.62 + 2.38 * (1 - Math.pow(1 - shockE, 3)))) / 0.82, 0, Math.pow(1 - shockE, 2) * 0.75 * dim, 1, 1, pal.bright);
    if (gaE > 0.01) {
      jobs.forEach((j, i) => {
        if (j.finished !== 'none') return;
        const ring = GALAXY_RINGS[i % 3] ?? 1.15;
        const ang = k.gal * (0.16 + 0.05 * (i % 3)) + i * 2.4;
        const rr = ring * (0.62 + 0.38 * gaE);
        const z0 = Math.sin(ang) * rr;
        const y2 = -z0 * Math.sin(0.45);
        const z2 = z0 * Math.cos(0.45);
        const per = GALAXY_CAM / (GALAXY_CAM - z2);
        const x = cx + Math.cos(ang) * rr * G * per;
        const y = cy - y2 * G * per;
        const amber = j.waiting;
        const col = amber ? token('--blocked-mid', [1, 0.64, 0.1]).map((c) => c * c) as Rgb : pal.hot;
        sprite(x, y, 16 * per, 16 * per, 0, 0.5 * gaE, 2.2, 0, col);
        sprite(x, y, 3.5 * per, 3.5 * per, 0, 2.2 * gaE, 1.2, 0, mixRgb(col, [1, 1, 1], 0.4));
      });
    }
    const comet = !compact && gaE < 0.5 ? (k.cam % 40) / 12 : 2;
    if (comet < 1) {
      const pass = Math.floor(k.cam / 40) % 2 === 0;
      const ax = pass ? -0.05 * cssW : 0.36 * cssW;
      const ay = pass ? 0.32 * cssH : 0.97 * cssH;
      const bx = pass ? 0.36 * cssW : -0.05 * cssW;
      const by = pass ? 0.97 * cssH : 0.32 * cssH;
      const hx = ax + (bx - ax) * comet - 0.05 * cssW * 4 * comet * (1 - comet);
      const hy = ay + (by - ay) * comet;
      const vis = Math.sin(Math.PI * comet) * dim * smooth(0.8, 1, bo);
      const dx = hx - cx;
      const dy = hy - cy;
      const dd = Math.hypot(dx, dy) || 1;
      const ang = Math.atan2(dy, dx);
      const tail = Math.min(190, Math.max(60, 55 + 140 * ((U * 2.2) / dd)));
      const ion = mixRgb(pal.hot, [0.75, 0.85, 1], 0.55);
      for (let j = 0; j < 3; j++) {
        const f = 0.35 + j * 0.32;
        sprite(hx + (dx / dd) * tail * f, hy + (dy / dd) * tail * f, tail * (0.42 + j * 0.12), 1.6 + j * 0.9, ang, (0.55 - j * 0.15) * vis, 1.6, 0, ion);
      }
      sprite(hx, hy, 15, 15, 0, 0.42 * vis, 2.2, 0, pal.hot);
      sprite(hx, hy, 3.2, 3.2, 0, 1.8 * vis, 1.3, 0, [1, 1, 1]);
    }
    const ping = (k.cam * 0.7) % 1;
    for (let s = 0; s < 2; s++) {
      const pi = slotShown[s];
      if (pi === null || pi === undefined) continue;
      const p = onScreen[pi];
      const a = (slotAlpha[s] ?? 0) * (p?.vis ?? 0);
      if (!p || a < 0.01) continue;
      sprite(p.x, p.y, (p.r + 6 + 14 * ping) / 0.82, (p.r + 6 + 14 * ping) / 0.82, 0, (1 - ping) * 0.5 * a, 1, 1, pal.bright);
    }
    if (spriteN > 0) {
      gl.useProgram(P.sprite.p);
      gl.uniform2f(P.sprite.u['uRes'] ?? null, cssW, cssH);
      gl.bindBuffer(gl.ARRAY_BUFFER, spriteBuf);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, sprites.subarray(0, spriteN * SPRITE_STRIDE));
      gl.bindVertexArray(vaoSprite);
      gl.blendFunc(gl.ONE, gl.ONE);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, spriteN);
    }

    if (planetsOn > 0.002) {
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.useProgram(P.planet.p);
      gl.uniform2f(P.planet.u['uRes'] ?? null, cssW, cssH);
      gl.uniform3f(P.planet.u['uSun'] ?? null, cx * rtDpr, sceneTarget.h - cy * rtDpr, U * 0.16 * rtDpr);
      gl.uniform1f(P.planet.u['uGain'] ?? null, fmt.gain);
      gl.bindBuffer(gl.ARRAY_BUFFER, planetBuf);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, planetData);
      gl.bindVertexArray(vaoPlanet);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, PLANETS.length);
    }

    gl.disable(gl.BLEND);
    gl.bindVertexArray(vaoFull);
    const levels = Math.max(2, Math.min(5, compact ? 3 : tier.bloom));
    const L0 = bloom[0] as Target;
    gl.bindFramebuffer(gl.FRAMEBUFFER, L0.fb);
    gl.viewport(0, 0, L0.w, L0.h);
    gl.useProgram(P.bright.p);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, sceneTarget.tex);
    gl.uniform1i(P.bright.u['uSrc'] ?? null, 0);
    gl.uniform2f(P.bright.u['uTexel'] ?? null, 1 / sceneTarget.w, 1 / sceneTarget.h);
    gl.uniform1f(P.bright.u['uThreshold'] ?? null, 0.3 * fmt.gain);
    gl.uniform1f(P.bright.u['uScale'] ?? null, 1);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.useProgram(P.down.p);
    for (let i = 1; i < levels; i++) {
      const src = bloom[i - 1] as Target;
      const dst = bloom[i] as Target;
      gl.bindFramebuffer(gl.FRAMEBUFFER, dst.fb);
      gl.viewport(0, 0, dst.w, dst.h);
      gl.bindTexture(gl.TEXTURE_2D, src.tex);
      gl.uniform1i(P.down.u['uSrc'] ?? null, 0);
      gl.uniform2f(P.down.u['uTexel'] ?? null, 1 / src.w, 1 / src.h);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE);
    const rayG = 0.5 * (0.7 + 0.6 * flare + 0.3 * lvS) * dim * orbOn * (1 - 0.5 * gaE);
    if (tier.rays && !compact && rayG > 0.002) {
      const L1 = bloom[1] as Target;
      gl.bindFramebuffer(gl.FRAMEBUFFER, L1.fb);
      gl.viewport(0, 0, L1.w, L1.h);
      gl.useProgram(P.rays.p);
      gl.bindTexture(gl.TEXTURE_2D, L0.tex);
      gl.uniform1i(P.rays.u['uSrc'] ?? null, 0);
      gl.uniform2f(P.rays.u['uCore'] ?? null, cx / cssW, 1 - cy / cssH);
      gl.uniform1f(P.rays.u['uAspect'] ?? null, cssW / cssH);
      gl.uniform1f(P.rays.u['uReach'] ?? null, 0.9);
      gl.uniform1f(P.rays.u['uHole'] ?? null, (U * 0.35) / cssH);
      gl.uniform1f(P.rays.u['uGain'] ?? null, rayG);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
    gl.useProgram(P.up.p);
    for (let i = levels - 1; i >= 1; i--) {
      const src = bloom[i] as Target;
      const dst = bloom[i - 1] as Target;
      gl.bindFramebuffer(gl.FRAMEBUFFER, dst.fb);
      gl.viewport(0, 0, dst.w, dst.h);
      gl.bindTexture(gl.TEXTURE_2D, src.tex);
      gl.uniform1i(P.up.u['uSrc'] ?? null, 0);
      gl.uniform2f(P.up.u['uTexel'] ?? null, 0.5 / src.w, 0.5 / src.h);
      gl.uniform1f(P.up.u['uGain'] ?? null, 0.8);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
    gl.disable(gl.BLEND);

    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, bufW, bufH);
    const cu = P.composite.u;
    gl.useProgram(P.composite.p);
    const texs: (WebGLTexture | null)[] = [sceneTarget.tex, L0.tex, spaceT?.tex ?? null, galaxyTex];
    texs.forEach((tx, i) => {
      gl.activeTexture(gl.TEXTURE0 + i);
      gl.bindTexture(gl.TEXTURE_2D, tx);
    });
    gl.uniform1i(cu['uScene'] ?? null, 0);
    gl.uniform1i(cu['uBloom'] ?? null, 1);
    gl.uniform1i(cu['uSpace'] ?? null, 2);
    gl.uniform1i(cu['uGalaxy'] ?? null, 3);
    gl.uniform2f(cu['uCanvas'] ?? null, bufW, bufH);
    gl.uniform1f(cu['uDpr'] ?? null, dpr);
    gl.uniform2f(cu['uOrbUv'] ?? null, cx / cssW, 1 - cy / cssH);
    gl.uniform1f(cu['uOrbR'] ?? null, U / cssH);
    gl.uniform1f(cu['uAspect'] ?? null, cssW / cssH);
    const gp = Math.min(1.45, Math.max(0.18, pitch + 0.27));
    const sn = Math.sin(gp);
    const gsc = 0.44 / (1 - 0.2 * gaE);
    const gc = Math.cos(k.gspin + yaw);
    const gs = Math.sin(k.gspin + yaw);
    galM.set([gsc * gc, gsc * gs, (-gsc * gs) / sn, (gsc * gc) / sn]);
    gl.uniformMatrix2fv(cu['uGalM'] ?? null, false, galM);
    gl.uniform1f(cu['uGalK'] ?? null, 0.8 * smooth(0.1, 0.8, bo) * (compact ? 0.6 : 1) * (1 - 0.35 * blocked));
    gl.uniform1f(cu['uSceneK'] ?? null, 1 / fmt.gain);
    gl.uniform1f(cu['uBloomK'] ?? null, 0.95 * (1 + 0.1 * Math.sin(tc * 0.8)) / fmt.gain);
    gl.uniform1f(cu['uSpaceK'] ?? null, 1.0 * (1 - 0.45 * blocked) * (connected ? 1 : 0.8));
    const gr = mixRgb(pal.grade, [1, 1, 1], 0.72);
    gl.uniform3f(cu['uGrade'] ?? null, gr[0], gr[1], gr[2]);
    gl.uniform1f(cu['uAberr'] ?? null, tier.aberration && !compact ? 0.003 : 0);
    gl.uniform1f(cu['uFlicker'] ?? null, reduced ? 1 : 1 + 0.015 * Math.sin(tc * 31) * Math.sin(tc * 7.7));
    gl.uniform1f(cu['uOverlays'] ?? null, overlaysInShader ? 1 : 0);
    gl.uniform1f(cu['uFade'] ?? null, 1);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    for (let i = 3; i >= 0; i--) {
      gl.activeTexture(gl.TEXTURE0 + i);
      gl.bindTexture(gl.TEXTURE_2D, null);
    }
    gl.bindVertexArray(null);
    clock?.end();
    clock?.collect((ms) => {
      gpuLast = ms;
      gpuHead = push(gpuRing, gpuHead, ms);
      gpuN = Math.min(GPU_WINDOW, gpuN + 1);
    });
    framesDrawn += 1;
  }

  function step(dt: number, nowMs: number): void {
    const st = getState();
    if (st !== shown) {
      shown = st;
      renderedPending = true;
      const t0 = pendingAt >= 0 ? pendingAt : nowMs;
      pendingAt = -1;
      for (const m of marks) if (m.live && m.budget > 0 && m.settle < 0 && m.t0 < t0 - 0.5) m.dropped = true;
    }
    const galaxy = dwell.update(shown, nowMs);
    const tgt = targetMix(shown, galaxy);
    Object.assign(goal, tgt);
    stepMix(mix, tgt, dt, reduced);
    const fresh = nowMs - voiceAt;
    voice = voiceStep(voice, voiceTarget(shown, voiceIn, fresh, reduced) * (night ? 0.3 : 1), dt);
    advanceClocks(k, dt, mix, voice, reduced);
    const palDt = reduced ? 1e6 : dt;
    const want = blendPalette(themePal, blockedPal, mix.bl);
    const kp = 1 - Math.exp(-palDt / PALETTE_TAU_S);
    pal = blendPalette(pal, want, mix.bl >= 1 ? 1 : kp);
    dimA = approach(dimA, connected ? 1 : 0.6, reduced ? 1e6 : dt, 0.3);
    if (bootAt > 0) {
      boot = Math.min(1, (nowMs - bootAt) / (BOOT_S * 1000));
      if (boot >= 1) {
        bootAt = -1;
        flareAt = nowMs / 1000;
      }
    }
    if (placeSet) {
      const lay = reduced ? 1e6 : dt;
      pcx = approach(pcx, pcxT, lay, LAYOUT_TAU_S);
      pcy = approach(pcy, pcyT, lay, LAYOUT_TAU_S);
      pr = approach(pr, prT, lay, LAYOUT_TAU_S);
      if (Math.abs(pcx - pcxT) < LAYOUT_SNAP_PX && Math.abs(pcy - pcyT) < LAYOUT_SNAP_PX && Math.abs(pr - prT) < LAYOUT_SNAP_PX) {
        pcx = pcxT;
        pcy = pcyT;
        pr = prT;
      }
      cx = pcx;
      cy = pcy;
      R = pr;
    } else {
      fitC = approach(fitC, fitT, dt, LAYOUT_TAU_S);
      R = 0.4 * cssH * fitC;
      cx = cssW / 2 - offX / 2;
      cy = cssH / 2 - offY / 2;
    }
    while (pushes.length > 0 && (nowMs - (pushes[0] as Push).t0) / 1000 > PUSH_LIFE_S) pushes.shift();
    placeFollowers();
  }

  function busy(nowMs: number): boolean {
    if (pushes.length > 0) return true;
    if (bootAt > 0) return true;
    if (nowMs / 1000 - flareAt < 1.45) return true;
    if (!placeSet) return true;
    if (Math.abs(pcx - pcxT) + Math.abs(pcy - pcyT) + Math.abs(pr - prT) > 0) return true;
    if (dwell.pending(nowMs)) return true;
    return false;
  }

  function frame(ts: number): boolean {
    if (lastTs > 0) {
      rafHead = push(rafRing, rafHead, ts - lastTs);
      rafN = Math.min(RAF_WINDOW, rafN + 1);
      if (rafN === RAF_WINDOW && rafHead === 0) {
        const med = pct(rafRing, rafN, scratch, 0.5);
        if (med > 1) refreshMs = med;
      }
    }
    if (frameCap !== null) {
      const div = Math.max(1, Math.round(1000 / frameCap / Math.max(refreshMs, 1)));
      capSkip = (capSkip + 1) % div;
      if (capSkip !== 0) return true;
    }
    const raw = lastTs > 0 ? ts - lastTs : refreshMs;
    lastTs = ts;
    if (raw > 0 && raw < 1500) {
      const bin = Math.min(HIST_BINS - 1, Math.round(raw));
      ivHist[bin] = (ivHist[bin] ?? 0) + 1;
      ivHead = push(ivRing, ivHead, raw);
      ivN = Math.min(IV_WINDOW, ivN + 1);
    }
    const t0 = performance.now();
    const dt = Math.min(Math.max(raw, 1), 50) / 1000;
    ensureSized();
    step(dt, t0);
    draw(t0 / 1000, dt, t0);
    drawWave(dt, k.clock);
    if (voiceTrace && voiceTrace.length < 70000 && (shown === 'speaking' || ribA > 0.001)) voiceTrace.push(t0, nowFresh(t0) ? voiceIn : 0, voice, ribA, 0, R, 0);
    const after = performance.now();
    if (renderedPending) {
      renderedPending = false;
      options.onStateRendered?.(shown, after);
    }
    resolveMarks(after);
    costHead = push(costRing, costHead, after - t0);
    costN = Math.min(COST_WINDOW, costN + 1);
    const tier = TIERS[governor.tier];
    const budget = 1000 / Math.max(1, 1000 / Math.max(refreshMs, 1));
    governor.frame(raw, gpuLast, after, budget);
    gpuLast = null;
    if (tier && tier !== TIERS[governor.tier]) ensureSized();
    const still = settledStill(mix, goal) && !busy(after);
    if (still) {
      frozen = true;
      lastTs = 0;
      return false;
    }
    if (!animating) {
      if (freezeAt === 0) freezeAt = after;
      const settled = Math.abs(mix.li - goal.li) + Math.abs(mix.th - goal.th) + Math.abs(mix.sp - goal.sp) + Math.abs(mix.ga - goal.ga) + Math.abs(mix.bl - goal.bl) < 0.01;
      if ((settled && !busy(after)) || after - freezeAt > FREEZE_MAX_MS) {
        frozen = true;
        lastTs = 0;
        return false;
      }
    }
    return true;
  }

  function nowFresh(now: number): boolean {
    return now - voiceAt <= 500;
  }

  function resolveMarks(nowMs: number): void {
    for (const m of marks) {
      if (!m.live) continue;
      if (m.first < 0) {
        m.first = nowMs - m.t0;
        changeLog[changeHead] = m.first;
        changeHead = (changeHead + 1) % CHANGE_WINDOW;
        changeN = Math.min(CHANGE_WINDOW, changeN + 1);
        if (m.budget === 0) {
          options.onMark?.({ label: m.label, firstMs: m.first, settleMs: null, budgetMs: 0, dropped: false } satisfies MarkReport);
          m.live = false;
          continue;
        }
      }
      const settled = Math.abs(mix.li - goal.li) + Math.abs(mix.th - goal.th) + Math.abs(mix.sp - goal.sp) + Math.abs(mix.bl - goal.bl) < 0.02;
      if (m.budget > 0 && m.settle < 0 && !m.dropped && settled) {
        m.settle = nowMs - m.t0;
        lastSettle = { label: m.label, ms: m.settle, budget: m.budget };
        options.onMark?.({ label: m.label, firstMs: m.first, settleMs: m.settle, budgetMs: m.budget, dropped: false });
        m.live = false;
        continue;
      }
      if (m.dropped || nowMs - m.t0 > 3000) {
        options.onMark?.({ label: m.label, firstMs: m.first, settleMs: null, budgetMs: m.budget, dropped: true });
        m.live = false;
      }
    }
  }

  const onLost = (e: Event): void => {
    e.preventDefault();
    lost = true;
    options.onPath?.('fallback', 'WebGL 2 context lost');
  };
  const onRestored = (): void => {
    window.tessa?.reportMetrics?.('SCENE context restored: the page must rebuild the scene; the Orb restarts its renderer on the next launch');
  };
  const onVisibility = (): void => {
    if (document.hidden) {
      lastTs = 0;
      governor.reset(performance.now());
      return;
    }
    wake();
  };
  let resizeTimer = 0;
  const observer = new ResizeObserver(() => {
    if (resizeTimer) return;
    resizeTimer = window.setTimeout(() => {
      resizeTimer = 0;
      if (disposed) return;
      ribbonDirty = true;
      wake();
    }, 60);
  });
  canvas.addEventListener('webglcontextlost', onLost, false);
  canvas.addEventListener('webglcontextrestored', onRestored, false);
  document.addEventListener('visibilitychange', onVisibility);
  observer.observe(canvas);
  ensureSized();
  options.onPath?.('webgl', `webgl2 scene (${fmt.name}, ${clock ? 'gpu timer' : 'no gpu timer'})`);
  loop.start();

  const stats = (): PlasmaStats => {
    const n = ivN;
    let sum = 0;
    let dropped = 0;
    for (let i = 0; i < n; i++) {
      const v = ivRing[i] ?? 0;
      sum += v;
      if (v > refreshMs * 1.5) dropped += 1;
    }
    return {
      path: 'webgl',
      reason: `webgl2 scene ${TIERS[tierNow()]?.name ?? ''}`,
      renderer,
      rendererShort: shortRenderer(renderer),
      timerQuery: clock !== null,
      fps: n > 0 ? 1000 / (sum / n) : 0,
      intervalP50: pct(ivRing, n, scratch, 0.5),
      intervalP95: pct(ivRing, n, scratch, 0.95),
      dropped,
      samples: n,
      scale: TIERS[tierNow()]?.scale ?? 1,
      gpuP50: pct(gpuRing, gpuN, scratch, 0.5),
      gpuP90: pct(gpuRing, gpuN, scratch, 0.9),
      costP50: pct(costRing, costN, scratch, 0.5),
      costP95: pct(costRing, costN, scratch, 0.95),
      framesDrawn,
      targetFps: frameCap ?? 1000 / Math.max(refreshMs, 1),
      refreshMs,
      focused: document.hasFocus(),
      changeP95: pct(changeLog, changeN, scratch, 0.95),
      changeCount: changeN,
      lastSettle,
      canvas: { cssW, cssH, bufW: canvas.width, bufH: canvas.height },
      radius: R,
      centre: { x: cx, y: cy },
      maxShaderInput: k.flow,
      load: 0,
      cpu: 0,
      mem: 0,
    };
  };

  return {
    setPlacement(x, y, r, snap) {
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(r) || r <= 0) return;
      pcxT = x;
      pcyT = y;
      prT = r;
      if (!placeSet || snap === true) {
        placeSet = true;
        pcx = x;
        pcy = y;
        pr = r;
      }
      wake();
    },
    setCompact(on) {
      if (on === compact) return;
      compact = on;
      chipX = chipY = capX = capY = quickX = quickY = -1e9;
      capW = quickW = -1;
      ribbonDirty = true;
      spaceDirty = true;
      wake();
    },
    grow() {
      if (boot >= 1 || bootAt > 0) return;
      bootAt = performance.now();
      wake();
    },
    setFollowers(f) {
      followers = f;
      chipX = chipY = capX = capY = quickX = quickY = -1e9;
      capW = quickW = -1;
      wake();
    },
    setCentreOffset(xPx, yPx) {
      offX = Number.isFinite(xPx) ? xPx : 0;
      offY = Number.isFinite(yPx) ? yPx : 0;
      wake();
    },
    setFit(factor) {
      fitT = Number.isFinite(factor) ? Math.min(1.2, Math.max(0.2, factor)) : 1;
      wake();
    },
    retint() {
      readPalettes();
      wake();
    },
    beat() {},
    setLoad() {},
    setConnected(on) {
      if (on === connected) return;
      connected = on;
      wake();
    },
    setJobs(list) {
      jobs = list;
      wake();
    },
    setVoiceLevel(level) {
      voiceIn = Number.isFinite(level) ? clamp01(level) : 0;
      voiceAt = performance.now();
      if (shown === 'speaking') loop.poke();
    },
    setMuted() {},
    setNight(on) {
      night = on;
      wake();
    },
    flare() {
      flareAt = performance.now() / 1000;
      wake();
    },
    noteStateChange(at) {
      const slot = marks.find((m) => !m.live);
      if (slot) Object.assign(slot, { live: true, label: 'State', t0: at, budget: 0, first: -1, settle: -1, dropped: false });
      pendingAt = at;
      wake();
    },
    mark(label, t0, budgetMs) {
      const slot = marks.find((m) => !m.live) ?? marks[0];
      if (!slot) return;
      Object.assign(slot, { live: true, label, t0, budget: budgetMs, first: -1, settle: -1, dropped: false });
      wake();
    },
    setFrameCap(fps) {
      frameCap = fps !== null && Number.isFinite(fps) && fps > 0 ? fps : null;
    },
    reprobeRefresh() {
      refreshMs = 1000 / 60;
      rafN = 0;
      rafHead = 0;
      lastTs = 0;
      wake();
    },
    setAnimating(on) {
      if (disposed) return;
      if (on) {
        freezeAt = 0;
        if (animating) return;
        animating = true;
        lastTs = 0;
        wake();
        return;
      }
      if (!animating) return;
      animating = false;
      freezeAt = 0;
      wake();
    },
    loseContextForTest(restoreAfterMs) {
      const ext = gl.getExtension('WEBGL_lose_context');
      if (!ext) return false;
      ext.loseContext();
      if (restoreAfterMs >= 0) window.setTimeout(() => ext.restoreContext(), restoreAfterMs);
      return true;
    },
    stats,
    takeIntervals() {
      const out = Array.from(ivHist);
      ivHist.fill(0);
      return out;
    },
    traceVoice(on) {
      voiceTrace = on ? [] : null;
    },
    takeVoiceTrace() {
      if (!voiceTrace) return [];
      const out = voiceTrace;
      voiceTrace = [];
      return out;
    },
    dispose() {
      disposed = true;
      loop.stop();
      window.clearTimeout(resizeTimer);
      observer.disconnect();
      canvas.removeEventListener('webglcontextlost', onLost);
      canvas.removeEventListener('webglcontextrestored', onRestored);
      document.removeEventListener('visibilitychange', onVisibility);
      clock?.dispose();
      clock = null;
      dropTarget(gl, sceneT);
      dropTarget(gl, spaceT);
      for (const b of bloom) dropTarget(gl, b);
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    },
    setStage(box) {
      stage = box;
      wake();
    },
    setCallouts(els) {
      callEls = [...els];
      slotStamp[0] = -1;
      slotStamp[1] = -1;
      wake();
    },
    setPlanetSources(src) {
      sources = src;
      sourcesStamp += 1;
      wake();
    },
    setOnBattery(on) {
      governor.setBattery(on, performance.now());
      wake();
    },
    pinQuality(tier) {
      governor.pin(tier);
      wake();
    },
    setOverlays(inShader) {
      overlaysInShader = inShader;
      wake();
    },
    push(x, y, strength) {
      if (mix.bl > 0.5 || !Number.isFinite(x) || !Number.isFinite(y)) return;
      pushes.push({ x, y, s: Math.max(0, Math.min(2, strength)), t0: performance.now() });
      while (pushes.length > 4) pushes.shift();
      pushR = Math.max(60, R * 0.55);
      wake();
    },
    sceneStats() {
      return {
        tier: tierNow(),
        tierName: TIERS[tierNow()]?.name ?? '',
        pinned: governor.isPinned(),
        onBattery: governor.onBattery(),
        changes: governor.changes,
        points: pointsDrawn,
        frozen,
        galaxy: mix.ga,
        gpuTimer: clock !== null,
        format: fmt.name,
        overlays: overlaysInShader ? 'shader' : 'css',
        callouts: slotShown.map((p) => (p === null || p === undefined ? '-' : (PLANETS[p]?.name ?? '?'))),
        unit: R * UNIT_PER_R,
        shellPx: R * UNIT_PER_R * ORB_R * (ORB_CAM / Math.sqrt(ORB_CAM * ORB_CAM - ORB_R * ORB_R)),
        bootDone: boot >= 1,
      };
    },
  };
}

