import type { AgentState } from '@tessa/protocol';

import { tokenValue } from '../design-tokens.ts';
import { drawFallback, type FallbackView } from './plasma-fallback.ts';
import { bindProgram, buildGl, getContext, shortRenderer, type GlState } from './plasma-gl.ts';
import {
  BREATH_ENERGY,
  BREATH_PERIOD_S,
  BREATH_SIZE,
  CAPTION_GAP_PX,
  CAPTION_H_PX,
  CAPTION_W_R,
  CHIP_ABOVE_PX,
  QUICK_H_PX,
  QUICK_W_MAX_PX,
  CHANGE_JUMP,
  CPU_TAU_S,
  DIM_SPEED,
  DIM_TAU_S,
  FLARE_ALERT,
  FLARE_WHITE_OTHER,
  FLARE_WHITE_RED,
  FLARE_WINDOW_S,
  FLOW_RATE_A,
  FLOW_RATE_B,
  FLOW_RATE_C,
  FLOW_RATE_D,
  HEART_RIM,
  HEART_SIZE,
  HEART_WINDOW_S,
  IDLE_FLOW,
  LAYOUT_SNAP_PX,
  LAYOUT_TAU_S,
  LOAD_CPU_WEIGHT,
  LOAD_MEM_WEIGHT,
  LOAD_TAU_S,
  MAX_RINGS,
  MEM_TAU_S,
  MUTED_TAU_S,
  NIGHT_TAU_S,
  NIGHT_VOICE_CUT,
  NOISE_PERIOD,
  ORB_QUAD,
  OVERLAY_QUAD,
  PALETTE_TAU_S,
  PARAM_KEYS,
  RIBBON_IN_TAU_S,
  RIBBON_OUT_TAU_S,
  RIBBON_RATE_HZ,
  RIBBON_SAMPLES,
  RING_AMBER_TAU_S,
  RING_CANCEL_TAU_S,
  RING_DONE_HOLD_S,
  RING_DONE_TAU_S,
  RING_FADE_IN_TAU_S,
  RING_HEAD_BASE,
  RING_HEAD_STEP,
  RING_PROGRESS_TAU_S,
  RING_REMOVE_S,
  RING_SLOTS,
  RING_WOBBLE_X,
  RING_WOBBLE_Z,
  SETTLE_FRACTION,
  SPARK_RATE_X,
  SPARK_RATE_Y,
  SPARK_RATE_Z,
  STATE_PARAMS,
  SURFACE_ROLL_B,
  SURFACE_SPIN_B,
  SURFACE_TILT_A,
  SURFACE_TILT_B,
  SWEEP_PERIOD_S,
  TAU,
  THEME_JUMP,
  THEME_TICK,
  TICK_TAU_S,
  TIGHT_FREQ,
  VOICE_ATTACK_TAU_S,
  VOICE_ENERGY,
  VOICE_FREQ,
  VOICE_RELEASE_TAU_S,
  VOICE_RIM,
  VOICE_SIZE,
  VOICE_STALE_S,
  approach,
  flareEnvelope,
  heartEnvelope,
  ribbonBox,
  transitionTau,
  wrap,
  type StateParams,
} from './plasma-model.ts';

export type RenderPath = 'webgl' | 'fallback';

export interface JobRingInput {
  id: string;
  progress: number;
  waiting: boolean;
  finished: 'none' | 'ok' | 'cancelled';
}

export interface MarkReport {
  label: string;
  firstMs: number;
  settleMs: number | null;
  budgetMs: number;
  dropped: boolean;
}

export interface PlasmaStats {
  path: RenderPath;
  reason: string;
  renderer: string;
  rendererShort: string;
  timerQuery: boolean;
  fps: number;
  intervalP50: number;
  intervalP95: number;
  dropped: number;
  samples: number;
  scale: number;
  gpuP50: number;
  gpuP90: number;
  costP50: number;
  costP95: number;
  framesDrawn: number;
  targetFps: number;
  refreshMs: number;
  focused: boolean;
  changeP95: number;
  changeCount: number;
  lastSettle: { label: string; ms: number; budget: number } | null;
  canvas: { cssW: number; cssH: number; bufW: number; bufH: number };
  radius: number;
  centre: { x: number; y: number };
  maxShaderInput: number;
  load: number;
  cpu: number;
  mem: number;
}

export interface PlasmaEngineOptions {
  canvas: HTMLCanvasElement;
  ribbon?: HTMLCanvasElement | null;
  getState: () => AgentState;
  transparent?: boolean;
  radius?: (w: number, h: number) => number;
  forceFallback?: boolean;
  clock?: { hours: number; raw: boolean } | null;
  onStateRendered?: (state: AgentState, at: number) => void;
  onMark?: (mark: MarkReport) => void;
  onSentinel?: (alert: boolean) => void;
  onPath?: (path: RenderPath, reason: string) => void;
}

export interface Followers {
  chip?: HTMLElement | null;
  caption?: HTMLElement | null;
  quick?: HTMLElement | null;
}

export interface PlasmaEngine {
  setPlacement(cx: number, cy: number, r: number): void;
  setFollowers(f: Followers): void;
  setCentreOffset(xPx: number, yPx: number): void;
  setFit(factor: number): void;
  retint(): void;
  beat(): void;
  setLoad(cpu: number, mem: number): void;
  setConnected(on: boolean): void;
  setJobs(jobs: readonly JobRingInput[]): void;
  setVoiceLevel(level: number): void;
  setMuted(on: boolean): void;
  setNight(on: boolean): void;
  flare(): void;
  noteStateChange(at: number, tauS?: number): void;
  mark(label: string, t0: number, budgetMs: number): void;
  setFrameCap(fps: number | null): void;
  reprobeRefresh(): void;
  setAnimating(on: boolean): void;
  loseContextForTest(restoreAfterMs: number): boolean;
  stats(): PlasmaStats;
  takeIntervals(): number[];
  dispose(): void;
}

interface Slot {
  id: string | null;
  seen: boolean;
  prog: number;
  progT: number;
  alpha: number;
  amb: number;
  waiting: boolean;
  done: boolean;
  doneT: number;
  cancelled: boolean;
  head: number;
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

const MARK_SLOTS = 8;
const RAF_WINDOW = 120;
const INTERVAL_WINDOW = 240;
const GPU_WINDOW = 60;
const COST_WINDOW = 120;
const CHANGE_WINDOW = 60;
const GOVERNOR_EVERY_MS = 500;
const SCALE_MIN = 0.5;
const SCALE_STEP = 0.1;
const GPU_HIGH = 0.7;
const GPU_LOW = 0.45;
const DROP_LIMIT = 0.05;
const RAISE_AFTER_MS = 8000;
const WARMUP_MS = 3000;
const STALL_MS = 250;
const HIST_BINS = 128;
const FALLBACK_FPS = 30;
const RESTORE_WAIT_MS = 3000;
const FREEZE_TRANS = 0.995;
const FREEZE_TICK = 0.005;
const FREEZE_MAX_MS = 1500;
const FLOW_JS_PERIOD = 20 * Math.PI;
const SCENE_PERIOD = 200 * Math.PI;
const STAGE_RADIUS = (_w: number, h: number): number => 0.4 * h;

function parseColour(value: string, out: Float32Array): boolean {
  const v = value.trim();
  if (v.length === 7 && v.charCodeAt(0) === 35) {
    const n = Number.parseInt(v.slice(1), 16);
    if (!Number.isFinite(n)) return false;
    out[0] = ((n >> 16) & 255) / 255;
    out[1] = ((n >> 8) & 255) / 255;
    out[2] = (n & 255) / 255;
    return true;
  }
  const m = /^rgba?\(([^)]+)\)$/.exec(v);
  if (!m) return false;
  const parts = (m[1] ?? '').split(',').map((p) => Number.parseFloat(p));
  if (parts.length < 3 || parts.slice(0, 3).some((p) => !Number.isFinite(p))) return false;
  out[0] = (parts[0] ?? 0) / 255;
  out[1] = (parts[1] ?? 0) / 255;
  out[2] = (parts[2] ?? 0) / 255;
  return true;
}

