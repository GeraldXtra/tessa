import type { TermSession } from './terminal/session.ts'
import type { KeyAction } from './terminal/keymap.ts'

export interface DevBindings {
  sessions: () => Map<string, TermSession>
  focused: () => TermSession | undefined
  runAction: (a: KeyAction) => boolean
  layout: () => { mode: string; panes: [string | null, string | null]; focus: number }
  tabs: () => string[]
  newTab: (shellId: string, cwd?: string) => void
  closeTab: (id: string) => void
  setMode: (m: 'one' | 'v' | 'h') => void
  moveToOther: (id: string) => void
  dupTab: (id: string) => void
  restartTab: (id: string) => void
  openPicker: () => void
  openTabMenu: (id: string, x: number, y: number) => void
  openFind: () => void
  setFindQuery: (q: string) => void
  reopenClosed: () => void
  patchSettings: (p: Record<string, unknown>) => void
  guard: () => { tabId: string; lines: number } | null
  notes: () => { label: string; text: string; kind: string }[]
  status: () => string
}

type Step = { type: string; [k: string]: unknown }

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

let noteWatch: (() => string) | null = null

function pct(sorted: number[], p: number): number {
  if (!sorted.length) return NaN
  return sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))]!
}

function stats(v: number[]): string {
  const s = [...v].sort((a, b) => a - b)
  return `n=${s.length} p50=${pct(s, 50).toFixed(1)} p95=${pct(s, 95).toFixed(1)} max=${(s[s.length - 1] ?? NaN).toFixed(1)}`
}

function keyEvent(chord: string): KeyboardEvent {
  const parts = chord.split('+')
  const k = parts[parts.length - 1] ?? ''
  const code = /^[a-z]$/.test(k)
    ? `Key${k.toUpperCase()}`
    : /^[0-9]$/.test(k)
      ? `Digit${k}`
      : ({ '-': 'Minus', '=': 'Equal', ',': 'Comma', up: 'ArrowUp', down: 'ArrowDown', left: 'ArrowLeft', right: 'ArrowRight', end: 'End', enter: 'Enter', escape: 'Escape', tab: 'Tab', insert: 'Insert' } as Record<string, string>)[k] ?? k
  const key = ({ up: 'ArrowUp', down: 'ArrowDown', left: 'ArrowLeft', right: 'ArrowRight', end: 'End', enter: 'Enter', escape: 'Escape', tab: 'Tab', insert: 'Insert' } as Record<string, string>)[k] ?? k
  const ev = new KeyboardEvent('keydown', {
    key,
    code,
    ctrlKey: parts.includes('ctrl'),
    altKey: parts.includes('alt'),
    shiftKey: parts.includes('shift'),
    bubbles: true,
    cancelable: true,
  })
  const keyCode = /^[a-z]$/.test(k)
    ? k.toUpperCase().charCodeAt(0)
    : /^[0-9]$/.test(k)
      ? 48 + Number(k)
      : ({ up: 38, down: 40, left: 37, right: 39, end: 35, enter: 13, escape: 27, tab: 9, insert: 45, '-': 189, '=': 187, ',': 188 } as Record<string, number>)[k] ?? 0
  Object.defineProperty(ev, 'keyCode', { get: () => keyCode })
  Object.defineProperty(ev, 'which', { get: () => keyCode })
  return ev
}

