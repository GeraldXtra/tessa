import bgFrag from './shaders/bg.frag.glsl?raw';
import fullVert from './shaders/full.vert.glsl?raw';
import noiseChunk from './shaders/noise.glsl?raw';
import orbFrag from './shaders/orb.frag.glsl?raw';
import overlayFrag from './shaders/overlay.frag.glsl?raw';
import quadVert from './shaders/quad.vert.glsl?raw';

const ORB_UNIFORMS = [
  'uRes', 'uC', 'uR', 'uQuad', 'uRp', 'uSurf', 'uSurf2', 'uOff', 'uOffSpark', 'uSweep', 'uSweepAmt',
  'uEnergy', 'uRim', 'uCore', 'uDetail', 'uTight', 'uTightPhase', 'uVoice', 'uVoiceMix', 'uVoiceFlow',
  'uSpark', 'uHollow', 'uTick', 'uHeart', 'uFlare', 'uFlareWhite', 'uDim', 'uMuted', 'uNight',
  'uAlphaFromColor', 'uTickCol', 'uDeep', 'uMid', 'uHot', 'uFlareCol',
] as const;
const BG_UNIFORMS = [
  'uC', 'uR', 'uDpr', 'uBg', 'uMid', 'uFlareCol', 'uLoad', 'uEnergy', 'uHeart', 'uFlare', 'uNight',
  'uDim', 'uDither',
] as const;
const OV_UNIFORMS = [
  'uRes', 'uC', 'uR', 'uQuad', 'uRp', 'uPass', 'uHollow', 'uMuted', 'uDim', 'uNight',
  'uAlphaFromColor', 'uMid', 'uHot', 'uAmber', 'uAmberHead', 'uRingM', 'uRingA', 'uRingH',
] as const;

type Uniforms<K extends string> = Record<K, WebGLUniformLocation | null>;

export interface Program<K extends string> {
  program: WebGLProgram;
  u: Uniforms<K>;
  aPos: number;
}

interface TimerExt {
  TIME_ELAPSED_EXT: number;
  GPU_DISJOINT_EXT: number;
  QUERY_RESULT_EXT: number;
  QUERY_RESULT_AVAILABLE_EXT: number;
  createQueryEXT(): WebGLQuery | null;
  deleteQueryEXT(query: WebGLQuery): void;
  beginQueryEXT(target: number, query: WebGLQuery): void;
  endQueryEXT(target: number): void;
  getQueryObjectEXT(query: WebGLQuery, pname: number): number | boolean;
}

const TIMER_SLOTS = 4;

export class GpuTimer {
  private readonly queries: (WebGLQuery | null)[] = [];
  private readonly pending: boolean[] = [];
  private next = 0;
  private open = -1;

  constructor(
    private readonly gl: WebGLRenderingContext,
    private readonly ext: TimerExt,
  ) {
    for (let i = 0; i < TIMER_SLOTS; i++) {
      this.queries.push(ext.createQueryEXT());
      this.pending.push(false);
    }
  }

  begin(): void {
    const slot = this.next;
    const q = this.queries[slot];
    if (!q || this.pending[slot]) {
      this.open = -1;
      return;
    }
    this.ext.beginQueryEXT(this.ext.TIME_ELAPSED_EXT, q);
    this.open = slot;
  }

  end(): void {
    if (this.open < 0) return;
    this.ext.endQueryEXT(this.ext.TIME_ELAPSED_EXT);
    this.pending[this.open] = true;
    this.next = (this.open + 1) % TIMER_SLOTS;
    this.open = -1;
  }

  collect(sink: (ms: number) => void): void {
    const disjoint = Boolean(this.gl.getParameter(this.ext.GPU_DISJOINT_EXT));
    for (let i = 0; i < TIMER_SLOTS; i++) {
      const q = this.queries[i];
      if (!q || !this.pending[i]) continue;
      if (!this.ext.getQueryObjectEXT(q, this.ext.QUERY_RESULT_AVAILABLE_EXT)) continue;
      const ns = Number(this.ext.getQueryObjectEXT(q, this.ext.QUERY_RESULT_EXT));
      this.pending[i] = false;
      if (!disjoint && Number.isFinite(ns)) sink(ns / 1e6);
    }
  }

