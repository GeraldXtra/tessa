export interface Prog {
  p: WebGLProgram;
  u: Record<string, WebGLUniformLocation | null>;
}

export const SOFTWARE_RENDERER = /swiftshader|llvmpipe|software|basic render/i;

function stage(gl: WebGL2RenderingContext, type: number, src: string, name: string): WebGLShader {
  const s = gl.createShader(type);
  if (!s) throw new Error(`${name}: createShader failed`);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS) && !gl.isContextLost()) {
    const log = gl.getShaderInfoLog(s) ?? 'no log';
    gl.deleteShader(s);
    throw new Error(`${name}: ${log.slice(0, 400)}`);
  }
  return s;
}

export function program(gl: WebGL2RenderingContext, name: string, vs: string, fs: string, uniforms: readonly string[]): Prog {
  const p = gl.createProgram();
  if (!p) throw new Error(`${name}: createProgram failed`);
  const v = stage(gl, gl.VERTEX_SHADER, vs, `${name}.vert`);
  const f = stage(gl, gl.FRAGMENT_SHADER, fs, `${name}.frag`);
  gl.attachShader(p, v);
  gl.attachShader(p, f);
  gl.linkProgram(p);
  gl.deleteShader(v);
  gl.deleteShader(f);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS) && !gl.isContextLost()) {
    throw new Error(`${name}: link failed: ${(gl.getProgramInfoLog(p) ?? '').slice(0, 400)}`);
  }
  const u: Record<string, WebGLUniformLocation | null> = {};
  for (const n of uniforms) u[n] = gl.getUniformLocation(p, n);
  return { p, u };
}

export interface Fmt {
  internal: number;
  format: number;
  type: number;
  name: string;
  gain: number;
}

function renderable(gl: WebGL2RenderingContext, f: Fmt): boolean {
  const tex = gl.createTexture();
  const fb = gl.createFramebuffer();
  if (!tex || !fb) return false;
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texImage2D(gl.TEXTURE_2D, 0, f.internal, 4, 4, 0, f.format, f.type, null);
  gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
  const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE && gl.getError() === gl.NO_ERROR;
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.bindTexture(gl.TEXTURE_2D, null);
  gl.deleteFramebuffer(fb);
  gl.deleteTexture(tex);
  return ok;
}

export function pickFormat(gl: WebGL2RenderingContext): Fmt {
  const half: Fmt = { internal: gl.RGBA16F, format: gl.RGBA, type: gl.HALF_FLOAT, name: 'RGBA16F', gain: 1 };
  if ((gl.getExtension('EXT_color_buffer_float') || gl.getExtension('EXT_color_buffer_half_float')) && renderable(gl, half)) return half;
  return { internal: gl.RGBA8, format: gl.RGBA, type: gl.UNSIGNED_BYTE, name: 'RGBA8', gain: 0.25 };
}

export interface Target {
  tex: WebGLTexture;
  fb: WebGLFramebuffer;
  w: number;
  h: number;
}

export function target(gl: WebGL2RenderingContext, w: number, h: number, f: Fmt, prev: Target | null): Target {
  const ww = Math.max(1, Math.round(w));
  const hh = Math.max(1, Math.round(h));
  if (prev && prev.w === ww && prev.h === hh) return prev;
  const tex = prev?.tex ?? gl.createTexture();
  const fb = prev?.fb ?? gl.createFramebuffer();
  if (!tex || !fb) throw new Error('render target allocation failed');
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texImage2D(gl.TEXTURE_2D, 0, f.internal, ww, hh, 0, f.format, f.type, null);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.bindTexture(gl.TEXTURE_2D, null);
  return { tex, fb, w: ww, h: hh };
}

export function dropTarget(gl: WebGL2RenderingContext, t: Target | null): void {
  if (!t) return;
  gl.deleteTexture(t.tex);
  gl.deleteFramebuffer(t.fb);
}

export function rendererString(gl: WebGL2RenderingContext): string {
  try {
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return String((ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)) ?? '');
  } catch {
    return '';
  }
}

interface TimerQueryExt {
  TIME_ELAPSED_EXT: number;
  GPU_DISJOINT_EXT: number;
}

export class GpuClock {
  private readonly free: WebGLQuery[] = [];
  private readonly waiting: WebGLQuery[] = [];
  private open: WebGLQuery | null = null;

  constructor(private readonly gl: WebGL2RenderingContext, private readonly ext: TimerQueryExt) {}

  static create(gl: WebGL2RenderingContext): GpuClock | null {
    const ext = gl.getExtension('EXT_disjoint_timer_query_webgl2') as TimerQueryExt | null;
    return ext ? new GpuClock(gl, ext) : null;
  }

  begin(): void {
    if (this.open || this.waiting.length >= 6) return;
    const q = this.free.pop() ?? this.gl.createQuery();
    if (!q) return;
    this.gl.beginQuery(this.ext.TIME_ELAPSED_EXT, q);
    this.open = q;
  }

  end(): void {
    if (!this.open) return;
    this.gl.endQuery(this.ext.TIME_ELAPSED_EXT);
    this.waiting.push(this.open);
    this.open = null;
  }

  collect(sink: (ms: number) => void): void {
    const gl = this.gl;
    const disjoint = Boolean(gl.getParameter(this.ext.GPU_DISJOINT_EXT));
    while (this.waiting.length > 0) {
      const q = this.waiting[0] as WebGLQuery;
      if (!gl.getQueryParameter(q, gl.QUERY_RESULT_AVAILABLE)) break;
      const ns = gl.getQueryParameter(q, gl.QUERY_RESULT) as number;
      this.waiting.shift();
      this.free.push(q);
      if (!disjoint && Number.isFinite(ns) && ns > 0) sink(ns / 1e6);
    }
  }

  dispose(): void {
    for (const q of [...this.free, ...this.waiting]) this.gl.deleteQuery(q);
    if (this.open) this.gl.deleteQuery(this.open);
    this.free.length = 0;
    this.waiting.length = 0;
    this.open = null;
  }
}