export function installDevHarness(b: DevBindings): () => void {
  if (!/(?:[?&])dev\b/.test(window.location.search)) {
    console.log(`SELFCHECK devHarness=absent search=${JSON.stringify(window.location.search)} __tessaDev=${typeof (window as unknown as { __tessaDev?: unknown }).__tessaDev}`)
    return () => undefined
  }
  const target = (): TermSession | undefined => b.focused()

  const run = async (step: Step): Promise<string> => {
    switch (step.type) {
      case 'input': {
        const s = target()
        if (!s) return 'no focused tab'
        s.devInput(String(step['text'] ?? ''))
        return `typed ${String(step['text'] ?? '').length} chars into ${s.id}`
      }
      case 'raw': {
        const s = target()
        if (!s) return 'no focused tab'
        s.devRaw(String(step['text'] ?? ''))
        return `raw ${String(step['text'] ?? '').length} chars into ${s.id}`
      }
      case 'keyCheck': {
        const chord = String(step['chord'] ?? '')
        const snap = (): Record<string, unknown> => {
          const f = target()
          const t = f?.terminal
          const L = b.layout()
          const ae = document.activeElement as HTMLElement | null
          return {
            tabs: b.tabs().join(','),
            focus: f?.id ?? '',
            layout: `${L.mode}:${L.panes.join('|')}:${L.focus}`,
            font: t?.options.fontSize ?? 0,
            view: t ? t.buffer.active.viewportY : -1,
            sel: t?.hasSelection() ? t.getSelection().length : 0,
            picked: f?.selected ?? null,
            line: f?.bufferTail(1).trim() ?? '',
            pal: !!document.querySelector('.pal'),
            palQ: (document.querySelector('.pal-in') as HTMLInputElement | null)?.value ?? '',
            palSel: (document.querySelector('.pal .it.sel') as HTMLElement | null)?.innerText.replace(/\s+/g, ' ').slice(0, 40) ?? '',
            set: !!document.querySelector('.set'),
            find: !!document.querySelector('.ph.find'),
            findAt: (document.querySelector('.ph.find .cnt') as HTMLElement | null)?.innerText ?? '',
            menu: !!document.querySelector('.menu:not(.pal)'),
            start: !!document.querySelector('.empty'),
            notes: [...document.querySelectorAll('.toasts .toast:not(.out)')].map((n) => (n as HTMLElement).innerText.replace(/\s+/g, ' ').slice(0, 50)).join(' / '),
            active: ae ? String(ae.className || ae.tagName).slice(0, 30) : '',
          }
        }
        const a = snap()
        const el = (document.activeElement as HTMLElement | null) ?? document.body
        const notPrevented = el.dispatchEvent(keyEvent(chord))
        await sleep(Number(step['ms'] ?? 400))
        const z = snap()
        const diff = Object.keys(z)
          .filter((k) => JSON.stringify(a[k]) !== JSON.stringify(z[k]))
          .map((k) => `${k}: ${JSON.stringify(a[k])} -> ${JSON.stringify(z[k])}`)
        return `KEY ${chord} in=${String(a['active'])} prevented=${!notPrevented} ${diff.length ? diff.join('; ') : 'NO CHANGE'}`
      }
      case 'key': {
        const ev = keyEvent(String(step['chord'] ?? ''))
        const el = (document.activeElement as HTMLElement | null) ?? document.body
        const notPrevented = el.dispatchEvent(ev)
        return `key ${String(step['chord'])} target=${el.className || el.tagName} defaultPrevented=${!notPrevented}`
      }
      case 'paste': {
        const s = target()
        if (!s) return 'no focused tab'
        s.requestPaste(String(step['text'] ?? ''))
        const g = b.guard()
        return `paste requested tab=${s.id} guard=${g ? `${g.lines} lines` : 'none'} bracketed=${s.bracketedPaste}`
      }
      case 'waitFor': {
        const re = new RegExp(String(step['pattern'] ?? ''))
        const deadline = Date.now() + Number(step['timeoutMs'] ?? 20000)
        let seen = ''
        while (Date.now() < deadline) {
          seen = target()?.bufferTail(40) ?? ''
          if (re.test(seen)) return `MATCHED last: ${seen.split('\n').filter(Boolean).slice(-3).join(' | ').slice(0, 200)}`
          await sleep(200)
        }
        return `TIMED OUT last: ${seen.split('\n').filter(Boolean).slice(-3).join(' | ').slice(0, 200)}`
      }
      case 'waitIdle': {
        const deadline = Date.now() + Number(step['timeoutMs'] ?? 20000)
        while (Date.now() < deadline) {
          const s = target()
          if (s?.awaitingInput) return `idle at prompt tab=${s.id}`
          await sleep(100)
        }
        return 'TIMED OUT waiting for an idle prompt'
      }
      case 'action': {
        const name = String(step['name'] ?? '')
        const arg = step['arg']
        const f = target()
        switch (name) {
          case 'newTab':
            b.newTab(String(arg ?? 'powershell'), step['cwd'] ? String(step['cwd']) : undefined)
            break
          case 'closeTab':
            if (f) b.closeTab(f.id)
            break
          case 'mode':
            b.setMode(String(arg) as 'one' | 'v' | 'h')
            break
          case 'moveToOther':
            if (f) b.moveToOther(f.id)
            break
          case 'dup':
            if (f) b.dupTab(f.id)
            break
          case 'restart':
            if (f) b.restartTab(f.id)
            break
          case 'picker':
            b.openPicker()
            break
          case 'tabMenu': {
            const el = f ? (document.querySelector(`.tab[data-tab="${f.id}"]`) as HTMLElement | null) : null
            const r = el?.getBoundingClientRect()
            if (f) b.openTabMenu(f.id, r ? r.left + 20 : 200, r ? r.bottom + 8 : 48)
            break
          }
          case 'find':
            b.runAction('find')
            break
          case 'findQuery': {
            const t0 = performance.now()
            b.setFindQuery(String(arg ?? ''))
            const tq = performance.now()
            await new Promise<void>((r) => requestAnimationFrame(() => setTimeout(r, 0)))
            const tp = performance.now()
            const fs = target()
            return `findQuery ${JSON.stringify(arg)} search+decorate ${(tq - t0).toFixed(1)} ms, to next paint ${(tp - t0).toFixed(1)} ms, total=${fs?.find.total} capped=${fs?.find.capped} lines=${fs?.terminal?.buffer.active.length}`
          }
          case 'reopen':
            b.reopenClosed()
            break
          case 'settings':
            b.patchSettings((arg ?? {}) as Record<string, unknown>)
            break
          case 'scrollUp': {
            const t = f?.terminal
            t?.scrollLines(-Number(arg ?? 10))
            break
          }
          case 'blockMenu': {
            const blk = f?.blocks.filter((x) => x.state !== 'prompt').at(Number(arg ?? -1))
            if (blk && f && !f.gutterOf(blk.id)) {
              f.scrollToBlock(blk.id)
              await new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 50))))
            }
            const g = blk && f ? f.gutterOf(blk.id) : null
            const r = g?.getBoundingClientRect()
            if (g && r) g.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: r.left + 8, clientY: r.top + 4 }))
            return `block menu for ${blk?.id ?? 'none'} found=${!!g}`
          }
          case 'focusPane':
            b.runAction('focusRight')
            break
          default:
            return b.runAction(name as KeyAction) ? `action ${name}` : `unknown action ${name}`
        }
        return `action ${name} done`
      }
      case 'fill': {
        const el = document.querySelector(String(step['selector'] ?? '')) as HTMLInputElement | null
        if (!el) return `fill: ${String(step['selector'])} not found`
        const t0 = performance.now()
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(el, String(step['text'] ?? ''))
        el.dispatchEvent(new Event('input', { bubbles: true }))
        await new Promise<void>((r) => requestAnimationFrame(() => setTimeout(r, 0)))
        return `filled ${String(step['selector'])} in ${(performance.now() - t0).toFixed(1)} ms to next paint`
      }
      case 'waitLink': {
        const want = step['linked'] !== false
        const until = performance.now() + Number(step['timeoutMs'] ?? 30000)
        const t0 = performance.now()
        while (performance.now() < until) {
          const st = b.status()
          if (want ? /LINKED/.test(st) : /OFFLINE/.test(st)) return `link ${want ? 'LINKED' : 'OFFLINE'} after ${Math.round(performance.now() - t0)} ms: ${st.replace(/\n/g, ' | ')}`
          await new Promise<void>((r) => setTimeout(r, 100))
        }
        return `waitLink TIMED OUT waiting for ${want ? 'LINKED' : 'OFFLINE'}`
      }
      case 'watchNotes': {
        const t0 = performance.now()
        const seen = new Map<Element, { text: string; kind: string; on: number; out?: number; gone?: number }>()
        let maxShown = 0
        const scan = (): void => {
          const now = performance.now() - t0
          const live = new Set(document.querySelectorAll('.toasts .toast'))
          maxShown = Math.max(maxShown, document.querySelectorAll('.toasts .toast:not(.out)').length)
          for (const el of live) {
            const rec = seen.get(el)
            if (!rec) seen.set(el, { text: ((el as HTMLElement).innerText ?? '').replace(/\n/g, ' ').slice(0, 70), kind: el.className, on: now })
            else if (rec.out === undefined && el.classList.contains('out')) rec.out = now
          }
          for (const [el, rec] of seen) if (rec.gone === undefined && !live.has(el)) rec.gone = now
        }
        const iv = window.setInterval(scan, 25)
        scan()
        noteWatch = (): string => {
          window.clearInterval(iv)
          scan()
          return `NOTES maxShown=${maxShown} ` + [...seen.values()].map((r) => `[${r.kind}] "${r.text}" on=${r.on.toFixed(0)} out=${r.out?.toFixed(0) ?? '-'} gone=${r.gone?.toFixed(0) ?? '-'} life=${r.out !== undefined ? (r.out - r.on).toFixed(0) : '-'}`).join(' || ')
        }
        return 'watching notes'
      }
      case 'notesReport': {
        const report = noteWatch ? noteWatch() : 'no note watch running'
        noteWatch = null
        return report
      }
      case 'repaint': {
        const t = target()?.terminal
        t?.refresh(0, t.rows - 1)
        return `repainted rows 0..${(t?.rows ?? 1) - 1}`
      }
      case 'click': {
        const el = document.querySelector(String(step['selector'] ?? '')) as HTMLElement | null
        if (!el) return `click: ${String(step['selector'])} not found`
        const r = el.getBoundingClientRect()
        const opts = { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, ctrlKey: !!step['ctrl'], button: Number(step['button'] ?? 0) }
        el.dispatchEvent(new MouseEvent('mousedown', opts))
        el.dispatchEvent(new MouseEvent('mouseup', opts))
        el.dispatchEvent(new MouseEvent(step['button'] === 2 ? 'contextmenu' : step['dbl'] ? 'dblclick' : 'click', opts))
        return `clicked ${String(step['selector'])}`
      }
      case 'hoverLink': {
        const s = target()
        const t = s?.terminal
        if (!s || !t) return 'no tab'
        const text = String(step['text'] ?? '')
        const buf = t.buffer.active
        const rows = Array.from({ length: t.rows }, (_, k) => buf.viewportY + k)
        if (step['last']) rows.reverse()
        for (const y of rows) {
          const line = buf.getLine(y)?.translateToString(true) ?? ''
          const x = line.indexOf(text)
          if (x < 0) continue
          const screen = (t.element?.querySelector('.xterm-screen') as HTMLElement | null)?.getBoundingClientRect()
          if (!screen) return 'no screen'
          const cw = screen.width / t.cols
          const ch = screen.height / t.rows
          const cx = screen.left + (x + 1.5) * cw
          const cy = screen.top + (y - buf.viewportY + 0.5) * ch
          const el = t.element?.querySelector('.xterm-screen') as HTMLElement
          el.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: cx, clientY: cy }))
          await sleep(300)
          if (step['ctrlClick']) {
            const o = { bubbles: true, cancelable: true, clientX: cx, clientY: cy, ctrlKey: true, button: 0 }
            el.dispatchEvent(new MouseEvent('mousedown', o))
            el.dispatchEvent(new MouseEvent('mouseup', o))
            el.dispatchEvent(new MouseEvent('click', o))
          }
          if (step['plainClick']) {
            const o = { bubbles: true, cancelable: true, clientX: cx, clientY: cy, ctrlKey: false, button: 0 }
            el.dispatchEvent(new MouseEvent('mousedown', o))
            el.dispatchEvent(new MouseEvent('mouseup', o))
            el.dispatchEvent(new MouseEvent('click', o))
          }
          return `hovered "${text}" at col ${x} row ${y - buf.viewportY}`
        }
        return `link text "${text}" not on screen`
      }
      case 'activateLink': {
        const s = target()
        if (!s) return 'no tab'
        s.activateLink(String(step['text'] ?? ''))
        return 'activated'
      }
      case 'dump': {
        const what = String(step['what'] ?? 'state')
        if (what === 'blocks') {
          const s = target()
          return JSON.stringify(
            (s?.blocks ?? []).map((x) => ({
              id: x.id,
              state: x.state,
              cmd: x.command,
              typed: x.typed,
              exit: x.exit,
              hasExit: x.hasExit,
              stopped: x.stopped,
              line: x.cmdMarker.line,
              rows: x.inputRows,
              ms: x.ended && x.started ? x.ended - x.started : null,
              runAgain: s?.canRunAgain(x.id),
              range: s?.blockRange(x.id),
            })),
          )
        }
        if (what === 'tabs') {
          return JSON.stringify(
            b.tabs().map((id) => {
              const s = b.sessions().get(id)
              return { id, shell: s?.shellId, cwd: s?.cwd, status: s?.status, statusText: s?.statusText, session: s?.sessionId?.slice(0, 8), unseen: s?.unseen, name: s?.name }
            }),
          )
        }
        if (what === 'layout') return JSON.stringify(b.layout())
        if (what === 'notes') return JSON.stringify(b.notes())
        if (what === 'status') return b.status().replace(/\n/g, ' | ')
        if (what === 'buffer') return (target()?.bufferTail(Number(step['n'] ?? 20)) ?? '').split('\n').join(' | ')
        if (what === 'find') {
          const s = target()
          return JSON.stringify({ ...s?.find, ruler: s?.matchRuler().ticks.length })
        }
        if (what === 'dom') {
          const sel = String(step['selector'] ?? '')
          return Array.from(document.querySelectorAll(sel))
            .slice(0, 12)
            .map((e) => {
              const r = (e as HTMLElement).getBoundingClientRect()
              const cs = getComputedStyle(e as HTMLElement)
              const tip = (e as HTMLElement).getAttribute('title')
              return `${(e as HTMLElement).className}[${Math.round(r.x)},${Math.round(r.y)} ${Math.round(r.width)}x${Math.round(r.height)}] fg=${cs.color} bg=${cs.backgroundColor} font=${cs.fontFamily.slice(0, 40)} ${cs.fontSize} "${((e as HTMLElement).innerText ?? '').slice(0, 60).replace(/\n/g, ' ')}"${tip ? ` title="${tip}"` : ''}`
            })
            .join(' || ')
        }
        if (what === 'canvases') {
          return Array.from(document.querySelectorAll('.tv'))
            .map((tv, i) => {
              const cs = getComputedStyle(tv as HTMLElement)
              return `tv${i} vis=${cs.visibility} canvases=${tv.querySelectorAll('canvas').length} rows=${tv.querySelectorAll('.xterm-rows').length} screens=${tv.querySelectorAll('.xterm-screen').length}`
            })
            .join(' ; ')
        }
        if (what === 'lines') {
          const t = target()?.terminal
          if (!t) return ''
          const buf = t.buffer.active
          const out: string[] = []
          let cur = ''
          const last = buf.baseY + buf.cursorY
          for (let y = Math.max(0, last - Number(step['rows'] ?? 200)); y <= last; y++) {
            const line = buf.getLine(y)
            if (!line) continue
            if (line.isWrapped) cur += line.translateToString(true)
            else {
              if (y > 0) out.push(cur)
              cur = line.translateToString(true)
            }
          }
          out.push(cur)
          return out.filter((l) => l.trim()).join(' || ')
        }
        if (what === 'modes') {
          const t = target()?.terminal
          return `modes=${JSON.stringify(t?.modes ?? null)} cols=${t?.cols} rows=${t?.rows}`
        }
        if (what === 'cursor') {
          const t = target()?.terminal
          const core = (t as unknown as { _core?: { coreService?: { isCursorHidden?: boolean; isCursorInitialized?: boolean }; _coreBrowserService?: { isFocused?: boolean } } } | undefined)?._core
          const buf = t?.buffer.active
          const cell = (t?.element?.querySelector('.xterm-screen') as HTMLElement | null)?.getBoundingClientRect()
          return `cursorBlink=${t?.options.cursorBlink} cursorStyle=${t?.options.cursorStyle} inactive=${t?.options.cursorInactiveStyle} hidden=${core?.coreService?.isCursorHidden} initialized=${core?.coreService?.isCursorInitialized} xtermFocused=${core?._coreBrowserService?.isFocused} textareaActive=${document.activeElement === t?.textarea} docHasFocus=${document.hasFocus()} cursor=${buf?.cursorX},${buf?.cursorY} rows=${t?.rows} cellH=${cell && t ? (cell.height / t.rows).toFixed(2) : '?'} lineHeight=${t?.options.lineHeight?.toFixed(3)}`
        }
        if (what === 'gpu') {
          return Array.from(b.sessions().values()).map((s) => `${s.id}:${s.probe?.rung}`).join(',')
        }
        return 'unknown dump'
      }
      case 'latency': {
        const s = target()
        if (!s) return 'no tab'
        const n = Number(step['n'] ?? 100)
        const chars = String(step['chars'] ?? 'xzqjk')
        const parse: number[] = []
        const render: number[] = []
        const paint: number[] = []
        let misses = 0
        for (let i = 0; i < n + 10; i++) {
          await sleep(30 + Math.random() * 40)
          const ch = chars[i % chars.length]!
          const r = await s.measureOne(ch)
          await sleep(20)
          s.devInput('\x7f')
          if (!r) {
            misses++
            if (misses > 20) break
            continue
          }
          if (i < 10) continue
          parse.push(r.toParse)
          render.push(r.toRender)
          paint.push(r.toPaint)
        }
        return `LATENCY tab=${s.id} shell=${s.shellId} rung=${s.probe?.rung} toParse ${stats(parse)} | toRender ${stats(render)} | toPaint ${stats(paint)} | misses=${misses}`
      }
      case 'ptyLatency': {
        const s = target()
        if (!s) return 'no tab'
        const n = Number(step['n'] ?? 100)
        const chars = String(step['chars'] ?? 'xzqjk')
        const rtt: number[] = []
        let misses = 0
        for (let i = 0; i < n + 10; i++) {
          await sleep(30 + Math.random() * 40)
          const ms = await s.devProbePty(chars[i % chars.length]!)
          await sleep(20)
          s.devInput('\x7f')
          if (ms === null) {
            misses++
            if (misses > 20) break
            continue
          }
          if (i >= 10) rtt.push(ms)
        }
        return `PTYLATENCY tab=${s.id} shell=${s.shellId} hostWriteToEcho ${stats(rtt)} | misses=${misses}`
      }
      case 'scrollFrames': {
        const s = target()
        const t = s?.terminal
        if (!s || !t) return 'no tab'
        const ms = Number(step['ms'] ?? 5000)
        const per = Number(step['lines'] ?? 3)
        const intervals: number[] = []
        let last = 0
        let dir = -1
        const end = performance.now() + ms
        await new Promise<void>((resolve) => {
          const loop = (now: number): void => {
            if (last) intervals.push(now - last)
            last = now
            const buf = t.buffer.active
            if (buf.viewportY <= 0) dir = 1
            if (buf.viewportY >= buf.baseY) dir = -1
            t.scrollLines(dir * per)
            if (now < end) requestAnimationFrame(loop)
            else resolve()
          }
          requestAnimationFrame(loop)
        })
        const sorted = [...intervals].sort((a, c) => a - c)
        const bucket = (lo: number, hi: number): number => intervals.filter((v) => v > lo && v <= hi).length
        return `SCROLLFRAMES n=${intervals.length} p50=${pct(sorted, 50).toFixed(2)} p95=${pct(sorted, 95).toFixed(2)} max=${(sorted[sorted.length - 1] ?? NaN).toFixed(1)} grid16.67=${bucket(0, 25)} at33.3=${bucket(25, 41.7)} at50=${bucket(41.7, 58.3)} longer=${bucket(58.3, 1e9)} find=${s.findOpen ? s.find.total : 'closed'}`
      }
      case 'frames': {
        const ms = Number(step['ms'] ?? 30000)
        const s = target()
        const latencies: number[] = []
        const intervals: number[] = []
        let last = 0
        let going = true
        const loop = (t: number): void => {
          if (last) intervals.push(t - last)
          last = t
          if (going) requestAnimationFrame(loop)
        }
        requestAnimationFrame(loop)
        const end = Date.now() + ms
        const probe = step['typeEvery'] ? Number(step['typeEvery']) : 0
        while (Date.now() < end) {
          if (probe && s) {
            const r = await s.measureOne(String(step['char'] ?? 'q'))
            if (r) latencies.push(r.toPaint)
            await sleep(30)
            s.devInput('\x7f')
            await sleep(probe)
          } else await sleep(250)
        }
        going = false
        const bucket = (lo: number, hi: number): number => intervals.filter((v) => v > lo && v <= hi).length
        const sorted = [...intervals].sort((a, c) => a - c)
        return `FRAMES n=${intervals.length} p50=${pct(sorted, 50).toFixed(2)} p95=${pct(sorted, 95).toFixed(2)} p99=${pct(sorted, 99).toFixed(2)} max=${(sorted[sorted.length - 1] ?? NaN).toFixed(1)} grid16.67=${bucket(0, 25)} at33.3=${bucket(25, 41.7)} at50=${bucket(41.7, 58.3)} longer=${bucket(58.3, 1e9)} | typed ${stats(latencies)}`
      }
      default:
        return `unknown step ${step.type}`
    }
  }

  ;(window as unknown as { __tessaDev?: (s: Step) => Promise<string> }).__tessaDev = (s: Step) => run(s)
  console.log('SELFCHECK devHarness=installed')
  return () => {
    delete (window as unknown as { __tessaDev?: unknown }).__tessaDev
  }
}