  dispose(): void {
    for (const q of this.queries) if (q) this.ext.deleteQueryEXT(q);
  }
}

export interface GlState {
  gl: WebGLRenderingContext;
  bg: Program<(typeof BG_UNIFORMS)[number]>;
  orb: Program<(typeof ORB_UNIFORMS)[number]>;
  ov: Program<(typeof OV_UNIFORMS)[number]>;
  full: WebGLBuffer;
  box: WebGLBuffer;
  timer: GpuTimer | null;
  renderer: string;
  software: boolean;
}

const SOFTWARE = /swiftshader|llvmpipe|software|basic render/i;

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader {
  const sh = gl.createShader(type);
  if (!sh) throw new Error('createShader returned null');
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS) && !gl.isContextLost()) {
    const log = gl.getShaderInfoLog(sh) ?? '';
    gl.deleteShader(sh);
    throw new Error(`shader compile failed: ${log}`);
  }
  return sh;
}

function link<K extends string>(
  gl: WebGLRenderingContext,
  vs: string,
  fs: string,
  precision: string,
  names: readonly K[],
): Program<K> {
  const program = gl.createProgram();
  if (!program) throw new Error('createProgram returned null');
  gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, vs));
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, `precision ${precision} float;\n${fs}`));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS) && !gl.isContextLost()) {
    throw new Error(`program link failed: ${gl.getProgramInfoLog(program) ?? ''}`);
  }
  const u = {} as Uniforms<K>;
  for (const n of names) u[n] = gl.getUniformLocation(program, n);
  return { program, u, aPos: gl.getAttribLocation(program, 'aPos') };
}

function buffer(gl: WebGLRenderingContext, data: number[]): WebGLBuffer {
  const b = gl.createBuffer();
  if (!b) throw new Error('createBuffer returned null');
  gl.bindBuffer(gl.ARRAY_BUFFER, b);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
  return b;
}

export function getContext(canvas: HTMLCanvasElement, transparent: boolean): WebGLRenderingContext | null {
  return canvas.getContext('webgl', {
    alpha: transparent,
    premultipliedAlpha: true,
    antialias: false,
    depth: false,
    stencil: false,
    preserveDrawingBuffer: false,
    powerPreference: 'default',
    failIfMajorPerformanceCaveat: true,
  });
}

export function rendererName(gl: WebGLRenderingContext): string {
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  const raw = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  return String(raw ?? 'unknown');
}

export function buildGl(gl: WebGLRenderingContext): GlState {
  const hp = gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, gl.HIGH_FLOAT);
  const precision = hp && hp.precision > 0 ? 'highp' : 'mediump';
  const renderer = rendererName(gl);
  const timerExt = gl.getExtension('EXT_disjoint_timer_query') as unknown as TimerExt | null;
  return {
    gl,
    bg: link(gl, fullVert, bgFrag, precision, BG_UNIFORMS),
    orb: link(gl, quadVert, `${noiseChunk}\n${orbFrag}`, precision, ORB_UNIFORMS),
    ov: link(gl, quadVert, overlayFrag, precision, OV_UNIFORMS),
    full: buffer(gl, [-1, -1, 3, -1, -1, 3]),
    box: buffer(gl, [-1, -1, 1, -1, -1, 1, 1, 1]),
    timer: timerExt ? new GpuTimer(gl, timerExt) : null,
    renderer,
    software: SOFTWARE.test(renderer),
  };
}

export function bindProgram<K extends string>(gl: WebGLRenderingContext, p: Program<K>, buf: WebGLBuffer): void {
  gl.useProgram(p.program);
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.enableVertexAttribArray(p.aPos);
  gl.vertexAttribPointer(p.aPos, 2, gl.FLOAT, false, 0, 0);
}

export function shortRenderer(full: string): string {
  const m = /^ANGLE \((.*)\)$/.exec(full);
  let s = m?.[1] ?? full;
  s = s
    .replace(/\s*\(0x[0-9a-f]+\)/gi, '')
    .replace(/\s*Direct3D\d+/gi, '')
    .replace(/\s*vs_\d_\d ps_\d_\d/gi, '')
    .replace(/,\s*D3D11(-[\d.]+)?$/i, '')
    .replace(/^(Intel|NVIDIA|AMD|Google Inc\.[^,]*), /i, '');
  return s.trim();
}
