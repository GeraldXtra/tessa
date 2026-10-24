import { Terminal, type IDecoration, type IDisposable, type ILink, type IMarker } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import type { PtyFromHost, PtyToHost } from '../../shared/pty-ipc.ts'
import { abandon, claimPort } from '../panes/port-broker.ts'
import { probeGpu, type ProbeResult } from './gpu-probe.ts'
import { CURRENT_BG, CURRENT_FG, INPUT_FG, MATCH_BG, TERMINAL_FONT, cellLineHeight, sgrTrue, terminalTheme } from './theme.ts'

export type BlockState = 'prompt' | 'running' | 'done'
export type TabStatus = 'waiting' | 'starting' | 'live' | 'ended' | 'failed'

export interface Block {
  id: number
  promptMarker: IMarker
  cmdMarker: IMarker
  cmdX: number
  inputRows: number
  command: string
  typed: string | null
  started: number | null
  ended: number | null
  exit: number | undefined
  hasExit: boolean
  stopped: boolean
  state: BlockState
  inputDeco: IDecoration | undefined
}

export interface LastView {
  state: 'ok' | 'fail' | 'stop' | 'run'
  exit: number | undefined
  hasExit: boolean
  ms: number
  started: number
  command: string
}

export interface StickyView {
  blockId: number
  text: string
  state: 'ok' | 'fail' | 'stop' | 'run'
  exit: number | undefined
  started: number
}

export interface FindView {
  query: string
  total: number
  at: number
  capped: boolean
}

export interface SessionSettings {
  fontSize: number
  scrollback: number
  outputColours: 'standard' | 'mono'
  cursorStyle: 'block' | 'bar' | 'underline'
  pasteGuard: boolean
  rightClickPastes: boolean
  copyOnSelect: boolean
}

export interface SessionHost {
  changed: (id: string) => void
  note: (label: string, text: string, kind?: 'wait' | 'threat') => void
  focusMe: (id: string) => void
  blockMenu: (id: string, blockId: number, x: number, y: number) => void
  cwdChanged: (id: string, cwd: string) => void
  commandEnded: (id: string, b: Block) => void
  isShown: (id: string) => boolean
  isFocused: (id: string) => boolean
  settings: () => SessionSettings
  shellLabel: (shellId: string) => string
  pasteAsk: (id: string, text: string, lines: number) => void
  openLink: (req: { kind: 'url' | 'path'; target: string; base?: string; line?: number; col?: number }) => void
  log: (msg: string) => void
}

const HOST_TOP = 18
const SETTLE_MS = 40
const MAX_MATCHES = 5000
const LINK_RE =
  /"[A-Za-z]:\\[^"\r\n]+"|'[A-Za-z]:\\[^'\r\n]+'|https?:\/\/[^\s'"<>)\]]+|[A-Za-z]:\\[^\s'"<>|*?]+|(?:[\w.-]+[\\/])+[\w.-]+\.[A-Za-z][A-Za-z0-9]{0,5}(?::\d+(?::\d+)?|\(\d+(?:,\d+)?\))?/g

let blockSeq = 0
const utf8 = new TextDecoder('utf-8', { fatal: false })

function stripEsc(s: string): string {
  return s.replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '').replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '').replace(/\x1b./g, '')
}