function readToken(name: string, out: Float32Array): void {
  parseColour(tokenValue(name), out);
}

function percentileOf(src: Float64Array, n: number, scratch: Float64Array, p: number): number {
  if (n <= 0) return 0;
  for (let i = 0; i < n; i++) scratch[i] = src[i] ?? 0;
  const view = scratch.subarray(0, n);
  view.sort();
  return view[Math.min(n - 1, Math.floor(n * p))] ?? 0;
}

function writeSurface(out: Float32Array, a: number, b: number, g: number): void {
  const ca = Math.cos(a);
  const sa = Math.sin(a);
  const cb = Math.cos(b);
  const sb = Math.sin(b);
  const cg = Math.cos(g);
  const sg = Math.sin(g);
  const p00 = ca;
  const p01 = sa * sb;
  const p02 = sa * cb;
  const p11 = cb;
  const p12 = -sb;
  const p20 = -sa;
  const p21 = ca * sb;
  const p22 = ca * cb;
  out[0] = p00 * cg + p01 * sg;
  out[1] = p11 * sg;
  out[2] = p20 * cg + p21 * sg;
  out[3] = -p00 * sg + p01 * cg;
  out[4] = p11 * cg;
  out[5] = -p20 * sg + p21 * cg;
  out[6] = p02;
  out[7] = p12;
  out[8] = p22;
}

export function createPlasmaEngine(options: PlasmaEngineOptions): PlasmaEngine {
  const { canvas, getState } = options;
  const transparent = options.transparent === true;
  const radiusOf = options.radius ?? STAGE_RADIUS;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const raw = options.clock?.raw === true;
  const w = (value: number, period: number): number => (raw ? value : wrap(value, period));

  let disposed = false;
  let animating = true;
  let freezeAt = 0;
  let rafId = 0;

  let path: RenderPath = 'webgl';
  let reason = 'webgl';
  let gls: GlState | null = null;
  let gl: WebGLRenderingContext | null = null;
  let renderer = 'none';
  let restoreTimer = 0;
  let fbCanvas: HTMLCanvasElement | null = null;
  let fbCtx: CanvasRenderingContext2D | null = null;

  let shown: AgentState = getState();
  const cur: StateParams = { ...STATE_PARAMS[shown] };
  let tau = 0.11;
  let trans = 1;
  let changeAt = 0;
  let pendingAt = -1;
  let pendingTau = -1;
  let renderedPending = false;

  let rot = 0.4;
  let rot2 = 1.7;
  let flowJs = 0;
  let breath = 0;
  let sweep = 0;
  let tight = 0;
  let voiceFlow = 0;
  let scene = 0;
  let offA = 0;
  let offB = 0;
  let offC = 0;
  let offD = 0;
  const offSpark = new Float32Array(3);
  const off4 = new Float32Array(4);

  let voice = 0;
  let voiceIn = 0;
  let voiceAt = -1e9;
  let tick = 0;
  const tickCol = new Float32Array(3);
  let heartStart = -1e9;
  let heart = 0;
  let flareStart = -1e9;
  let flareV = 0;
  let sentinelOn = false;

  let connected = true;
  let dim = 0;
  let muted = false;
  let mutedA = 0;
  let night = false;
  let nightA = 0;
  let cpuIn = 0;
  let memIn = 0;
  let cpu = 0;
  let mem = 0;
  let loadA = 0;

  const pal = { deep: new Float32Array(3), mid: new Float32Array(3), hot: new Float32Array(3), bg: new Float32Array(3) };
  const palT = { deep: new Float32Array(3), mid: new Float32Array(3), hot: new Float32Array(3), bg: new Float32Array(3) };
  const amber = { deep: new Float32Array(3), mid: new Float32Array(3), hot: new Float32Array(3), head: new Float32Array(3) };
  const flareCol = new Float32Array(3);
  const outCol = { deep: new Float32Array(3), mid: new Float32Array(3), hot: new Float32Array(3) };
  let themeIsRed = false;

  let cssW = 1;
  let cssH = 1;
  let effDpr = 1;
  let baseR = 100;
  let fitT = 1;
  let fitC = 1;
  let offXT = 0;
  let offYT = 0;
  let offXC = 0;
  let offYC = 0;
  let layoutSet = false;
  let placeSet = false;
  let pcxT = 0;
  let pcyT = 0;
  let prT = 100;
  let pcx = 0;
  let pcy = 0;
  let pr = 100;
  let followers: Followers = {};
  let chipX = -1e9;
  let chipY = -1e9;
  let capX = -1e9;
  let capY = -1e9;
  let capW = -1;
  let quickX = -1e9;
  let quickY = -1e9;
  let quickW = -1;
  let cx = 0;
  let cy = 0;
  let R = 100;
  let orbR = 100;
  let energy = 1;
  let rim = 1;
  let scale = 1;
  let maxShaderInput = 0;

  const mA = new Float32Array(9);
  const mB = new Float32Array(9);
  const ringM = new Float32Array(36);
  const ringA = new Float32Array(16);
  const ringH = new Float32Array(16);
  const slots: Slot[] = [];
  for (let i = 0; i < MAX_RINGS; i++) {
    slots.push({ id: null, seen: false, prog: 0, progT: 0, alpha: 0, amb: 0, waiting: false, done: false, doneT: 0, cancelled: false, head: [0.3, 1.9, 3.3, 4.6][i] ?? 0 });
  }

  const ribbonCanvas = options.ribbon ?? null;
  const ribCtx = ribbonCanvas ? ribbonCanvas.getContext('2d') : null;
  const hist = new Float32Array(RIBBON_SAMPLES);
  let hHead = 0;
  let hAcc = 0;
  let ribbonA = 0;
  let ribbonDirty = true;
  let ribW = 0;
  let ribH = 0;
  let ribX = -1e9;
  let ribY = -1e9;
  let ribFill: CanvasGradient | null = null;
  let ribStroke: CanvasGradient | null = null;
  let ribCleared = false;
  let ribSigW = -1;
  let ribSigMid = -1;
  let ribSigHot = -1;
  const ribDash: number[] = [2, 6];
  const ribSolid: number[] = [];

  const marks: Mark[] = [];
  for (let i = 0; i < MARK_SLOTS; i++) marks.push({ live: false, label: '', t0: 0, budget: 0, first: -1, settle: -1, dropped: false });
  const changeLog = new Float64Array(CHANGE_WINDOW);
  let changeN = 0;
  let changeHead = 0;
  let lastSettle: { label: string; ms: number; budget: number } | null = null;

  const rafRing = new Float64Array(RAF_WINDOW);
  let rafN = 0;
  let rafHead = 0;
  let refreshMs = 1000 / 60;
  const ivRing = new Float64Array(INTERVAL_WINDOW);
  let ivN = 0;
  let ivHead = 0;
  const gpuRing = new Float64Array(GPU_WINDOW);
  let gpuN = 0;
  let gpuHead = 0;
  const costRing = new Float64Array(COST_WINDOW);
  let costN = 0;
  let costHead = 0;
  const scratch = new Float64Array(INTERVAL_WINDOW);
  let lastRafAt = 0;
  let lastDrawAt = 0;
  let tickCounter = 0;
  let frameCap: number | null = null;
  let lastGov = 0;
  let govIvHead = 0;
  let govIvCount = 0;
  let slowChecks = 0;
  let emergencyChecks = 0;
  let fastSince = 0;
  let framesDrawn = 0;
  let lastBudget = 0;
  const startedAt = performance.now();
  const ivHist = new Uint32Array(HIST_BINS);

  const fbView: FallbackView = {
    w: 1, h: 1, dpr: 1, transparent, cx: 0, cy: 0, r: 1, baseR: 1, energy: 1, rim: 1, core: 1, hollow: 0,
    sweepAmt: 0, sweepPhase: 0, spark: 0, rot: 0, flow: 0, tight: 0, tightPhase: 0, tick: 0, heart: 0, flare: 0,
    dim: 0, muted: 0, night: 0, load: 0, deep: outCol.deep, mid: outCol.mid, hot: outCol.hot, bg: pal.bg,
    tickCol, flareCol, amber: amber.mid, ringA,
  };

  function pushRing(ring: Float64Array, value: number, head: number): number {
    ring[head] = value;
    return (head + 1) % ring.length;
  }

  function readPalette(target: typeof palT): void {
    readToken('--orb-deep', target.deep);
    readToken('--orb-mid', target.mid);
    readToken('--orb-hot', target.hot);
    readToken('--orb-bg', target.bg);
    readToken('--blocked-deep', amber.deep);
    readToken('--blocked-mid', amber.mid);
    readToken('--blocked-hot', amber.hot);
    for (let i = 0; i < 3; i++) amber.head[i] = (amber.mid[i] ?? 0) + ((amber.hot[i] ?? 0) - (amber.mid[i] ?? 0)) * 0.6;
    readToken('--threat', flareCol);
    themeIsRed = document.documentElement.dataset['theme'] === 'red';
  }

  readPalette(palT);
  pal.deep.set(palT.deep);
  pal.mid.set(palT.mid);
  pal.hot.set(palT.hot);
  pal.bg.set(palT.bg);
  tickCol.set(pal.hot);

  if (options.clock && options.clock.hours > 0) {
    const t = options.clock.hours * 3600;
    const idle = STATE_PARAMS.idle;
    const dFlow = (IDLE_FLOW + idle.churn) * t;
    rot = w(rot + idle.spin * t, TAU);
    rot2 = w(rot2 - idle.spin * SURFACE_SPIN_B * t, TAU);
    flowJs = w(dFlow, FLOW_JS_PERIOD);
    offA = w(dFlow * FLOW_RATE_A, NOISE_PERIOD);
    offB = w(dFlow * FLOW_RATE_B, NOISE_PERIOD);
    offC = w(dFlow * FLOW_RATE_C * (0.55 + 0.9 * idle.detail), NOISE_PERIOD);
    offD = w(dFlow * FLOW_RATE_D, NOISE_PERIOD);
    offSpark[0] = w(dFlow * SPARK_RATE_X, NOISE_PERIOD);
    offSpark[1] = w(dFlow * SPARK_RATE_Y, NOISE_PERIOD);
    offSpark[2] = w(dFlow * SPARK_RATE_Z, NOISE_PERIOD);
    breath = w((t * TAU) / BREATH_PERIOD_S, TAU);
    sweep = w((t * Math.PI) / SWEEP_PERIOD_S, Math.PI);
    tight = w(t * 0.5, TAU / TIGHT_FREQ);
    voiceFlow = w(t * 0.4, TAU / VOICE_FREQ);
    scene = w(t, SCENE_PERIOD);
    for (let k = 0; k < MAX_RINGS; k++) {
      const s = slots[k];
      if (s) s.head = w(s.head + t * (RING_HEAD_BASE + RING_HEAD_STEP * k), TAU);
    }
  }

  function setPath(next: RenderPath, why: string): void {
    const changed = next !== path || why !== reason;
    path = next;
    reason = why;
    if (next === 'fallback') {
      if (!fbCanvas) {
        fbCanvas = document.createElement('canvas');
        fbCanvas.className = canvas.className;
        fbCanvas.setAttribute('aria-hidden', 'true');
        canvas.parentElement?.insertBefore(fbCanvas, canvas.nextSibling);
        fbCtx = fbCanvas.getContext('2d');
      }
      fbCanvas.hidden = false;
      sizeFallback();
    } else if (fbCanvas) {
      fbCanvas.hidden = true;
    }
    if (changed) options.onPath?.(next, why);
  }

  function startGl(): void {
    if (options.forceFallback) {
      setPath('fallback', 'forced by --force-fallback');
      return;
    }
    const ctx = getContext(canvas, transparent);
    if (!ctx) {
      setPath('fallback', 'no hardware WebGL context (unavailable, or only with a performance caveat)');
      return;
    }
    gl = ctx;
    try {
      gls = buildGl(ctx);
    } catch (err) {
      gls = null;
      setPath('fallback', `shader build failed: ${String((err as Error).message ?? err).slice(0, 160)}`);
      return;
    }
    renderer = gls.renderer;
    if (gls.software) {
      setPath('fallback', `software renderer (${shortRenderer(renderer)})`);
      return;
    }
    setPath('webgl', 'webgl');
  }

  function onLost(event: Event): void {
    event.preventDefault();
    gls = null;
    setPath('fallback', 'WebGL context lost');
    window.clearTimeout(restoreTimer);
    restoreTimer = window.setTimeout(() => {
      if (path === 'fallback' && reason === 'WebGL context lost') setPath('fallback', 'WebGL context lost and not restored');
    }, RESTORE_WAIT_MS);
    requestDraw();
  }

  function onRestored(): void {
    window.clearTimeout(restoreTimer);
    if (!gl) return;
    try {
      gls = buildGl(gl);
      renderer = gls.renderer;
      if (gls.software) {
        setPath('fallback', `software renderer after restore (${shortRenderer(renderer)})`);
        return;
      }
      appliedW = -1;
      setPath('webgl', 'webgl (context restored, programs rebuilt)');
      ensureSized();
    } catch (err) {
      gls = null;
      setPath('fallback', `restore failed: ${String((err as Error).message ?? err).slice(0, 160)}`);
    }
    requestDraw();
  }

  let appliedW = -1;
  let appliedH = -1;
  let appliedScale = -1;

  function sizeFallback(): void {
    if (!fbCanvas) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const bw = Math.max(1, Math.round(cssW * dpr));
    const bh = Math.max(1, Math.round(cssH * dpr));
    if (fbCanvas.width !== bw) fbCanvas.width = bw;
    if (fbCanvas.height !== bh) fbCanvas.height = bh;
    fbView.dpr = dpr;
  }

  function ensureSized(): void {
    const cw = canvas.clientWidth || 1;
    const ch = canvas.clientHeight || 1;
    if (cw === appliedW && ch === appliedH && scale === appliedScale) return;
    appliedW = cw;
    appliedH = ch;
    appliedScale = scale;
    cssW = cw;
    cssH = ch;
    const dpr = Math.min(window.devicePixelRatio || 1, 2) * scale;
    const bw = Math.max(1, Math.round(cw * dpr));
    const bh = Math.max(1, Math.round(ch * dpr));
    if (canvas.width !== bw) canvas.width = bw;
    if (canvas.height !== bh) canvas.height = bh;
    effDpr = bw / cw;
    gls?.gl.viewport(0, 0, bw, bh);
    baseR = radiusOf(cw, ch);
    sizeFallback();
    ribbonDirty = true;
  }

  function beginTransition(prev: AgentState, next: AgentState, now: number): void {
    tau = pendingTau > 0 ? pendingTau : transitionTau(prev, next);
    changeAt = pendingAt >= 0 ? pendingAt / 1000 : now;
    pendingAt = -1;
    pendingTau = -1;
    const tgt = STATE_PARAMS[next];
    for (let i = 0; i < PARAM_KEYS.length; i++) {
      const k = PARAM_KEYS[i] as keyof StateParams;
      cur[k] = reduced ? tgt[k] : cur[k] + (tgt[k] - cur[k]) * CHANGE_JUMP;
    }
    trans = reduced ? 1 : CHANGE_JUMP;
    tick = reduced ? 0 : 1;
    tickCol.set(next === 'blocked' ? amber.hot : pal.hot);
    const t0 = changeAt * 1000;
    for (let i = 0; i < marks.length; i++) {
      const m = marks[i];
      if (m && m.live && m.budget > 0 && m.settle < 0 && m.t0 < t0 - 0.5) m.dropped = true;
    }
    shown = next;
    renderedPending = true;
  }

  function step(dt: number, nowMs: number): void {
    const now = nowMs / 1000;
    const st = getState();
    if (st !== shown) beginTransition(shown, st, now);
    const tgt = STATE_PARAMS[shown];
    const dtP = reduced ? 1e6 : Math.min(dt, Math.max(0, now - changeAt));
    for (let i = 0; i < PARAM_KEYS.length; i++) {
      const k = PARAM_KEYS[i] as keyof StateParams;
      cur[k] = approach(cur[k], tgt[k], dtP, tau);
    }
    trans = approach(trans, 1, dtP, tau);

    const live = 1 - DIM_SPEED * dim;
    const still = 1 - cur.hollow;
    const motion = reduced ? 0 : 1;
    const rate = still * live * motion;
    rot = w(rot + cur.spin * dt * live * motion, TAU);
    rot2 = w(rot2 - cur.spin * SURFACE_SPIN_B * dt * live * motion, TAU);
    const dFlow = (IDLE_FLOW * still + cur.churn) * dt * live * motion;
    flowJs = w(flowJs + dFlow, FLOW_JS_PERIOD);
    offA = w(offA + dFlow * FLOW_RATE_A, NOISE_PERIOD);
    offB = w(offB + dFlow * FLOW_RATE_B, NOISE_PERIOD);
    offC = w(offC + dFlow * FLOW_RATE_C * (0.55 + 0.9 * cur.detail), NOISE_PERIOD);
    offD = w(offD + dFlow * FLOW_RATE_D, NOISE_PERIOD);
    offSpark[0] = w((offSpark[0] ?? 0) + dFlow * SPARK_RATE_X, NOISE_PERIOD);
    offSpark[1] = w((offSpark[1] ?? 0) + dFlow * SPARK_RATE_Y, NOISE_PERIOD);
    offSpark[2] = w((offSpark[2] ?? 0) + dFlow * SPARK_RATE_Z, NOISE_PERIOD);
    breath = w(breath + ((dt * TAU) / BREATH_PERIOD_S) * rate, TAU);
    sweep = w(sweep + dt * (Math.PI / SWEEP_PERIOD_S) * rate, Math.PI);
    tight = w(tight + dt * (0.5 + 1.1 * cur.tight) * rate, TAU / TIGHT_FREQ);
    scene = w(scene + dt * rate, SCENE_PERIOD);

    const fresh = nowMs - voiceAt <= VOICE_STALE_S * 1000;
    let lv = shown === 'speaking' && connected && fresh && !reduced ? voiceIn : 0;
    lv *= 1 - NIGHT_VOICE_CUT * nightA;
    voice = approach(voice, lv, dt, lv > voice ? VOICE_ATTACK_TAU_S : VOICE_RELEASE_TAU_S);
    voiceFlow = w(voiceFlow + dt * (0.4 + 3.2 * voice) * rate, TAU / VOICE_FREQ);

    if (connected) {
      const since = now - heartStart;
      heart = reduced ? (since >= 0 && since < 0.3 ? 0.6 : 0) : heartEnvelope(since);
    } else {
      heart = 0;
    }
    flareV = flareEnvelope(now - flareStart);
    const alert = flareV > FLARE_ALERT;
    if (alert !== sentinelOn) {
      sentinelOn = alert;
      options.onSentinel?.(alert);
    }

    const palDt = reduced ? 1e6 : dt;
    for (let i = 0; i < 3; i++) {
      pal.deep[i] = approach(pal.deep[i] ?? 0, palT.deep[i] ?? 0, palDt, PALETTE_TAU_S);
      pal.mid[i] = approach(pal.mid[i] ?? 0, palT.mid[i] ?? 0, palDt, PALETTE_TAU_S);
      pal.hot[i] = approach(pal.hot[i] ?? 0, palT.hot[i] ?? 0, palDt, PALETTE_TAU_S);
      pal.bg[i] = approach(pal.bg[i] ?? 0, palT.bg[i] ?? 0, palDt, PALETTE_TAU_S);
      const h = cur.hollow;
      outCol.deep[i] = (pal.deep[i] ?? 0) + ((amber.deep[i] ?? 0) - (pal.deep[i] ?? 0)) * h;
      outCol.mid[i] = (pal.mid[i] ?? 0) + ((amber.mid[i] ?? 0) - (pal.mid[i] ?? 0)) * h;
      outCol.hot[i] = (pal.hot[i] ?? 0) + ((amber.hot[i] ?? 0) - (pal.hot[i] ?? 0)) * h;
    }

    const fx = reduced ? 1e6 : dt;
    tick = reduced ? 0 : approach(tick, 0, dt, TICK_TAU_S);
    dim = approach(dim, connected ? 0 : 1, fx, DIM_TAU_S);
    mutedA = approach(mutedA, muted ? 1 : 0, fx, MUTED_TAU_S);
    nightA = approach(nightA, night ? 1 : 0, fx, NIGHT_TAU_S);
    cpu = approach(cpu, cpuIn, fx, CPU_TAU_S);
    mem = approach(mem, memIn, fx, MEM_TAU_S);
    loadA = approach(loadA, Math.max(0, Math.min(1, LOAD_CPU_WEIGHT * cpu + LOAD_MEM_WEIGHT * mem)), fx, LOAD_TAU_S);

    const lay = layoutSet ? (reduced ? 1e6 : dt) : 1e6;
    if (placeSet) {
      pcx = approach(pcx, pcxT, lay, LAYOUT_TAU_S);
      pcy = approach(pcy, pcyT, lay, LAYOUT_TAU_S);
      pr = approach(pr, prT, lay, LAYOUT_TAU_S);
      if (Math.abs(pcx - pcxT) < LAYOUT_SNAP_PX && Math.abs(pcy - pcyT) < LAYOUT_SNAP_PX && Math.abs(pr - prT) < LAYOUT_SNAP_PX) {
        pcx = pcxT;
        pcy = pcyT;
        pr = prT;
      }
      layoutSet = true;
      R = pr;
      cx = pcx;
      cy = pcy;
    } else {
      fitC = approach(fitC, fitT, lay, LAYOUT_TAU_S);
      offXC = approach(offXC, offXT, lay, LAYOUT_TAU_S);
      offYC = approach(offYC, offYT, lay, LAYOUT_TAU_S);
      layoutSet = true;
      R = baseR * fitC;
      cx = cssW / 2 - offXC / 2;
      cy = cssH / 2 - offYC / 2;
    }
    placeFollowers();

    for (let k = 0; k < MAX_RINGS; k++) {
      const s = slots[k];
      const slot = RING_SLOTS[k];
      if (!s || !slot) continue;
      if (s.id === null) {
        ringA[k * 4 + 2] = 0;
        continue;
      }
      s.amb = approach(s.amb, s.waiting ? 1 : 0, fx, RING_AMBER_TAU_S);
      if (!s.done) {
        s.prog = approach(s.prog, s.progT, fx, RING_PROGRESS_TAU_S);
        s.alpha = approach(s.alpha, 1, fx, RING_FADE_IN_TAU_S);
      } else {
        s.doneT += dt;
        if (!s.cancelled) s.prog = 1;
        const want = s.cancelled ? 0 : s.doneT < RING_DONE_HOLD_S ? 1 : 0;
        s.alpha = approach(s.alpha, want, fx, s.cancelled ? RING_CANCEL_TAU_S : RING_DONE_TAU_S);
        if (s.doneT > RING_REMOVE_S) s.id = null;
      }
      s.head = w(s.head + dt * (RING_HEAD_BASE + RING_HEAD_STEP * k) * rate * (1 - s.amb), TAU);
      const zc = slot.c + RING_WOBBLE_Z * Math.sin(scene * 0.23 + k * 1.7);
      const xp = slot.phi + RING_WOBBLE_X * Math.sin(scene * 0.31 + k);
      const cz = Math.cos(zc);
      const sz = Math.sin(zc);
      const cxr = Math.cos(xp);
      const sxr = Math.sin(xp);
      const o = k * 9;
      ringM[o] = cz;
      ringM[o + 1] = sz;
      ringM[o + 2] = 0;
      ringM[o + 3] = -sz * cxr;
      ringM[o + 4] = cz * cxr;
      ringM[o + 5] = sxr;
      ringM[o + 6] = sz * sxr;
      ringM[o + 7] = -cz * sxr;
      ringM[o + 8] = cxr;
      ringA[k * 4] = slot.r;
      ringA[k * 4 + 1] = s.prog;
      ringA[k * 4 + 2] = s.id === null ? 0 : s.alpha;
      ringA[k * 4 + 3] = s.amb;
      const hc = slot.r * Math.cos(s.head);
      const hs = slot.r * Math.sin(s.head);
      ringH[k * 4] = cz * hc + sz * sxr * hs;
      ringH[k * 4 + 1] = sz * hc - cz * sxr * hs;
      ringH[k * 4 + 2] = s.head;
      ringH[k * 4 + 3] = cxr * hs;
    }

    const breathS = Math.sin(breath);
    const bAmt = cur.breath * (1 - cur.hollow);
    const vm = cur.voiceGain;
    const flick = cur.spark * 0.07 * Math.sin(flowJs * 7.3) * Math.sin(flowJs * 3.7 + 1.3);
    energy = cur.energy * (1 + BREATH_ENERGY * bAmt * breathS) + VOICE_ENERGY * voice * vm + flick;
    rim = cur.rim + VOICE_RIM * voice * vm + HEART_RIM * heart * (1 - 0.5 * cur.hollow);
    orbR =
      R *
      cur.scale *
      (1 + BREATH_SIZE * bAmt * breathS) *
      (1 + VOICE_SIZE * voice * vm) *
      (1 + HEART_SIZE * heart * (1 - cur.hollow));
    writeSurface(mA, rot, SURFACE_TILT_A, 0);
    writeSurface(mB, rot2, SURFACE_TILT_B, SURFACE_ROLL_B);
    off4[0] = offA;
    off4[1] = offB;
    off4[2] = offC;
    off4[3] = offD;
    maxShaderInput = Math.max(Math.abs(offA), Math.abs(offB), Math.abs(offC), Math.abs(offD), Math.abs(offSpark[0] ?? 0), Math.abs(offSpark[1] ?? 0), Math.abs(offSpark[2] ?? 0), sweep, tight, voiceFlow);
  }

  function drawGl(g: GlState): void {
    const ctx = g.gl;
    const flipY = cssH - cy;
    g.timer?.begin();
    if (!transparent) {
      ctx.disable(ctx.BLEND);
      const b = g.bg;
      bindProgram(ctx, b, g.full);
      ctx.uniform2f(b.u.uC, cx, flipY);
      ctx.uniform1f(b.u.uR, orbR);
      ctx.uniform1f(b.u.uDpr, effDpr);
      ctx.uniform3fv(b.u.uBg, pal.bg);
      ctx.uniform3fv(b.u.uMid, outCol.mid);
      ctx.uniform3fv(b.u.uFlareCol, flareCol);
      ctx.uniform1f(b.u.uLoad, loadA);
      ctx.uniform1f(b.u.uEnergy, energy);
      ctx.uniform1f(b.u.uHeart, heart);
      ctx.uniform1f(b.u.uFlare, flareV);
      ctx.uniform1f(b.u.uNight, nightA);
      ctx.uniform1f(b.u.uDim, dim);
      ctx.uniform1f(b.u.uDither, scene * 7 - Math.floor(scene * 7));
      ctx.drawArrays(ctx.TRIANGLES, 0, 3);
    } else {
      ctx.clearColor(0, 0, 0, 0);
      ctx.clear(ctx.COLOR_BUFFER_BIT);
    }
    ctx.enable(ctx.BLEND);
    ctx.blendFunc(ctx.ONE, ctx.ONE);
    overlay(g, 0, flipY);
    ctx.blendFunc(ctx.ONE, ctx.ONE_MINUS_SRC_ALPHA);
    const o = g.orb;
    bindProgram(ctx, o, g.box);
    ctx.uniform2f(o.u.uRes, cssW, cssH);
    ctx.uniform2f(o.u.uC, cx, flipY);
    ctx.uniform1f(o.u.uR, orbR);
    ctx.uniform1f(o.u.uQuad, ORB_QUAD);
    ctx.uniform1f(o.u.uRp, orbR);
    ctx.uniformMatrix3fv(o.u.uSurf, false, mA);
    ctx.uniformMatrix3fv(o.u.uSurf2, false, mB);
    ctx.uniform4fv(o.u.uOff, off4);
    ctx.uniform3fv(o.u.uOffSpark, offSpark);
    ctx.uniform1f(o.u.uSweep, sweep);
    ctx.uniform1f(o.u.uSweepAmt, cur.sweep);
    ctx.uniform1f(o.u.uEnergy, energy);
    ctx.uniform1f(o.u.uRim, rim);
    ctx.uniform1f(o.u.uCore, cur.core);
    ctx.uniform1f(o.u.uDetail, cur.detail);
    ctx.uniform1f(o.u.uTight, cur.tight);
    ctx.uniform1f(o.u.uTightPhase, tight);
    ctx.uniform1f(o.u.uVoice, voice);
    ctx.uniform1f(o.u.uVoiceMix, cur.voiceGain);
    ctx.uniform1f(o.u.uVoiceFlow, voiceFlow);
    ctx.uniform1f(o.u.uSpark, cur.spark);
    ctx.uniform1f(o.u.uHollow, cur.hollow);
    ctx.uniform1f(o.u.uTick, tick);
    ctx.uniform3fv(o.u.uTickCol, tickCol);
    ctx.uniform1f(o.u.uHeart, heart * (1 - 0.5 * cur.hollow));
    ctx.uniform1f(o.u.uFlare, flareV);
    ctx.uniform1f(o.u.uFlareWhite, themeIsRed ? FLARE_WHITE_RED : FLARE_WHITE_OTHER);
    ctx.uniform1f(o.u.uDim, dim);
    ctx.uniform1f(o.u.uMuted, mutedA);
    ctx.uniform1f(o.u.uNight, nightA);
    ctx.uniform1f(o.u.uAlphaFromColor, transparent ? 1 : 0);
    ctx.uniform3fv(o.u.uDeep, outCol.deep);
    ctx.uniform3fv(o.u.uMid, outCol.mid);
    ctx.uniform3fv(o.u.uHot, outCol.hot);
    ctx.uniform3fv(o.u.uFlareCol, flareCol);
    ctx.drawArrays(ctx.TRIANGLE_STRIP, 0, 4);
    ctx.blendFunc(ctx.ONE, ctx.ONE);
    overlay(g, 1, flipY);
    g.timer?.end();
  }

  function overlay(g: GlState, pass: number, flipY: number): void {
    const ctx = g.gl;
    const v = g.ov;
    bindProgram(ctx, v, g.box);
    ctx.uniform2f(v.u.uRes, cssW, cssH);
    ctx.uniform2f(v.u.uC, cx, flipY);
    ctx.uniform1f(v.u.uR, R);
    ctx.uniform1f(v.u.uQuad, OVERLAY_QUAD);
    ctx.uniform1f(v.u.uRp, R);
    ctx.uniform1f(v.u.uPass, pass);
    ctx.uniform1f(v.u.uHollow, cur.hollow);
    ctx.uniform1f(v.u.uMuted, mutedA);
    ctx.uniform1f(v.u.uDim, dim);
    ctx.uniform1f(v.u.uNight, nightA);
    ctx.uniform1f(v.u.uAlphaFromColor, transparent ? 1 : 0);
    ctx.uniform3fv(v.u.uMid, outCol.mid);
    ctx.uniform3fv(v.u.uHot, outCol.hot);
    ctx.uniform3fv(v.u.uAmber, amber.mid);
    ctx.uniform3fv(v.u.uAmberHead, amber.head);
    ctx.uniformMatrix3fv(v.u.uRingM, false, ringM);
    ctx.uniform4fv(v.u.uRingA, ringA);
    ctx.uniform4fv(v.u.uRingH, ringH);
    ctx.drawArrays(ctx.TRIANGLE_STRIP, 0, 4);
  }

  function drawFb(): void {
    if (!fbCtx) return;
    fbView.w = cssW;
    fbView.h = cssH;
    fbView.cx = cx;
    fbView.cy = cy;
    fbView.r = orbR;
    fbView.baseR = R;
    fbView.energy = energy;
    fbView.rim = rim;
    fbView.core = cur.core;
    fbView.hollow = cur.hollow;
    fbView.sweepAmt = cur.sweep;
    fbView.sweepPhase = sweep;
    fbView.spark = cur.spark;
    fbView.rot = rot;
    fbView.flow = flowJs;
    fbView.tight = cur.tight;
    fbView.tightPhase = tight;
    fbView.tick = tick;
    fbView.heart = heart;
    fbView.flare = flareV;
    fbView.dim = dim;
    fbView.muted = mutedA;
    fbView.night = nightA;
    fbView.load = loadA;
    drawFallback(fbCtx, fbView);
  }

  function placeFollowers(): void {
    const chip = followers.chip;
    if (chip) {
      const y = cy - R - CHIP_ABOVE_PX;
      if (Math.abs(cx - chipX) > 0.25 || Math.abs(y - chipY) > 0.25) {
        chipX = cx;
        chipY = y;
        chip.style.transform = `translate(${cx.toFixed(1)}px,${y.toFixed(1)}px) translateX(-50%)`;
      }
    }
    const caption = followers.caption;
    if (caption) {
      const box = ribbonBox(cx, cy, R);
      const w = Math.round(CAPTION_W_R * R);
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
      const box = ribbonBox(cx, cy, R);
      const w = Math.round(Math.min(CAPTION_W_R * R, QUICK_W_MAX_PX));
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
  }

  function placeRibbon(): void {
    if (!ribbonCanvas) return;
    const box = ribbonBox(cx, cy, R);
    const rw = box.w;
    const rh = box.h;
    const x = box.x;
    const y = box.y;
    if (ribbonDirty || Math.abs(rw - ribW) > 1 || Math.abs(rh - ribH) > 1) {
      ribW = rw;
      ribH = rh;
      const d = Math.min(window.devicePixelRatio || 1, 2);
      ribbonCanvas.style.width = `${rw.toFixed(1)}px`;
      ribbonCanvas.style.height = `${rh.toFixed(1)}px`;
      ribbonCanvas.width = Math.max(1, Math.round(rw * d));
      ribbonCanvas.height = Math.max(1, Math.round(rh * d));
      ribDash[0] = 2 * d;
      ribDash[1] = 6 * d;
      ribSigW = -1;
      ribCleared = false;
      ribbonDirty = false;
    }
    if (Math.abs(x - ribX) > 0.25 || Math.abs(y - ribY) > 0.25) {
      ribX = x;
      ribY = y;
      ribbonCanvas.style.transform = `translate(${x.toFixed(1)}px,${y.toFixed(1)}px)`;
    }
  }

  function ribbonColour(c: Float32Array, a: number): string {
    return `rgba(${Math.round((c[0] ?? 0) * 255)},${Math.round((c[1] ?? 0) * 255)},${Math.round((c[2] ?? 0) * 255)},${a})`;
  }

  function drawRibbon(dt: number, nowMs: number): void {
    if (!ribCtx || !ribbonCanvas) return;
    const fresh = nowMs - voiceAt <= VOICE_STALE_S * 1000;
    const speaking = shown === 'speaking' && connected && !reduced;
    const want = speaking && (fresh || nightA > 0.5) ? 1 : 0;
    ribbonA = approach(ribbonA, want, dt, want ? RIBBON_IN_TAU_S : RIBBON_OUT_TAU_S);
    hAcc += dt;
    while (hAcc >= 1 / RIBBON_RATE_HZ) {
      hAcc -= 1 / RIBBON_RATE_HZ;
      hHead = (hHead + 1) % RIBBON_SAMPLES;
      hist[hHead] = night ? 0 : voice;
    }
    const bw = ribbonCanvas.width;
    const bh = ribbonCanvas.height;
    if (ribbonA < 0.005) {
      if (!ribCleared) {
        ribCtx.clearRect(0, 0, bw, bh);
        ribCleared = true;
      }
      return;
    }
    ribCleared = false;
    placeRibbon();
    const ctx = ribCtx;
    const k = bw / Math.max(1, ribW);
    const ccx = bw / 2;
    const ccy = bh / 2;
    const half = bw * 0.49;
    const amp = bh * 0.44;
    const sigMid =
      Math.round((pal.mid[0] ?? 0) * 255) * 65536 + Math.round((pal.mid[1] ?? 0) * 255) * 256 + Math.round((pal.mid[2] ?? 0) * 255);
    const sigHot =
      Math.round((pal.hot[0] ?? 0) * 255) * 65536 + Math.round((pal.hot[1] ?? 0) * 255) * 256 + Math.round((pal.hot[2] ?? 0) * 255);
    if (bw !== ribSigW || sigMid !== ribSigMid || sigHot !== ribSigHot || !ribFill || !ribStroke) {
      ribSigW = bw;
      ribSigMid = sigMid;
      ribSigHot = sigHot;
      ribFill = ctx.createLinearGradient(ccx - half, 0, ccx + half, 0);
      ribFill.addColorStop(0, ribbonColour(pal.mid, 0));
      ribFill.addColorStop(0.5, ribbonColour(pal.mid, 0.5));
      ribFill.addColorStop(1, ribbonColour(pal.mid, 0));
      ribStroke = ctx.createLinearGradient(ccx - half, 0, ccx + half, 0);
      ribStroke.addColorStop(0, ribbonColour(pal.hot, 0));
      ribStroke.addColorStop(0.5, ribbonColour(pal.hot, 0.95));
      ribStroke.addColorStop(1, ribbonColour(pal.hot, 0));
    }
    ctx.clearRect(0, 0, bw, bh);
    const a = ribbonA * (1 - 0.75 * dim);
    ctx.globalAlpha = a;
    if (nightA > 0.5) {
      ctx.setLineDash(ribDash);
      ctx.lineWidth = 1.5 * k;
      ctx.globalAlpha = 0.55 * a;
      ctx.strokeStyle = ribStroke;
      ctx.beginPath();
      ctx.moveTo(ccx - half * 0.7, ccy);
      ctx.lineTo(ccx + half * 0.7, ccy);
      ctx.stroke();
      ctx.setLineDash(ribSolid);
      ctx.globalAlpha = 1;
      return;
    }
    const n1 = RIBBON_SAMPLES - 1;
    ctx.beginPath();
    for (let s = -n1; s <= n1; s++) {
      const i = Math.abs(s);
      const v = hist[(hHead - i + RIBBON_SAMPLES) % RIBBON_SAMPLES] ?? 0;
      const fd = Math.pow(1 - i / n1, 0.7);
      const x = ccx + Math.sign(s) * (i / n1) * half;
      const y = ccy - (1.2 * k + v * amp) * fd;
      if (s === -n1) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    for (let s = n1; s >= -n1; s--) {
      const i = Math.abs(s);
      const v = hist[(hHead - i + RIBBON_SAMPLES) % RIBBON_SAMPLES] ?? 0;
      const fd = Math.pow(1 - i / n1, 0.7);
      ctx.lineTo(ccx + Math.sign(s) * (i / n1) * half, ccy + (1.2 * k + v * amp * 0.82) * fd);
    }
    ctx.closePath();
    ctx.fillStyle = ribFill;
    ctx.fill();
    ctx.strokeStyle = ribStroke;
    ctx.lineWidth = 1.2 * k;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  function render(): void {
    if (path === 'webgl' && gls && !gls.gl.isContextLost()) {
      drawGl(gls);
    } else if (path === 'fallback') {
      drawFb();
    }
    framesDrawn += 1;
  }

  function resolveMarks(nowMs: number): void {
    for (let i = 0; i < marks.length; i++) {
      const m = marks[i];
      if (!m || !m.live) continue;
      if (m.first < 0) {
        m.first = nowMs - m.t0;
        changeLog[changeHead] = m.first;
        changeHead = (changeHead + 1) % CHANGE_WINDOW;
        changeN = Math.min(CHANGE_WINDOW, changeN + 1);
        if (m.budget === 0) {
          options.onMark?.({ label: m.label, firstMs: m.first, settleMs: null, budgetMs: 0, dropped: false });
          m.live = false;
          continue;
        }
      }
      if (m.budget > 0 && m.settle < 0 && !m.dropped && trans >= SETTLE_FRACTION) {
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

  function targetFps(): number {
    let fps = 1000 / Math.max(refreshMs, 1);
    if (path === 'fallback') fps = Math.min(fps, FALLBACK_FPS);
    if (frameCap !== null) fps = Math.min(fps, frameCap);
    return fps;
  }

  function setScale(next: number): void {
    scale = Math.max(SCALE_MIN, Math.min(1, Math.round(next * 100) / 100));
    ivN = 0;
    govIvCount = 0;
    gpuN = 0;
    ensureSized();
  }

  function governor(nowMs: number): void {
    if (nowMs - lastGov < GOVERNOR_EVERY_MS) return;
    lastGov = nowMs;
    if (path !== 'webgl') return;
    const budget = 1000 / targetFps();
    if (budget !== lastBudget) {
      lastBudget = budget;
      govIvCount = 0;
      slowChecks = 0;
      emergencyChecks = 0;
      fastSince = 0;
      return;
    }
    if (nowMs - startedAt < WARMUP_MS) {
      govIvCount = 0;
      return;
    }
    let n = 0;
    let sum = 0;
    let dropped = 0;
    for (let i = 0; i < govIvCount; i++) {
      const v = ivRing[(govIvHead - 1 - i + INTERVAL_WINDOW * 4) % INTERVAL_WINDOW] ?? 0;
      if (v > STALL_MS) continue;
      n += 1;
      sum += v;
      if (v > budget * 1.5) dropped += 1;
    }
    govIvCount = 0;
    if (n < 10) return;
    const avg = sum / n;
    const dropFrac = dropped / n;
    const timer = gls?.timer !== null && gls?.timer !== undefined && gpuN >= 8;
    const gpu90 = timer ? percentileOf(gpuRing, gpuN, scratch, 0.9) : 0;
    emergencyChecks = avg > budget * 3 ? emergencyChecks + 1 : 0;
    if (emergencyChecks >= 2 && scale > SCALE_MIN) {
      setScale(SCALE_MIN);
      emergencyChecks = 0;
      slowChecks = 0;
      fastSince = 0;
      return;
    }
    const slowGpu = timer && gpu90 > GPU_HIGH * budget;
    const dropping = dropFrac > DROP_LIMIT;
    if (slowGpu || dropping) slowChecks += 1;
    else slowChecks = 0;
    if (slowChecks >= (slowGpu ? 1 : 2) && scale > SCALE_MIN) {
      setScale(scale - SCALE_STEP);
      slowChecks = 0;
      fastSince = 0;
      return;
    }
    const roomy = (timer ? gpu90 < GPU_LOW * budget : true) && dropped === 0;
    if (scale < 1 && roomy) {
      if (!fastSince) fastSince = nowMs;
      else if (nowMs - fastSince > RAISE_AFTER_MS) {
        setScale(scale + SCALE_STEP);
        fastSince = 0;
      }
    } else {
      fastSince = 0;
    }
  }

  function frameDivider(): number {
    return Math.max(1, Math.min(20, Math.round(1000 / targetFps() / Math.max(refreshMs, 1))));
  }

  function frame(ts: number): void {
    rafId = 0;
    if (disposed || !animating) return;
    if (document.hidden) {
      lastRafAt = 0;
      lastDrawAt = 0;
      return;
    }
    rafId = requestAnimationFrame(frame);
    if (lastRafAt > 0) {
      rafHead = pushRing(rafRing, ts - lastRafAt, rafHead);
      rafN = Math.min(RAF_WINDOW, rafN + 1);
      if (rafN === RAF_WINDOW && rafHead === 0) {
        const med = percentileOf(rafRing, rafN, scratch, 0.5);
        if (med > 1) refreshMs = med;
      }
    }
    lastRafAt = ts;
    if (++tickCounter < frameDivider()) return;
    tickCounter = 0;
    const dtMs = lastDrawAt > 0 ? ts - lastDrawAt : 1000 / targetFps();
    if (lastDrawAt > 0) {
      const bin = Math.min(HIST_BINS - 1, Math.round(dtMs));
      ivHist[bin] = (ivHist[bin] ?? 0) + 1;
    }
    if (lastDrawAt > 0 && dtMs < 1500) {
      ivHead = pushRing(ivRing, dtMs, ivHead);
      ivN = Math.min(INTERVAL_WINDOW, ivN + 1);
      govIvHead = ivHead;
      govIvCount = Math.min(INTERVAL_WINDOW, govIvCount + 1);
    }
    lastDrawAt = ts;
    drawOnce(Math.min(dtMs, 100) / 1000);
    if (freezeAt > 0 && ((trans >= FREEZE_TRANS && tick <= FREEZE_TICK) || ts - freezeAt >= FREEZE_MAX_MS)) stopAnimating();
  }

  function stopAnimating(): void {
    freezeAt = 0;
    animating = false;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
  }

  function drawOnce(dt: number): void {
    const t0 = performance.now();
    ensureSized();
    step(dt, performance.now());
    render();
    drawRibbon(dt, performance.now());
    const after = performance.now();
    if (renderedPending) {
      renderedPending = false;
      options.onStateRendered?.(shown, after);
    }
    resolveMarks(after);
    gls?.timer?.collect(sinkGpu);
    costHead = pushRing(costRing, performance.now() - t0, costHead);
    costN = Math.min(COST_WINDOW, costN + 1);
    governor(after);
  }

  function sinkGpu(ms: number): void {
    gpuHead = pushRing(gpuRing, ms, gpuHead);
    gpuN = Math.min(GPU_WINDOW, gpuN + 1);
  }

  let reducedQueued = 0;
  let lastReducedAt = 0;

  function requestDraw(): void {
    if (disposed) return;
    if (!reduced) {
      if (animating && !rafId && !document.hidden) rafId = requestAnimationFrame(frame);
      return;
    }
    if (reducedQueued) return;
    reducedQueued = requestAnimationFrame(() => {
      reducedQueued = 0;
      if (disposed || document.hidden) return;
      const now = performance.now();
      const dt = lastReducedAt > 0 ? Math.min(1, (now - lastReducedAt) / 1000) : 0;
      lastReducedAt = now;
      drawOnce(dt);
    });
  }

  function reducedLater(ms: number): void {
    if (reduced) window.setTimeout(requestDraw, ms);
  }

  function onVisibility(): void {
    if (document.hidden) {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = 0;
      lastRafAt = 0;
      lastDrawAt = 0;
      return;
    }
    requestDraw();
  }

  let resizeTimer = 0;
  const observer = new ResizeObserver(() => {
    if (resizeTimer) return;
    resizeTimer = window.setTimeout(() => {
      resizeTimer = 0;
      if (disposed) return;
      ensureSized();
      requestDraw();
    }, 100);
  });

  canvas.addEventListener('webglcontextlost', onLost, false);
  canvas.addEventListener('webglcontextrestored', onRestored, false);
  document.addEventListener('visibilitychange', onVisibility);
  observer.observe(canvas);
  startGl();
  ensureSized();
  requestDraw();

  return {
    setPlacement(x, y, r) {
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(r) || r <= 0) return;
      pcxT = x;
      pcyT = y;
      prT = r;
      if (!placeSet) {
        placeSet = true;
        pcx = x;
        pcy = y;
        pr = r;
      }
      requestDraw();
    },
    setFollowers(f) {
      followers = f;
      chipX = -1e9;
      chipY = -1e9;
      capX = -1e9;
      capY = -1e9;
      capW = -1;
      quickX = -1e9;
      quickY = -1e9;
      quickW = -1;
      requestDraw();
    },
    setCentreOffset(xPx, yPx) {
      offXT = Number.isFinite(xPx) ? xPx : 0;
      offYT = Number.isFinite(yPx) ? yPx : 0;
      requestDraw();
    },
    setFit(factor) {
      fitT = Number.isFinite(factor) ? Math.min(1.2, Math.max(0.2, factor)) : 1;
      requestDraw();
    },
    retint() {
      readPalette(palT);
      for (let i = 0; i < 3; i++) {
        pal.deep[i] = (pal.deep[i] ?? 0) + ((palT.deep[i] ?? 0) - (pal.deep[i] ?? 0)) * THEME_JUMP;
        pal.mid[i] = (pal.mid[i] ?? 0) + ((palT.mid[i] ?? 0) - (pal.mid[i] ?? 0)) * THEME_JUMP;
        pal.hot[i] = (pal.hot[i] ?? 0) + ((palT.hot[i] ?? 0) - (pal.hot[i] ?? 0)) * THEME_JUMP;
        pal.bg[i] = (pal.bg[i] ?? 0) + ((palT.bg[i] ?? 0) - (pal.bg[i] ?? 0)) * THEME_JUMP;
      }
      if (!reduced) {
        tick = Math.max(tick, THEME_TICK);
        tickCol.set(palT.hot);
      }
      requestDraw();
    },
    beat() {
      if (!connected) return;
      heartStart = performance.now() / 1000;
      requestDraw();
      reducedLater(320);
    },
    setLoad(c, m) {
      cpuIn = Number.isFinite(c) ? Math.max(0, Math.min(1, c)) : 0;
      memIn = Number.isFinite(m) ? Math.max(0, Math.min(1, m)) : 0;
      if (reduced) requestDraw();
    },
    setConnected(on) {
      if (on === connected) return;
      connected = on;
      if (!reduced) dim = on ? dim * 0.7 : dim + (1 - dim) * CHANGE_JUMP;
      if (!on) heart = 0;
      requestDraw();
    },
    setJobs(jobs) {
      for (const s of slots) s.seen = false;
      for (const j of jobs) {
        let s: Slot | undefined;
        for (const x of slots) if (x.id === j.id) s = x;
        if (!s) {
          if (j.finished !== 'none') continue;
          for (const x of slots) if (x.id === null && !s) s = x;
          if (!s) continue;
          s.id = j.id;
          s.prog = 0;
          s.alpha = 0.3;
          s.amb = 0;
          s.done = false;
          s.doneT = 0;
          s.cancelled = false;
        }
        s.seen = true;
        s.progT = Number.isFinite(j.progress) ? Math.max(0, Math.min(1, j.progress)) : 0;
        s.waiting = j.waiting && j.finished === 'none';
        if (j.finished === 'ok' && !s.done) {
          s.done = true;
          s.doneT = 0;
          s.progT = 1;
        } else if (j.finished === 'cancelled' && !s.done) {
          s.done = true;
          s.cancelled = true;
          s.doneT = 0;
        }
      }
      for (const s of slots) {
        if (s.id !== null && !s.seen && !s.done) {
          s.done = true;
          s.cancelled = true;
          s.doneT = 0;
        }
      }
      requestDraw();
      reducedLater(RING_REMOVE_S * 1000 + 50);
    },
    setVoiceLevel(level) {
      voiceIn = Number.isFinite(level) ? Math.max(0, Math.min(1, level)) : 0;
      voiceAt = performance.now();
    },
    setMuted(on) {
      muted = on;
      if (!reduced) mutedA = on ? mutedA + (1 - mutedA) * CHANGE_JUMP : mutedA * 0.7;
      requestDraw();
    },
    setNight(on) {
      night = on;
      if (!reduced) nightA = on ? nightA + (1 - nightA) * CHANGE_JUMP : nightA * 0.7;
      requestDraw();
    },
    flare() {
      flareStart = performance.now() / 1000;
      requestDraw();
      reducedLater(FLARE_WINDOW_S * 1000 + 50);
    },
    noteStateChange(at, tauS) {
      const slot = marks.find((mk) => !mk.live);
      if (slot) {
        slot.live = true;
        slot.label = 'State';
        slot.t0 = at;
        slot.budget = 0;
        slot.first = -1;
        slot.settle = -1;
        slot.dropped = false;
      }
      pendingAt = at;
      pendingTau = typeof tauS === 'number' && tauS > 0 ? tauS : -1;
      requestDraw();
      reducedLater(HEART_WINDOW_S * 1000);
    },
    mark(label, t0, budgetMs) {
      let slot = marks.find((m) => !m.live);
      if (!slot) {
        slot = marks[0];
        if (!slot) return;
      }
      slot.live = true;
      slot.label = label;
      slot.t0 = t0;
      slot.budget = budgetMs;
      slot.first = -1;
      slot.settle = -1;
      slot.dropped = false;
      requestDraw();
    },
    setFrameCap(fps) {
      frameCap = fps !== null && Number.isFinite(fps) && fps > 0 ? fps : null;
      ivN = 0;
      govIvCount = 0;
      slowChecks = 0;
      fastSince = 0;
    },
    reprobeRefresh() {
      refreshMs = 1000 / 60;
      rafN = 0;
      rafHead = 0;
      lastRafAt = 0;
      tickCounter = 0;
      ivN = 0;
      govIvCount = 0;
      appliedW = -1;
      ensureSized();
      requestDraw();
    },
    setAnimating(on) {
      if (disposed) return;
      if (on) {
        freezeAt = 0;
        if (animating) return;
        animating = true;
        lastRafAt = 0;
        lastDrawAt = 0;
        requestDraw();
        return;
      }
      if (!animating || freezeAt > 0) return;
      if (reduced) {
        stopAnimating();
        requestDraw();
        return;
      }
      freezeAt = performance.now();
      requestDraw();
    },
    loseContextForTest(restoreAfterMs) {
      const ctx = gls?.gl ?? gl;
      const ext = ctx?.getExtension('WEBGL_lose_context');
      if (!ext) return false;
      ext.loseContext();
      if (restoreAfterMs >= 0) window.setTimeout(() => ext.restoreContext(), restoreAfterMs);
      return true;
    },
    stats() {
      const n = ivN;
      let sum = 0;
      let dropped = 0;
      const budget = 1000 / targetFps();
      for (let i = 0; i < n; i++) {
        const v = ivRing[i] ?? 0;
        sum += v;
        if (v > budget * 1.5) dropped += 1;
      }
      return {
        path,
        reason,
        renderer,
        rendererShort: shortRenderer(renderer),
        timerQuery: Boolean(gls?.timer),
        fps: n > 0 ? 1000 / (sum / n) : 0,
        intervalP50: percentileOf(ivRing, n, scratch, 0.5),
        intervalP95: percentileOf(ivRing, n, scratch, 0.95),
        dropped,
        samples: n,
        scale,
        gpuP50: percentileOf(gpuRing, gpuN, scratch, 0.5),
        gpuP90: percentileOf(gpuRing, gpuN, scratch, 0.9),
        costP50: percentileOf(costRing, costN, scratch, 0.5),
        costP95: percentileOf(costRing, costN, scratch, 0.95),
        framesDrawn,
        targetFps: targetFps(),
        refreshMs,
        focused: document.hasFocus(),
        changeP95: percentileOf(changeLog, changeN, scratch, 0.95),
        changeCount: changeN,
        lastSettle,
        canvas: { cssW, cssH, bufW: canvas.width, bufH: canvas.height },
        radius: R,
        centre: { x: cx, y: cy },
        maxShaderInput,
        load: loadA,
        cpu,
        mem,
      };
    },
    takeIntervals() {
      const out = Array.from(ivHist);
      ivHist.fill(0);
      return out;
    },
    dispose() {
      disposed = true;
      if (rafId) cancelAnimationFrame(rafId);
      if (reducedQueued) cancelAnimationFrame(reducedQueued);
      window.clearTimeout(restoreTimer);
      window.clearTimeout(resizeTimer);
      observer.disconnect();
      canvas.removeEventListener('webglcontextlost', onLost);
      canvas.removeEventListener('webglcontextrestored', onRestored);
      document.removeEventListener('visibilitychange', onVisibility);
      gls?.timer?.dispose();
      fbCanvas?.remove();
      gls?.gl.getExtension('WEBGL_lose_context')?.loseContext();
    },
  };
}