export function parseOsc7(data: string, raw = false): string | null {
  let s = data.trim()
  if (!s.toLowerCase().startsWith('file://')) return null
  s = s.slice(7)
  const slash = s.indexOf('/')
  if (slash < 0) return null
  let p = s.slice(slash + 1)
  if (!raw) {
    try {
      p = decodeURIComponent(p)
    } catch {
      p = p.replace(/%([0-9a-fA-F]{2})/g, (_m, h: string) => String.fromCharCode(parseInt(h, 16)))
    }
  }
  p = p.replace(/\//g, '\\')
  const msys = p.match(/^\\?([a-zA-Z])(\\.*)?$/)
  if (msys && !/^[a-zA-Z]:/.test(p) && p.startsWith('\\')) p = `${msys[1]!.toUpperCase()}:${msys[2] ?? '\\'}`
  if (/^\\[a-zA-Z]:/.test(p)) p = p.slice(1)
  if (!/^[a-zA-Z]:\\/.test(p) && !/^[a-zA-Z]:$/.test(p)) return null
  if (/^[a-zA-Z]:$/.test(p)) p += '\\'
  if (/[\u0000-\u001f]/.test(p) || p.length > 1024) return null
  return p.length > 3 ? p.replace(/\\+$/, '') : p
}

export function fmtDuration(ms: number): string {
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} S`
  const s = Math.floor(ms / 1000)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export function mmss(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export function hhmm(t: number): string {
  const d = new Date(t)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export function blockVisualState(b: Block): 'ok' | 'fail' | 'stop' | 'run' | 'prompt' {
  if (b.state === 'prompt') return 'prompt'
  if (b.state === 'running') return 'run'
  if (b.stopped) return 'stop'
  if (b.hasExit && b.exit !== 0) return 'fail'
  return 'ok'
}

export class TermSession {
  readonly id: string
  readonly shellId: string
  cwd: string
  name = ''
  status: TabStatus = 'starting'
  statusText = ''
  sessionId = ''
  integrated = false
  unseen = false
  newOutput = false
  blocks: Block[] = []
  selected: number | null = null
  sticky: StickyView | null = null
  find: FindView = { query: '', total: 0, at: 0, capped: false }
  findOpen = false
  createdAt = Date.now()
  probe: ProbeResult | null = null

  private host: SessionHost
  private banner: string
  private container: HTMLDivElement | null = null
  private hostEl: HTMLDivElement | null = null
  private underEl: HTMLDivElement | null = null
  private gutterEl: HTMLDivElement | null = null
  private metaEl: HTMLDivElement | null = null
  private term: Terminal | null = null
  private fit: FitAddon | null = null
  private port: MessagePort | null = null
  private disposables: IDisposable[] = []
  private ro: ResizeObserver | null = null
  private lastDims = ''
  private settling = false
  private firstPromptSeen = false
  private settleTimer: number | null = null
  private record = ''
  private tainted = false
  private missedEnter = false
  private pasting = false
  private overlayQueued = false
  private overlaySig = ''
  private matches: { line: number; col: number; len: number }[] = []
  private matchDecos: IDecoration[] = []
  private decoWindow = ''
  private disposed = false
  private starting = false
  private echoWaiters: { ch: string; resolve: (at: number) => void }[] = []
  private probeWaiters = new Map<number, (ms: number) => void>()
  private probeSeq = 0

  constructor(opts: { id: string; shellId: string; cwd: string; name?: string; banner?: string; host: SessionHost }) {
    this.id = opts.id
    this.shellId = opts.shellId
    this.cwd = opts.cwd
    this.name = opts.name ?? ''
    this.banner = opts.banner ?? ''
    this.host = opts.host
  }

  get terminal(): Terminal | null {
    return this.term
  }

  get current(): Block | undefined {
    return this.blocks[this.blocks.length - 1]
  }

  get running(): Block | undefined {
    const b = this.current
    return b && b.state === 'running' ? b : undefined
  }

  get awaitingInput(): boolean {
    const b = this.current
    return this.status === 'live' && !!b && b.state === 'prompt' && !this.settling
  }

  last(): LastView | null {
    for (let i = this.blocks.length - 1; i >= 0; i--) {
      const b = this.blocks[i]!
      if (b.state === 'running') {
        return { state: 'run', exit: undefined, hasExit: false, ms: Date.now() - (b.started ?? Date.now()), started: b.started ?? Date.now(), command: b.command }
      }
      if (b.state === 'done') {
        const v = blockVisualState(b)
        return {
          state: v === 'prompt' ? 'ok' : v,
          exit: b.exit,
          hasExit: b.hasExit,
          ms: (b.ended ?? 0) - (b.started ?? 0),
          started: b.started ?? 0,
          command: b.command,
        }
      }
    }
    return null
  }

  attach(container: HTMLDivElement): void {
    if (this.container === container) return
    this.container = container
    container.innerHTML = ''
    const under = document.createElement('div')
    under.className = 'tv-under'
    const gutter = document.createElement('div')
    gutter.className = 'tv-gutter'
    const hostEl = document.createElement('div')
    hostEl.className = 'tv-host'
    const meta = document.createElement('div')
    meta.className = 'tv-meta'
    container.append(under, gutter, hostEl, meta)
    this.underEl = under
    this.gutterEl = gutter
    this.hostEl = hostEl
    this.metaEl = meta
    this.openTerminal()
  }

  private openTerminal(): void {
    const el = this.hostEl
    if (!el) return
    const s = this.host.settings()
    const p = probeGpu()
    this.probe = p
    const term = new Terminal({
      fontFamily: TERMINAL_FONT,
      fontSize: s.fontSize,
      lineHeight: cellLineHeight(s.fontSize),
      theme: terminalTheme(s.outputColours),
      cursorBlink: false,
      cursorStyle: s.cursorStyle,
      cursorInactiveStyle: 'outline',
      allowTransparency: true,
      scrollback: s.scrollback,
      allowProposedApi: true,
      convertEol: false,
      macOptionIsMeta: false,
      drawBoldTextInBrightColors: false,
    })
    this.term = term
    const fit = new FitAddon()
    this.fit = fit
    term.loadAddon(fit)
    term.open(el)
    if (p.rung === 'webgl') {
      try {
        const webgl = new WebglAddon()
        webgl.onContextLoss(() => {
          this.host.log(`SELFCHECK gpuRung=dom tab=${this.id} reason="webgl context lost"`)
          webgl.dispose()
        })
        term.loadAddon(webgl)
      } catch (err) {
        this.host.log(`SELFCHECK webglAddon=FAILED ${(err as Error).message}`)
      }
    }
    this.host.log(`SELFCHECK tab=${this.id} gpuRung=${p.rung}`)
    this.safeFit()

    this.disposables.push(
      term.parser.registerOscHandler(133, (data) => {
        this.onOsc133(data)
        return true
      }),
      term.parser.registerOscHandler(7, (data) => {
        const p7 = parseOsc7(data, this.shellId === 'cmd')
        if (p7 && p7 !== this.cwd) {
          this.cwd = p7
          this.host.cwdChanged(this.id, p7)
          this.host.changed(this.id)
        }
        return true
      }),
      term.parser.registerCsiHandler({ final: 'J' }, (params) => {
        const p0 = params[0]
        if (p0 === 2 || p0 === 3) queueMicrotask(() => this.onClear(p0 === 3))
        return false
      }),
      term.onData((d) => this.onUserData(d)),
      term.onScroll(() => this.afterScroll()),
      term.onRender(() => this.queueOverlay()),
      term.onSelectionChange(() => {
        if (this.host.settings().copyOnSelect) {
          const sel = term.getSelection()
          if (sel) void window.tessa.clipWrite(sel)
        }
      }),
      term.registerLinkProvider({ provideLinks: (y, cb) => cb(this.linksAt(y)) }),
    )
    const ta = (term as unknown as { textarea?: HTMLTextAreaElement }).textarea
    if (ta) {
      ta.addEventListener('paste', (ev) => {
        ev.preventDefault()
        ev.stopImmediatePropagation()
        const text = ev.clipboardData?.getData('text') ?? ''
        if (text) this.requestPaste(text)
      }, true)
    }
    el.addEventListener('mousedown', () => this.host.focusMe(this.id))
    el.addEventListener('dragover', (ev) => {
      ev.preventDefault()
      if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'copy'
    })
    el.addEventListener('drop', (ev) => {
      ev.preventDefault()
      this.onDrop(ev)
    })
    el.addEventListener('contextmenu', (ev) => {
      if (!this.host.settings().rightClickPastes) return
      ev.preventDefault()
      void window.tessa.clipRead().then((t) => {
        if (t) this.requestPaste(t)
      })
    })
    this.ro = new ResizeObserver(() => {
      this.safeFit()
      this.queueOverlay()
    })
    this.ro.observe(el)
    this.gutterEl?.addEventListener('click', (ev) => {
      const t = (ev.target as HTMLElement).closest('[data-blk]') as HTMLElement | null
      if (!t) return
      const bid = Number(t.dataset['blk'])
      this.host.focusMe(this.id)
      this.host.blockMenu(this.id, bid, ev.clientX, ev.clientY)
    })
  }

  private onDrop(ev: DragEvent): void {
    const files = Array.from(ev.dataTransfer?.files ?? [])
    if (files.length) {
      const paths = files
        .map((f) => {
          try {
            return window.tessa.pathForFile(f)
          } catch {
            return ''
          }
        })
        .filter(Boolean)
        .map((p) => (/[\s"']/.test(p) ? `"${p.replace(/"/g, '')}"` : p))
      if (!paths.length) {
        this.host.note('DROP', 'That drop carried no file path. Nothing was inserted.')
        return
      }
      this.host.log(`DROP ${paths.length} path(s) inserted tab=${this.id}`)
      this.pasteNow(paths.join(' ') + ' ')
      return
    }
    const text = ev.dataTransfer?.getData('text') ?? ''
    if (text) this.requestPaste(text)
    else this.host.note('DROP', 'That drop carried nothing this terminal can use.')
  }

  private safeFit(): void {
    const el = this.hostEl
    const term = this.term
    if (!el || !term || !this.fit) return
    if (el.clientWidth < 40 || el.clientHeight < 20) return
    try {
      this.fit.fit()
    } catch {
      return
    }
    if (term.cols < 2 || term.rows < 2) return
    const dims = `${term.cols}x${term.rows}`
    if (dims !== this.lastDims) {
      this.lastDims = dims
      this.post({ t: 'resize', cols: term.cols, rows: term.rows })
    }
  }

  cellHeight(): number {
    const term = this.term
    if (!term) return 21
    const core = (term as unknown as { _core?: { _renderService?: { dimensions?: { css?: { cell?: { height?: number } } } } } })._core
    const h = core?._renderService?.dimensions?.css?.cell?.height
    if (h && h > 0) return h
    const screen = this.hostEl?.querySelector('.xterm-screen') as HTMLElement | null
    return screen && term.rows ? screen.clientHeight / term.rows : 21
  }

  async start(): Promise<void> {
    if (this.disposed || this.starting || this.status === 'live') return
    const term = this.term
    if (!term) return
    this.starting = true
    this.status = 'starting'
    this.statusText = ''
    this.host.changed(this.id)
    const r = await window.tessa.startPty({ cols: term.cols, rows: term.rows }, {
      shellId: this.shellId,
      cwd: this.cwd,
      ...(this.banner ? { banner: this.banner } : {}),
    })
    this.starting = false
    if (this.disposed) {
      if (r.ok) void window.tessa.closePty(r.sessionId, 'tab closed while opening')
      return
    }
    if (!r.ok) {
      this.status = r.waiting ? 'waiting' : 'failed'
      this.statusText = r.error
      this.host.log(`TAB ${this.id} start ${this.status}: ${r.error}`)
      this.host.changed(this.id)
      return
    }
    this.sessionId = r.sessionId
    this.integrated = r.integrated
    if (r.cwd && r.cwd !== this.cwd) {
      this.cwd = r.cwd
      this.host.cwdChanged(this.id, r.cwd)
    }
    if (r.cwdNote) this.host.note('FOLDER', r.cwdNote)
    this.banner = ''
    const port = await claimPort(r.sessionId)
    if (this.disposed) {
      try {
        port.close()
      } catch {
        return
      }
      return
    }
    this.wire(port)
    this.status = 'live'
    this.host.log(`TAB ${this.id} live session=${r.sessionId} shell=${r.shellId} pid=${r.pid} integrated=${r.integrated} grantMs=${r.grantMs}`)
    this.host.changed(this.id)
  }

  private wire(port: MessagePort): void {
    this.port = port
    port.onmessage = (ev: MessageEvent) => {
      const m = ev.data as PtyFromHost
      if (m.t === 'data') {
        const bin = atob(m.b64)
        const bytes = new Uint8Array(bin.length)
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
        const at = performance.now()
        if (this.echoWaiters.length) {
          const text = utf8.decode(bytes)
          this.echoWaiters = this.echoWaiters.filter((w) => {
            if (text.includes(w.ch)) {
              w.resolve(at)
              return false
            }
            return true
          })
        }
        this.term?.write(bytes, () => this.afterWrite(bin))
      } else if (m.t === 'exit') {
        this.ended(`exited`, m.code)
      } else if (m.t === 'probed') {
        const w = this.probeWaiters.get(m.id)
        if (w) {
          this.probeWaiters.delete(m.id)
          w(m.ms)
        }
      } else if (m.t === 'error') {
        this.host.log(`TAB ${this.id} pty error ${m.message}`)
      }
    }
    port.start()
    this.lastDims = ''
    this.safeFit()
  }

  async restart(banner: string): Promise<void> {
    if (this.disposed || this.starting) return
    const old = this.sessionId
    const wasLive = this.status === 'live'
    this.status = 'starting'
    if (old) abandon(old)
    try {
      this.port?.close()
    } catch {
      this.port = null
    }
    this.port = null
    this.sessionId = ''
    if (old && wasLive) await window.tessa.closePty(old, 'restarted by the owner')
    for (const b of [...this.blocks]) this.dropBlock(b)
    this.settling = false
    this.record = ''
    this.tainted = false
    this.unseen = false
    this.newOutput = false
    this.term?.reset()
    this.banner = banner
    this.lastDims = ''
    this.status = 'starting'
    await this.start()
  }

  ended(why: string, code?: number): void {
    if (this.status === 'ended') return
    this.status = 'ended'
    const label = this.host.shellLabel(this.shellId)
    this.statusText = why === 'exited' ? `${label} ended${code === undefined ? '' : ` (exit ${code})`}.` : why
    const b = this.current
    if (b && b.state === 'running') {
      b.state = 'done'
      b.ended = Date.now()
    }
    try {
      this.port?.close()
    } catch {
      this.port = null
    }
    this.port = null
    this.term?.write(`\r\n${sgrTrue('console-t4')}${this.statusText} Restart it from the tab menu.\x1b[0m\r\n`)
    this.host.changed(this.id)
  }

  private post(m: PtyToHost): void {
    this.port?.postMessage(m)
  }

  private afterWrite(raw: string): void {
    const term = this.term
    if (!term) return
    if (this.settling) this.scheduleSettle()
    const buf = term.buffer.active
    if (buf.viewportY < buf.baseY && !this.newOutput) {
      this.newOutput = true
      this.host.changed(this.id)
    }
    if (!this.host.isShown(this.id) && !this.unseen) {
      if (stripEsc(raw).replace(/[\r\n\s\x07\x08]/g, '').length > 0) {
        this.unseen = true
        this.host.changed(this.id)
      }
    }
    this.queueOverlay()
  }

  private afterScroll(): void {
    const term = this.term
    if (!term) return
    const buf = term.buffer.active
    if (buf.viewportY >= buf.baseY && this.newOutput) {
      this.newOutput = false
      this.host.changed(this.id)
    }
    this.queueOverlay()
    if (this.findOpen && this.find.query) this.decorateMatches(false)
  }

  private onOsc133(data: string): void {
    const parts = data.split(';')
    const kind = parts[0]
    if (kind === 'A') return
    if (kind === 'B') this.markB()
    else if (kind === 'C') this.markC()
    else if (kind === 'D') this.markD(parts[1])
  }

  private markB(): void {
    const b = this.current
    if (b && b.state === 'prompt') this.dropBlock(b)
    else if (b && b.state === 'running') this.finish(b, undefined, false)
    this.settling = true
    this.record = ''
    this.tainted = false
    this.missedEnter = false
    this.scheduleSettle()
  }

  private markC(): void {
    if (this.settling) this.settleNow()
    const b = this.current
    if (!b) return
    if (b.state === 'prompt' && !b.command && this.missedEnter) {
      this.missedEnter = false
      const text = this.screenCommand(b)
      if (text) {
        b.command = text
        b.typed = null
      }
    }
    if (b.state === 'prompt' && b.command) {
      b.state = 'running'
      b.started = b.started ?? Date.now()
      this.host.changed(this.id)
      this.queueOverlay()
    } else if (b.state === 'running' && b.started === null) {
      b.started = Date.now()
    }
  }

  private markD(code: string | undefined): void {
    const b = this.current
    if (!b || b.state !== 'running') return
    const n = code === undefined || code === '' ? undefined : Number.parseInt(code, 10)
    this.finish(b, Number.isFinite(n) ? n : undefined, Number.isFinite(n))
  }

  private finish(b: Block, exit: number | undefined, hasExit: boolean): void {
    if (b.typed === null) {
      const text = this.screenCommand(b)
      if (text) b.command = text
    }
    b.state = 'done'
    b.ended = Date.now()
    b.exit = exit
    b.hasExit = hasExit && this.shellId !== 'cmd'
    if (this.shellId === 'cmd') b.exit = undefined
    this.host.commandEnded(this.id, b)
    this.host.changed(this.id)
    this.queueOverlay()
  }

  private scheduleSettle(): void {
    if (this.settleTimer !== null) window.clearTimeout(this.settleTimer)
    this.settleTimer = window.setTimeout(() => this.settleNow(), SETTLE_MS)
  }

  private settleNow(): void {
    if (this.settleTimer !== null) {
      window.clearTimeout(this.settleTimer)
      this.settleTimer = null
    }
    if (!this.settling) return
    this.settling = false
    const term = this.term
    if (!term || term.buffer.active.type !== 'normal') return
    const buf = term.buffer.active
    const cmdLine = buf.baseY + buf.cursorY
    let top = cmdLine
    while (top > 0 && buf.getLine(top)?.isWrapped) top--
    const cmdMarker = term.registerMarker(0)
    const promptMarker = term.registerMarker(top - cmdLine)
    if (!cmdMarker || !promptMarker) return
    const b: Block = {
      id: ++blockSeq,
      promptMarker,
      cmdMarker,
      cmdX: buf.cursorX,
      inputRows: 1,
      command: '',
      typed: null,
      started: null,
      ended: null,
      exit: undefined,
      hasExit: false,
      stopped: false,
      state: 'prompt',
      inputDeco: undefined,
    }
    cmdMarker.onDispose(() => this.forget(b))
    if (!this.firstPromptSeen) {
      this.firstPromptSeen = true
      this.host.log(`TAB ${this.id} first prompt at ${new Date().toISOString()}`)
    }
    if (this.shellId === 'cmd' && this.integrated) {
      b.inputDeco = term.registerDecoration({
        marker: cmdMarker,
        x: b.cmdX,
        width: Math.max(1, term.cols - b.cmdX),
        foregroundColor: INPUT_FG(),
        layer: 'top',
      })
    }
    this.blocks.push(b)
    this.host.changed(this.id)
    this.queueOverlay()
  }

  private forget(b: Block): void {
    const i = this.blocks.indexOf(b)
    if (i >= 0) this.blocks.splice(i, 1)
    if (this.selected === b.id) this.selected = null
    this.queueOverlay()
  }

  private dropBlock(b: Block): void {
    b.inputDeco?.dispose()
    b.cmdMarker.dispose()
    b.promptMarker.dispose()
    this.forget(b)
  }

  private onClear(all: boolean): void {
    const term = this.term
    if (!term) return
    const baseY = term.buffer.active.baseY
    for (const b of [...this.blocks]) {
      if (b === this.current && (b.state === 'running' || this.settling)) continue
      if (all || b.cmdMarker.line >= baseY) this.dropBlock(b)
    }
    this.queueOverlay()
    this.host.changed(this.id)
  }

  private screenCommand(b: Block): string {
    const term = this.term
    if (!term) return ''
    const buf = term.buffer.active
    let line = b.cmdMarker.line
    let text = buf.getLine(line)?.translateToString(true, b.cmdX) ?? ''
    while (buf.getLine(line + 1)?.isWrapped) {
      line++
      text += buf.getLine(line)?.translateToString(true) ?? ''
    }
    return text.trim()
  }

  private onUserData(d: string): void {
    if (this.settling) this.settleNow()
    const b = this.current
    const atPrompt = this.status === 'live' && !!b && b.state === 'prompt'
    if (d === '\x03' && b && b.state === 'running') {
      b.stopped = true
      this.host.changed(this.id)
    }
    if (atPrompt && b) {
      if (this.pasting) this.tainted = true
      for (const ch of d) {
        if (ch === '\r') {
          this.onEnter(b)
          break
        }
        if (ch === '\x7f' || ch === '\b') this.record = this.record.slice(0, -1)
        else if (ch === '\x03') {
          this.record = ''
          this.tainted = false
        } else if (ch >= ' ' && ch !== '\x7f' && !this.pasting) this.record += ch
        else this.tainted = true
      }
    }
    this.post({ t: 'write', b64: btoa(unescape(encodeURIComponent(d))) })
  }

  private onEnter(b: Block): void {
    const term = this.term
    if (!term) return
    const screen = this.screenCommand(b)
    const typed = this.tainted ? null : this.record
    const text = (typed && typed.trim() ? typed : screen) || ''
    if (!text.trim()) {
      this.missedEnter = this.record.trim().length > 0
      return
    }
    this.missedEnter = false
    const buf = term.buffer.active
    b.inputRows = Math.max(1, buf.baseY + buf.cursorY - b.cmdMarker.line + 1)
    b.command = text
    b.typed = typed && typed.trim() ? typed : null
    b.state = 'running'
    b.started = Date.now()
    this.record = ''
    this.tainted = false
    this.selected = null
    this.host.changed(this.id)
    this.queueOverlay()
  }

  canRunAgain(blockId: number): { ok: boolean; why: string } {
    const b = this.blocks.find((x) => x.id === blockId)
    if (!b) return { ok: false, why: 'That command is no longer in this tab.' }
    if (b.typed === null) return { ok: false, why: 'You did not type this command in this tab, so the Console will not type it.' }
    if (this.status !== 'live') return { ok: false, why: 'The shell is not running.' }
    if (!this.awaitingInput || this.record || this.tainted) return { ok: false, why: 'The shell is busy or has text on its line.' }
    return { ok: true, why: '' }
  }

  runAgain(blockId: number): boolean {
    const b = this.blocks.find((x) => x.id === blockId)
    if (!b || !this.canRunAgain(blockId).ok || b.typed === null || !this.term) return false
    const text = b.typed
    this.term.input(text, true)
    this.term.input('\r', true)
    return true
  }

  blockById(id: number): Block | undefined {
    return this.blocks.find((b) => b.id === id)
  }

  blockRange(id: number): { top: number; cmd: number; outStart: number; end: number; cmdRow: string; endRow: string } | null {
    const b = this.blockById(id)
    const term = this.term
    if (!b || !term) return null
    const buf = term.buffer.active
    const end = this.endLineOf(b)
    return {
      top: b.promptMarker.line,
      cmd: b.cmdMarker.line,
      outStart: b.cmdMarker.line + b.inputRows,
      end,
      cmdRow: buf.getLine(b.cmdMarker.line)?.translateToString(true) ?? '',
      endRow: buf.getLine(end)?.translateToString(true) ?? '',
    }
  }

  private endLineOf(b: Block): number {
    const term = this.term
    if (!term) return b.cmdMarker.line
    const buf = term.buffer.active
    const i = this.blocks.indexOf(b)
    const next = this.blocks[i + 1]
    let end: number
    if (next) end = next.promptMarker.line - 1
    else end = b.state === 'running' ? buf.baseY + buf.cursorY : Math.max(b.cmdMarker.line, buf.baseY + buf.cursorY - 1)
    while (end > b.cmdMarker.line && !(buf.getLine(end)?.translateToString(true).trim())) end--
    return Math.max(end, b.cmdMarker.line)
  }

  outputOf(blockId: number): { text: string; lines: number } {
    const b = this.blockById(blockId)
    const term = this.term
    if (!b || !term) return { text: '', lines: 0 }
    const buf = term.buffer.active
    const start = b.cmdMarker.line + b.inputRows
    const end = this.endLineOf(b)
    const out: string[] = []
    let cur = ''
    for (let l = start; l <= end; l++) {
      const line = buf.getLine(l)
      if (!line) continue
      const t = line.translateToString(true)
      if (line.isWrapped && out.length + (cur ? 1 : 0) > 0) cur += t
      else {
        if (l > start) out.push(cur)
        cur = t
      }
    }
    if (end >= start) out.push(cur)
    return { text: out.join('\n'), lines: Math.max(0, end - start + 1) }
  }

  select(blockId: number | null): void {
    this.selected = blockId
    this.queueOverlay()
    this.host.changed(this.id)
  }

  jump(dir: -1 | 1): void {
    const done = this.blocks.filter((b) => b.state !== 'prompt')
    if (!done.length || !this.term) return
    let idx = done.findIndex((b) => b.id === this.selected)
    if (idx < 0) idx = dir < 0 ? done.length - 1 : 0
    else idx = Math.max(0, Math.min(done.length - 1, idx + dir))
    const b = done[idx]!
    this.selected = b.id
    this.term.scrollToLine(Math.max(0, b.promptMarker.line - 1))
    this.queueOverlay()
    this.host.changed(this.id)
  }

  scrollToBlock(blockId: number): void {
    const b = this.blockById(blockId)
    if (!b || !this.term) return
    this.term.scrollToLine(Math.max(0, b.promptMarker.line - 1))
    this.queueOverlay()
  }

  gutterOf(blockId: number): HTMLElement | null {
    return this.gutterEl?.querySelector(`[data-blk="${blockId}"]`) ?? null
  }

  scrollToBottom(): void {
    this.term?.scrollToBottom()
    this.newOutput = false
    this.host.changed(this.id)
  }

  focus(): void {
    this.term?.focus()
  }

  blur(): void {
    this.term?.blur()
  }

  getSelection(): string {
    return this.term?.getSelection() ?? ''
  }

  selectAll(): void {
    this.term?.selectAll()
  }

  clearSelection(): void {
    this.term?.clearSelection()
  }

  get bracketedPaste(): boolean {
    return !!this.term?.modes.bracketedPasteMode
  }

  requestPaste(text: string): void {
    if (this.status !== 'live' || !text) return
    const multiline = /\r|\n/.test(text.trimEnd())
    if (multiline && this.host.settings().pasteGuard && !this.bracketedPaste) {
      const lines = text.trimEnd().split(/\r\n|\r|\n/).length
      this.host.log(`PASTE GUARD asked tab=${this.id} lines=${lines} bracketed=false`)
      this.host.pasteAsk(this.id, text, lines)
      return
    }
    this.pasteNow(text)
  }

  pasteNow(text: string): void {
    if (!this.term || this.status !== 'live') return
    this.host.log(`PASTE sent tab=${this.id} chars=${text.length} bracketed=${this.bracketedPaste}`)
    this.pasting = true
    try {
      this.term.paste(text)
    } finally {
      this.pasting = false
    }
  }

  applySettings(s: SessionSettings): void {
    const term = this.term
    if (!term) return
    term.options.theme = terminalTheme(s.outputColours)
    term.options.cursorStyle = s.cursorStyle
    term.options.scrollback = s.scrollback
    if (term.options.fontSize !== s.fontSize) {
      term.options.fontSize = s.fontSize
      term.options.lineHeight = cellLineHeight(s.fontSize)
      this.safeFit()
    }
    this.queueOverlay()
  }

  refit(): void {
    this.safeFit()
    this.queueOverlay()
  }

  repaint(): void {
    requestAnimationFrame(() => {
      const term = this.term
      if (!term) return
      term.refresh(0, term.rows - 1)
      this.queueOverlay()
    })
  }

  queueOverlay(): void {
    if (this.overlayQueued) return
    this.overlayQueued = true
    requestAnimationFrame(() => {
      this.overlayQueued = false
      this.drawOverlay()
    })
  }

  private visualState(b: Block): 'ok' | 'fail' | 'stop' | 'run' | 'prompt' {
    return blockVisualState(b)
  }

  private tip(b: Block): string {
    if (b.started === null) return ''
    const start = `Started ${hhmm(b.started)}`
    if (b.state === 'running') return `${start}, still running`
    const took = `, took ${fmtDuration((b.ended ?? b.started) - b.started).toLowerCase()}`
    if (b.stopped) return `${start}${took}, stopped`
    if (!b.hasExit) return `${start}${took}${this.shellId === 'cmd' ? '. Command Prompt does not report exit codes.' : ''}`
    return `${start}${took}, exit ${b.exit}`
  }

  metaText(b: Block): { text: string; cls: string } {
    const st = this.visualState(b)
    if (st === 'run') return { text: `RUNNING ${mmss(Date.now() - (b.started ?? Date.now()))}`, cls: 'run' }
    if (st === 'prompt') return { text: '', cls: '' }
    const d = (b.ended ?? 0) - (b.started ?? 0)
    if (st === 'fail') return { text: `EXIT ${b.exit}  ${fmtDuration(d)}`, cls: 'bad' }
    if (st === 'stop') return { text: 'STOPPED', cls: '' }
    return d >= 1000 ? { text: fmtDuration(d), cls: '' } : { text: '', cls: '' }
  }

  private drawOverlay(): void {
    const term = this.term
    const gutter = this.gutterEl
    const meta = this.metaEl
    const under = this.underEl
    if (!term || !gutter || !meta || !under) return
    const buf = term.buffer.active
    const vy = buf.viewportY
    const rows = term.rows
    const ch = this.cellHeight()
    const focused = this.host.isFocused(this.id)
    const now = Math.floor(Date.now() / 1000)
    const sigParts: string[] = [String(vy), String(rows), ch.toFixed(2), String(focused), String(this.selected), String(this.settling), String(buf.cursorY), String(buf.baseY)]
    const visible: { b: Block; top: number; end: number }[] = []
    for (const b of this.blocks) {
      if (b.cmdMarker.isDisposed) continue
      const top = b.promptMarker.isDisposed ? b.cmdMarker.line : b.promptMarker.line
      if (b.state === 'prompt') {
        visible.push({ b, top, end: b.cmdMarker.line })
        sigParts.push(`${b.id}p${top}`)
        continue
      }
      const end = this.endLineOf(b)
      if (end < vy || top > vy + rows - 1) continue
      visible.push({ b, top, end })
      sigParts.push(`${b.id}:${top}:${end}:${this.visualState(b)}:${b.state === 'running' ? now : ''}`)
    }
    const sig = sigParts.join('|')
    if (sig !== this.overlaySig) {
      this.overlaySig = sig
      const g: string[] = []
      const m: string[] = []
      const u: string[] = []
      for (const { b, top, end } of visible) {
        const st = this.visualState(b)
        if (st === 'prompt') {
          if (focused && !this.settling && this.status === 'live' && this.host.isShown(this.id)) {
            const cursorLine = buf.baseY + buf.cursorY
            const last = Math.max(b.cmdMarker.line, cursorLine)
            const y0 = HOST_TOP + (b.cmdMarker.line - vy) * ch
            const h = (last - b.cmdMarker.line + 1) * ch
            if (last >= vy && b.cmdMarker.line <= vy + rows - 1) {
              u.push(`<div class="tv-band" style="top:${y0.toFixed(1)}px;height:${h.toFixed(1)}px"></div>`)
              g.push(`<div class="tv-live" style="top:${(y0 + 6).toFixed(1)}px;height:${Math.max(2, h - 12).toFixed(1)}px"></div>`)
            }
          }
          continue
        }
        const a = Math.max(top, vy)
        const z = Math.min(end, vy + rows - 1)
        if (z < a) continue
        const y0 = HOST_TOP + (a - vy) * ch + (a === top ? 6 : 0)
        const y1 = HOST_TOP + (z - vy + 1) * ch - (z === end ? 6 : 0)
        const tip = this.tip(b).replace(/"/g, '')
        g.push(
          `<div class="tv-g ${st}${this.selected === b.id ? ' sel' : ''}" data-blk="${b.id}" title="${tip}" style="top:${(HOST_TOP + (a - vy) * ch).toFixed(1)}px;height:${((z - a + 1) * ch).toFixed(1)}px"><i style="top:${(y0 - (HOST_TOP + (a - vy) * ch)).toFixed(1)}px;height:${Math.max(2, y1 - y0).toFixed(1)}px"></i></div>`,
        )
        if (this.selected === b.id) {
          u.push(`<div class="tv-sel" style="top:${(HOST_TOP + (a - vy) * ch).toFixed(1)}px;height:${((z - a + 1) * ch).toFixed(1)}px"></div>`)
        }
        const line = b.cmdMarker.line
        if (line >= vy && line <= vy + rows - 1) {
          const mt = this.metaText(b)
          if (mt.text) {
            m.push(`<span class="tv-m ${mt.cls}${this.selected === b.id ? ' sel' : ''}" style="top:${(HOST_TOP + (line - vy) * ch).toFixed(1)}px;height:${ch.toFixed(1)}px;line-height:${ch.toFixed(1)}px">${mt.text}</span>`)
          }
        }
      }
      gutter.innerHTML = g.join('')
      meta.innerHTML = m.join('')
      under.innerHTML = u.join('')
    }
    this.updateSticky()
  }

  private updateSticky(): void {
    const term = this.term
    if (!term) return
    const vy = term.buffer.active.viewportY
    let found: Block | null = null
    if (vy > 0) {
      for (const b of this.blocks) {
        if (b.state === 'prompt') continue
        if (b.cmdMarker.line < vy && this.endLineOf(b) >= vy) found = b
      }
    }
    const next: StickyView | null = found
      ? {
          blockId: found.id,
          text: term.buffer.active.getLine(found.cmdMarker.line)?.translateToString(true) ?? found.command,
          state: (() => {
            const v = this.visualState(found)
            return v === 'prompt' ? 'ok' : v
          })(),
          exit: found.exit,
          started: found.started ?? 0,
        }
      : null
    const a = this.sticky
    if ((a?.blockId ?? null) !== (next?.blockId ?? null) || a?.state !== next?.state) {
      this.sticky = next
      this.host.changed(this.id)
    }
  }

  promptRowText(blockId: number): { prompt: string; command: string } {
    const b = this.blockById(blockId)
    const term = this.term
    if (!b || !term) return { prompt: '', command: '' }
    const line = term.buffer.active.getLine(b.cmdMarker.line)?.translateToString(true) ?? ''
    return { prompt: line.slice(0, b.cmdX), command: line.slice(b.cmdX) }
  }

  private linksAt(y: number): ILink[] {
    const term = this.term
    if (!term) return []
    const line = term.buffer.active.getLine(y - 1)
    if (!line) return []
    const text = line.translateToString(true)
    const out: ILink[] = []
    LINK_RE.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = LINK_RE.exec(text))) {
      const quoted = /^["']/.test(m[0])
      let raw = quoted ? m[0] : m[0].replace(/[.,;:'"\])]+$/, '')
      if (!quoted && m[0].endsWith(')') && /\(\d+(?:,\d+)?\)$/.test(m[0])) raw = m[0]
      const startX = m.index + 1
      const endX = m.index + raw.length
      const target = raw
      out.push({
        range: { start: { x: startX, y }, end: { x: endX, y } },
        text: target,
        decorations: { pointerCursor: true, underline: true },
        activate: (ev) => {
          if (!ev.ctrlKey) return
          this.activateLink(target)
        },
        hover: () => {
          if (this.hostEl) this.hostEl.title = 'Ctrl click to open'
        },
        leave: () => {
          if (this.hostEl) this.hostEl.title = ''
        },
      })
      LINK_RE.lastIndex = m.index + Math.max(1, raw.length)
    }
    return out
  }

  activateLink(target: string): void {
    this.host.log(`LINK activate tab=${this.id} ${JSON.stringify(target)}`)
    target = target.replace(/^["']|["']$/g, '')
    if (/^https?:\/\//i.test(target)) {
      this.host.openLink({ kind: 'url', target })
      return
    }
    let path = target
    let line: number | undefined
    let col: number | undefined
    const paren = path.match(/^(.*)\((\d+)(?:,(\d+))?\)$/)
    const colon = path.match(/^(.*?):(\d+)(?::(\d+))?$/)
    if (paren?.[1]) {
      path = paren[1]
      line = Number(paren[2])
      col = paren[3] ? Number(paren[3]) : undefined
    } else if (colon?.[1] && !/^[A-Za-z]$/.test(colon[1])) {
      path = colon[1]
      line = Number(colon[2])
      col = colon[3] ? Number(colon[3]) : undefined
    }
    this.host.openLink({ kind: 'path', target: path, base: this.cwd, ...(line ? { line } : {}), ...(col ? { col } : {}) })
  }

  setFindOpen(open: boolean): void {
    this.findOpen = open
    if (!open) {
      this.find = { query: '', total: 0, at: 0, capped: false }
      this.matches = []
      this.clearMatchDecos()
    }
    this.host.changed(this.id)
  }

  setFindQuery(q: string): void {
    this.find.query = q
    this.find.at = 0
    this.runFind()
    if (this.matches.length) this.revealMatch()
    this.host.changed(this.id)
  }

  stepFind(d: number): void {
    if (!this.matches.length) return
    this.find.at = (this.find.at + d + this.matches.length) % this.matches.length
    this.revealMatch()
    this.decorateMatches(true)
    this.host.changed(this.id)
  }

  matchRuler(): { ticks: number[]; total: number } {
    const term = this.term
    if (!term || !this.matches.length) return { ticks: [], total: 0 }
    const total = term.buffer.active.length
    const set = new Set<number>()
    for (const m of this.matches) set.add(m.line)
    return { ticks: [...set], total }
  }

  viewWindow(): { top: number; rows: number; total: number } {
    const term = this.term
    if (!term) return { top: 0, rows: 0, total: 0 }
    const buf = term.buffer.active
    return { top: buf.viewportY, rows: term.rows, total: buf.length }
  }

  private runFind(): void {
    const term = this.term
    this.matches = []
    if (!term) return
    const q = this.find.query.toLowerCase()
    if (!q) {
      this.find.total = 0
      this.clearMatchDecos()
      return
    }
    const buf = term.buffer.active
    for (let l = 0; l < buf.length && this.matches.length < MAX_MATCHES; l++) {
      const t = buf.getLine(l)?.translateToString(true).toLowerCase() ?? ''
      let i = t.indexOf(q)
      while (i >= 0 && this.matches.length < MAX_MATCHES) {
        this.matches.push({ line: l, col: i, len: q.length })
        i = t.indexOf(q, i + q.length)
      }
    }
    this.find.total = this.matches.length
    this.find.capped = this.matches.length >= MAX_MATCHES
    let best = this.matches.length - 1
    const vy = buf.viewportY + term.rows
    for (let k = 0; k < this.matches.length; k++) {
      if (this.matches[k]!.line >= buf.viewportY && this.matches[k]!.line < vy) {
        best = k
        break
      }
    }
    this.find.at = Math.max(0, best)
    this.decorateMatches(true)
  }

  private revealMatch(): void {
    const term = this.term
    const m = this.matches[this.find.at]
    if (!term || !m) return
    const buf = term.buffer.active
    if (m.line < buf.viewportY + 2 || m.line >= buf.viewportY + term.rows) {
      term.scrollToLine(Math.max(0, m.line - Math.floor(term.rows / 2)))
    }
    this.decorateMatches(true)
  }

  private clearMatchDecos(): void {
    for (const d of this.matchDecos) d.dispose()
    this.matchDecos = []
    this.decoWindow = ''
  }

  private decorateMatches(force: boolean): void {
    const term = this.term
    if (!term) return
    const buf = term.buffer.active
    const lo = buf.viewportY - term.rows
    const hi = buf.viewportY + term.rows * 2
    const key = `${lo}:${hi}:${this.find.at}:${this.find.query}:${this.matches.length}`
    if (!force && key === this.decoWindow) return
    this.clearMatchDecos()
    this.decoWindow = key
    const cursorAbs = buf.baseY + buf.cursorY
    const bg = MATCH_BG()
    this.matches.forEach((m, k) => {
      if (m.line < lo || m.line > hi) return
      const marker = term.registerMarker(m.line - cursorAbs)
      if (!marker) return
      const current = k === this.find.at
      const deco = term.registerDecoration({
        marker,
        x: m.col,
        width: m.len,
        backgroundColor: current ? CURRENT_BG() : bg,
        ...(current ? { foregroundColor: CURRENT_FG() } : {}),
        layer: current ? 'top' : 'bottom',
      })
      if (deco) {
        deco.onDispose(() => marker.dispose())
        this.matchDecos.push(deco)
      } else marker.dispose()
    })
  }

  bufferTail(n = 40): string {
    const term = this.term
    if (!term) return ''
    const b = term.buffer.active
    const end = b.baseY + b.cursorY + 1
    const out: string[] = []
    for (let i = Math.max(0, end - n); i < end; i++) out.push(b.getLine(i)?.translateToString(true) ?? '')
    return out.join('\n')
  }

  devInput(text: string): void {
    this.term?.input(text, true)
  }

  devProbePty(ch: string): Promise<number | null> {
    const port = this.port
    if (!port) return Promise.resolve(null)
    const id = ++this.probeSeq
    return new Promise((resolve) => {
      const timer = window.setTimeout(() => {
        this.probeWaiters.delete(id)
        resolve(null)
      }, 2000)
      this.probeWaiters.set(id, (ms) => {
        window.clearTimeout(timer)
        resolve(ms)
      })
      port.postMessage({ t: 'probe', id, b64: btoa(ch) })
    })
  }

  devRaw(text: string): void {
    this.post({ t: 'write', b64: btoa(unescape(encodeURIComponent(text))) })
  }

  async measureOne(ch: string): Promise<{ toParse: number; toRender: number; toPaint: number } | null> {
    const term = this.term
    if (!term || !this.port) return null
    return new Promise((resolve) => {
      let tParse = 0
      let tRender = 0
      let done = false
      const bail = window.setTimeout(() => finish(null), 1500)
      const finish = (v: { toParse: number; toRender: number; toPaint: number } | null): void => {
        if (done) return
        done = true
        window.clearTimeout(bail)
        rd.dispose()
        pd.dispose()
        resolve(v)
      }
      const t0 = performance.now()
      const pd = term.onWriteParsed(() => {
        if (!tParse && arrived) tParse = performance.now()
      })
      const rd = term.onRender(() => {
        if (!tParse || tRender) return
        tRender = performance.now()
        requestAnimationFrame(() => {
          window.setTimeout(() => {
            const tPaint = performance.now()
            finish({ toParse: tParse - t0, toRender: tRender - t0, toPaint: tPaint - t0 })
          }, 0)
        })
      })
      let arrived = false
      this.echoWaiters.push({
        ch,
        resolve: () => {
          arrived = true
        },
      })
      term.input(ch, true)
    })
  }

  dispose(why: string): void {
    if (this.disposed) return
    this.disposed = true
    if (this.settleTimer !== null) window.clearTimeout(this.settleTimer)
    if (this.sessionId) {
      abandon(this.sessionId)
      if (this.status === 'live') void window.tessa.closePty(this.sessionId, why)
    }
    try {
      this.port?.close()
    } catch {
      this.port = null
    }
    this.ro?.disconnect()
    for (const d of this.disposables) d.dispose()
    this.clearMatchDecos()
    this.term?.dispose()
    this.term = null
    this.container = null
  }
}
