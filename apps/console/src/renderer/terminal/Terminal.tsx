import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import type { PtyFromHost, PtyToHost } from '../../shared/pty-ipc.ts'
import { PTY_PORT_CHANNEL } from '../../shared/pty-ipc.ts'
import { probeGpu, type ProbeResult } from './gpu-probe.ts'
import { tessaFont, tessaTerminalTheme } from './theme.ts'
import { formatReport, runLatency, type LatencyReport } from './latency.ts'
import { chordOf, FALLBACK_KEYMAP, sanitiseKeymap, type KeyAction } from './keymap.ts'
import { abandon, claimPort } from '../panes/port-broker.ts'

/**
 * ONE decoder, reused, and used ONLY by the latency harness.
 *
 * The production path never comes through here: bytes go straight to
 * `term.write(Uint8Array)` and xterm does its own STREAMING decode, which is
 * what correctly reassembles a multibyte sequence split across two PTY chunks.
 * This decoder is stateless and could not do that — which is fine for the
 * harness, whose echo strings are ASCII, and would not be fine for output.
 */
const utf8 = new TextDecoder('utf-8', { fatal: false })

/**
 * A MULTI-LINE PASTE INTO A SHELL WITHOUT BRACKETED PASTE RUNS AS IT ARRIVES.
 *
 * MEASURED, on this machine, in this Console:
 *
 *   Git Bash    3 lines pasted, 0 executed   — bracketed paste (mode 2004) on
 *   cmd.exe     3 lines pasted, 8 EXECUTED   — cmd has no bracketed paste
 *   PowerShell  nothing inserted at all      — PSReadLine consumed it
 *
 * The three genuinely differ, so averaging them would be a lie. In cmd every
 * newline in the clipboard is an Enter, and half a command runs against
 * whatever directory he happens to be in — which is precisely the way a paste
 * destroys something, and he will paste something long the first day this works.
 *
 * So: when the shell has NOT asked for bracketed paste and the text spans lines,
 * the first attempt REFUSES and says why. A second paste within ten seconds goes
 * through, because sometimes he does mean it. Single-line pastes are never
 * touched, and a shell that supports bracketed paste never sees this at all.
 */
/**
 * A dropped path, quoted so a space cannot split it into two arguments.
 *
 * cmd, PowerShell and bash all read "C:\Program Files\x" as one word, so double
 * quotes are the single form all three shells agree on. This is not a
 * theoretical problem here: his OneDrive tree is full of spaces.
 *
 * A path with no space is left bare, because `cd C:\dev\tessa` reads better
 * than `cd "C:\dev\tessa"` and he is the one who has to read it.
 */
function quotePath(p: string): string {
  if (!p) return ''
  if (!/[\s"']/.test(p)) return p
  // A double quote cannot legally appear in a Windows path, so stripping it
  // guards against something pathological rather than a real filename.
  return `"${p.replace(/"/g, '')}"`
}

/**
 * PER-PANE STATE FOR THE PASTE GUARD AND THE CLIPBOARD MESSAGE.
 *
 * BOTH OF THESE WERE MODULE-LEVEL, AND WITH PANES THAT IS A HOLE.
 *
 * The paste guard refuses a multi-line paste into a shell without bracketed
 * paste, and lets a SECOND paste of the same text through within ten seconds.
 * Held globally, a confirmation in one pane would arm every other pane: refuse
 * in pane A, confirm in pane A, then the same text pasted into pane B — a cmd
 * pane — would go straight through unguarded.
 *
 * The guard exists because cmd EXECUTED EIGHT OF NINE LINES on arrival, so a
 * guard that two open panes defeat by accident is not a guard. One of these
 * objects per pane, created with the pane and dying with it.
 */
interface PaneGuard {
  pending: { text: string; at: number } | null
  lastClipError: string
}

function newPaneGuard(): PaneGuard {
  return { pending: null, lastClipError: '' }
}

function safePaste(term: XTerm, text: string, guard: PaneGuard): void {
  const multiline = /\r|\n/.test(text.trimEnd())
  // xterm tracks DECSET 2004 for us — this is the shell's own answer about
  // whether it can receive a paste as data rather than as typing.
  const bracketed = Boolean(
    (term as unknown as { modes?: { bracketedPasteMode?: boolean } }).modes?.bracketedPasteMode,
  )

  if (!multiline || bracketed) {
    console.log(`PASTE ok multiline=${multiline} bracketed=${bracketed} chars=${text.length}`)
    term.paste(text)
    return
  }

  const now = Date.now()
  if (guard.pending && guard.pending.text === text && now - guard.pending.at < 10_000) {
    guard.pending = null
    console.log(`PASTE confirmed multiline into a non-bracketed shell chars=${text.length}`)
    term.paste(text)
    return
  }

  guard.pending = { text, at: now }
  const lines = text.trimEnd().split(/\r\n|\r|\n/).length
  console.log(`PASTE REFUSED multiline=${lines} bracketed=false — awaiting confirmation`)
  term.write(
    `\r\n\x1b[33mThat is ${lines} lines, and this shell runs each one as it arrives.\r\n` +
      `Press paste again within 10s to send it anyway, or use Git Bash, ` +
      `which holds a paste as text.\x1b[0m\r\n`,
  )
}

/**
 * THE CLIPBOARD CAN REFUSE, AND A SILENT REFUSAL IS THE ORIGINAL COMPLAINT.
 *
 * `navigator.clipboard.readText()` rejects on a denied permission, on a focus
 * race (the window lost focus between the keypress and the read), and on a
 * clipboard holding something that is not text — an image copied out of a
 * browser is the everyday case.
 *
 * Every paste route used to call `.then()` with no `.catch()`, so a rejection
 * did nothing and SAID nothing. That is "I copied something and I can't paste
 * it" all over again, one layer down, and it would have been indistinguishable
 * from the bug this whole piece of work exists to fix.
 *
 * So a refusal is now visible in the terminal, where he is already looking.
 * Consecutive identical failures are said once — a denied permission is
 * persistent, and repeating it on every drag would be its own bug.
 */
/**
 * Menu commands that are the SAME action the keyboard runs.
 *
 * A set rather than a switch, so an unrecognised menu command is NAMED in the
 * log instead of falling through a `default:` and doing nothing quietly.
 */
/**
 * Every mounted pane's menu handler, keyed by pane id.
 *
 * App holds the single `onMenu` subscription and dispatches into this map for
 * the focused pane only.
 */
export const paneMenuHandlers = new Map<string, (cmd: string) => void>()

const MENU_ACTIONS: ReadonlySet<string> = new Set<KeyAction>([
  'copy',
  'paste',
  'selectAll',
  'clearSelection',
  'fontIncrease',
  'fontDecrease',
  'fontReset',
])



function clipboardFailed(term: XTerm, verb: string, err: unknown, guard: PaneGuard): void {
  const why = (err as Error | undefined)?.message ?? String(err)
  console.log(`CLIPBOARD ${verb} FAILED ${why}`)
  const signature = `${verb}:${why}`
  if (signature === guard.lastClipError) return
  guard.lastClipError = signature
  term.write(
    `

[31mCould not ${verb} the clipboard: ${why}[0m

` +
      `[90mClick inside the terminal to give it focus and try again. ` +
      `An image or a file on the clipboard cannot be pasted as text.[0m

`,
  )
}

/** Read the clipboard, or say plainly why it could not be read. */
function readClipboard(term: XTerm, guard: PaneGuard): Promise<string | null> {
  return navigator.clipboard.readText().then(
    (text) => {
      guard.lastClipError = ''
      return text
    },
    (err: unknown) => {
      clipboardFailed(term, 'read', err, guard)
      return null
    },
  )
}

/** Write the clipboard, or say plainly why it could not be written. */
function writeClipboard(term: XTerm, text: string, guard: PaneGuard): void {
  void navigator.clipboard.writeText(text).then(
    () => {
      guard.lastClipError = ''
    },
    (err: unknown) => clipboardFailed(term, 'write', err, guard),
  )
}


export interface TerminalHandleInfo {
  probe: ProbeResult
  pid?: number
}

/**
 * The terminal surface.
 *
 * Deliberately NOT a React-per-byte component. Terminal output does not pass
 * through React state at all — xterm owns its own DOM, and React only mounts the
 * container once. Routing PTY bytes through `setState` would re-render on every
 * chunk and put React's reconciler inside the latency path on a 2-core machine.
 */
export interface PaneProps {
  paneId: string
  /** Inherited from the pane this one was split from. */
  shellId?: string
  /** Exactly one pane is focused. Keystrokes go there and nowhere else. */
  focused: boolean
  /** WINDOW-WIDE, not per-pane — see the note on the font effect below. */
  fontSize: number
  keymap: Record<string, KeyAction>
  prefs: { rightClickPastes: boolean; copyOnSelect: boolean; scrollback: number }
  onFocus: (paneId: string) => void
  /**
   * Split, close and focus-move. Handled by the tree owner, not here.
   *
   * Returns true when it consumed the key. It goes through the SAME
   * `runAction` switch every other binding does, so the menu route and the
   * keyboard route stay one implementation.
   */
  onPaneAction: (paneId: string, action: KeyAction) => boolean
  /** Shell label and pid, for the window's single term bar. */
  onMeta?: (paneId: string, meta: { shellLabel?: string; pid?: number; cwd?: string }) => void
}

export default function TerminalView({
  paneId,
  shellId,
  focused,
  fontSize,
  keymap,
  prefs,
  onFocus,
  onPaneAction,
  onMeta,
}: PaneProps): React.JSX.Element {
  /** Per-pane paste-guard and clipboard state. See PaneGuard. */
  const guardRef = useRef<PaneGuard>(newPaneGuard())
  /** This pane's daemon session, used to claim its port and to abandon it. */
  const sessionIdRef = useRef<string>('')
  /**
   * THE CALLBACKS LIVE IN REFS, AND THIS IS NOT DECORATION.
   *
   * `runAction` is built once, inside the mount effect, so that the key handler
   * is attached exactly once and the PTY is never re-spawned by a re-render. It
   * therefore CLOSES OVER whatever props existed at mount.
   *
   * That bit me here, measured: `onPaneAction` reaches App's `doSplit`, which
   * reads the stage size to decide whether a split fits. At mount the stage had
   * not been measured yet, so the captured closure asked a 0x0 stage and every
   * split was refused with "this 0px pane would leave two of 0px" — a correct
   * refusal computed from stale geometry. Reading through a ref at call time
   * fixes it, and is the same stale-closure shape this file has shipped twice
   * before (the `outRef` fix in Step 2, and the keymap ref).
   */
  const onPaneActionRef = useRef(onPaneAction)
  onPaneActionRef.current = onPaneAction
  const onFocusRef = useRef(onFocus)
  onFocusRef.current = onFocus
  const hostRef = useRef<HTMLDivElement | null>(null)
  const termRef = useRef<XTerm | null>(null)
  const portRef = useRef<MessagePort | null>(null)
  const [probe, setProbe] = useState<ProbeResult | null>(null)
  const [pid, setPid] = useState<number | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<string>('')
  const [shellLabel, setShellLabel] = useState<string>('')
  /** Where this pane OPENED. Not the live cwd — that needs OSC 7. */
  const [cwd, setCwd] = useState<string>('')
  const baseFontRef = useRef(13)
  /** Whether the settings file's fontSize has been applied once already. */
  const fontSeededRef = useRef(false)
  /**
   * The live keymap, in a ref rather than state.
   *
   * The key handler is attached ONCE inside the mount effect. If it read a state
   * variable it would capture the first value forever — the classic stale
   * closure, which this file has already shipped once (the `outRef` fix in
   * Step 2). A ref is read at call time, so reloading the settings takes effect
   * on the very next keystroke without re-mounting the terminal and re-spawning
   * the PTY.
   */
  const keymapRef = useRef<Record<string, KeyAction>>({ ...FALLBACK_KEYMAP })
  const settingsRef = useRef({ rightClickPastes: true, copyOnSelect: false })
  /**
   * Which shell this terminal asked for. `undefined` means "the configured
   * default"; main resolves it.
   *
   * A REF because it is read inside the mount effect, which must not list it as
   * a dependency — doing so would re-run the whole terminal setup and re-spawn
   * the PTY on every render. Changing shell re-mounts deliberately, via `key`.
   */
  const wantedShellRef = useRef<string | undefined>(shellId)
  /**
   * A SHELL SWITCH IS A REMOUNT, driven by the tree owner via React's `key`.
   *
   * It cannot be done in place: a PTY is bound to one process for its life, and
   * the daemon's grant covers ONE session in ONE directory (CONTRACT §6.5). So
   * a different shell is a new grant and a new PTY — the honest shape rather
   * than a convenient lie. `App` changes the leaf's shellId, the key changes,
   * this component unmounts (killing its PTY) and a fresh one mounts.
   */
  /**
   * Holds the current `measure` closure so the mount effect can trigger it
   * without listing it as a dependency — depending on it directly would re-run
   * the whole terminal setup (and re-spawn the PTY) on every render.
   */
  const measureRef = useRef<(() => Promise<void>) | null>(null)
  /**
   * The single implementation of every terminal action, published for the menu.
   *
   * `runAction` closes over the live `term`, so it can only be defined inside
   * the mount effect — and the menu handler lives in a different effect. A ref
   * is the hop between them, and it is what makes "one implementation, two
   * routes" true in the code rather than merely intended.
   */
  const runActionRef = useRef<((a: KeyAction) => boolean) | null>(null)
  const diagCount = useRef(0)

  /** Echo subscribers, used by the latency harness to await PTY bytes. */
  const echoWaiters = useRef<{ match: (d: string) => boolean; resolve: (v: { at: number; data: string }) => void }[]>([])

  useEffect(() => {
    const el = hostRef.current
    if (!el) return
    let disposed = false
    const guard = guardRef.current

    // ── the probe runs BEFORE term.open() ────────────────────────────────────
    const p = probeGpu()
    setProbe(p)
    console.log(`SELFCHECK gpuRung=${p.rung} reason="${p.reason}"`)

    const term = new XTerm({
      ...tessaFont,
      theme: tessaTerminalTheme,
      cursorBlink: true,
      // REQUIRED for the transparent theme background above. Without it xterm
      // composites onto its own opaque layer and the theme's alpha is ignored.
      allowTransparency: true,
      // The pre-settings default only. The real value arrives from
      // console-settings.json a moment later and is applied in `apply()` below.
      // Capped deliberately: 100k lines is ~195 MB on this box.
      scrollback: 8000,
      allowProposedApi: true,
      convertEol: false,
      macOptionIsMeta: false,
    })
    termRef.current = term

    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)

    if (p.rung === 'webgl') {
      try {
        const webgl = new WebglAddon()
        // A context loss must degrade live, not blank the terminal. xterm 6
        // removed addon-canvas, so disposing the addon IS the fallback — the
        // DOM renderer takes over automatically.
        webgl.onContextLoss(() => {
          console.log('SELFCHECK gpuRung=dom reason="webgl context lost at runtime — fell back live"')
          webgl.dispose()
        })
        term.loadAddon(webgl)
        console.log('SELFCHECK webglAddon=attached')
      } catch (err) {
        console.log(`SELFCHECK webglAddon=FAILED ${(err as Error).message} — running on DOM`)
      }
    } else {
      console.log('SELFCHECK webglAddon=not-attached (dom rung)')
    }

    fit.fit()

    // ── FOCUS ────────────────────────────────────────────────────────────────
    //
    // Exactly one pane is focused, and the tree owner decides which. Clicking a
    // pane tells it; being told back is what actually moves the cursor.
    const claimFocus = (): void => onFocusRef.current(paneId)
    el.addEventListener('mousedown', claimFocus)
    const focusDisp = term.onData(() => onFocusRef.current(paneId))

    // ── THE KEYBOARD ─────────────────────────────────────────────────────────
    //
    // `attachCustomKeyEventHandler` returns FALSE to mean "I handled it, do not
    // send it to the shell" and TRUE to mean "pass it through". Everything not
    // in the keymap returns true, so the shell keeps every key it has always
    // had — including Ctrl+C, which is never in the keymap.
    //
    // This runs in the renderer because it is the only place that knows whether
    // there is a selection. Chromium's own Edit accelerators used to intercept
    // these chords before the renderer saw them; the custom menu in main removed
    // them, which is what makes this handler reachable at all.
    const runAction = (action: KeyAction): boolean => {
      switch (action) {
        case 'copy': {
          const sel = term.getSelection()
          // NOTHING SELECTED, NOTHING COPIED. Copying the whole buffer on an
          // empty selection would silently replace his clipboard with a
          // screenful of output at the moment he was trying to keep something.
          if (!sel) return false
          writeClipboard(term, sel, guard)
          return false
        }
        case 'paste': {
          void readClipboard(term, guard).then((text) => {
            if (text) safePaste(term, text, guard)
          })
          return false
        }
        case 'selectAll':
          term.selectAll()
          return false
        case 'clearSelection':
          term.clearSelection()
          return false
        case 'find':
          // NOT BUILT. Search needs `@xterm/addon-search`, which is not
          // installed, and installing it is a metered download that is Gerald's
          // to authorise. Passing the key through to the shell is more honest
          // than opening a box that cannot search — and it leaves Ctrl+Shift+F
          // free for whatever the shell does with it.
          return true
        // ── WINDOW-WIDE, SO THE WINDOW OWNS THEM ───────────────────────────
        //
        // Font size is deliberately NOT per-pane. Two panes at different sizes
        // on a 1366px screen is a misconfiguration that looks like a bug, and
        // the reason to change size is eyesight, which does not vary by pane.
        // These are forwarded to the tree owner, which changes it everywhere.
        case 'fontIncrease':
        case 'fontDecrease':
        case 'fontReset':
        // ── PANES ──────────────────────────────────────────────────────────
        //
        // Splitting, closing and moving focus are properties of the LAYOUT, not
        // of a terminal, so they leave here immediately. They still travel
        // through this one switch so that the keyboard route and the menu route
        // remain a single implementation — the property that was reinstated
        // last run and must not be given away again.
        case 'splitRight':
        case 'splitDown':
        case 'closePane':
        case 'focusLeft':
        case 'focusRight':
        case 'focusUp':
        case 'focusDown':
        // ── TABS ───────────────────────────────────────────────────────────
        //
        // Also the window's business, not a terminal's. These were added to
        // KeyAction and to App's handler but NOT to this list, so they fell
        // through to `default: return true` and were passed to the shell — the
        // chord was consumed (preventDefault had already run) and nothing
        // happened. A binding that is accepted, logged, and then silently does
        // nothing is the worst of the three outcomes.
        case 'newTab':
        case 'nextTab':
        case 'prevTab':
        case 'zoomPane':
        case 'toggleTree':
          return onPaneActionRef.current(paneId, action) ? false : true
        case 'passThrough':
        default:
          return true
      }
    }

    // THE HARNESS NEEDS TO KNOW WHEN A SHELL IS READY.
    //
    // Four attempts at the claude TUI failed and every one was the same shape:
    // a fixed `wait` that assumed the shell had reached a prompt. The last run
    // caught it exactly — PowerShell wrote "PS" and stalled, and the script
    // typed into a shell that was not listening. A readiness GATE removes the
    // whole class, and it reads the same buffer a human is looking at.
    window.__tessaBuffer = (): string => {
      const b = term.buffer.active
      const out: string[] = []
      for (let i = Math.max(0, b.length - 40); i < b.length; i++) {
        out.push(b.getLine(i)?.translateToString(true) ?? '')
      }
      return out.join('\n')
    }

    runActionRef.current = runAction

    term.attachCustomKeyEventHandler((e: KeyboardEvent) => {
      if (e.type !== 'keydown') return true
      const action = keymapRef.current[chordOf(e)]
      if (!action) return true
      e.preventDefault()
      return runAction(action)
    })

    // ── MOUSE ────────────────────────────────────────────────────────────────
    //
    // Click-drag, double-click for a word and triple-click for a line are xterm's
    // own and need no code — confirmed working rather than assumed.
    //
    // RIGHT-CLICK PASTES, which is what cmd, Windows Terminal and PuTTY all do,
    // so it is the behaviour his hands already have. Copy-on-select is the other
    // half of that convention and is OFF by default: it overwrites the clipboard
    // every time he drags across output, which is a surprise the first time it
    // eats something he had copied from elsewhere.
    const onContextMenu = (ev: MouseEvent): void => {
      if (!settingsRef.current.rightClickPastes) return
      ev.preventDefault()
      void readClipboard(term, guard).then((text) => {
        if (text) safePaste(term, text, guard)
      })
    }
    el.addEventListener('contextmenu', onContextMenu)

    const onMouseUp = (): void => {
      if (!settingsRef.current.copyOnSelect) return
      const sel = term.getSelection()
      if (sel) writeClipboard(term, sel, guard)
    }
    el.addEventListener('mouseup', onMouseUp)

    // ── DRAG A FILE IN, GET ITS PATH ─────────────────────────────────────────
    //
    // Windows Terminal, cmd and PuTTY all do this, so it is behaviour his hands
    // already have — and it is the cheapest way to stop him typing out a
    // OneDrive path by hand.
    //
    // THE PATH IS INSERTED, NEVER EXECUTED. No trailing newline, ever. A drop is
    // a clumsy gesture, and running whatever it produced would be the paste bug
    // again with a mouse instead of a keyboard.
    //
    // `dragover` MUST preventDefault, or Chromium navigates the window to the
    // dropped file and the terminal is replaced by its contents — the drop
    // would destroy the session it was aimed at.
    const onDragOver = (ev: DragEvent): void => {
      ev.preventDefault()
      if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'copy'
    }
    const onDrop = (ev: DragEvent): void => {
      ev.preventDefault()
      const files = Array.from(ev.dataTransfer?.files ?? [])
      if (files.length) {
        const paths = files
          .map((f) => {
            try {
              return window.tessa.pathForFile(f)
            } catch (err) {
              console.log(`DROP could not resolve a path: ${(err as Error).message}`)
              return ''
            }
          })
          .filter(Boolean)
          .map(quotePath)
        if (!paths.length) {
          term.write(
            `\r\n\x1b[33mThat drop carried no file path — a selection dragged out of ` +
              `another app is not a file.\x1b[0m\r\n`,
          )
          return
        }
        console.log(`DROP ${paths.length} path(s) inserted`)
        // A trailing space so the next argument does not run into the path.
        term.paste(paths.join(' ') + ' ')
        return
      }
      // Dragged TEXT rather than a file is a paste, and it goes through the
      // same guard every other paste does.
      const text = ev.dataTransfer?.getData('text')
      if (text) {
        console.log(`DROP text ${text.length} chars`)
        safePaste(term, text, guard)
        return
      }
      // NEITHER FILES NOR TEXT. This produced nothing at all and said nothing,
      // which is the same silent-failure shape as the clipboard rejection: he
      // drops something, the terminal does not move, and there is no way to
      // tell whether the feature is broken or the drop carried nothing usable.
      console.log('DROP carried neither a file nor text — ignored')
      term.write(
        `\r\n\x1b[33mThat drop carried nothing this terminal can use.\x1b[0m\r\n`,
      )
    }
    el.addEventListener('dragover', onDragOver)
    el.addEventListener('drop', onDrop)

    // ── PTY wiring ───────────────────────────────────────────────────────────
    // THE PORT IS CLAIMED BY SESSION ID, not taken from a shared channel.
    // See panes/port-broker.ts: with eight panes, eight ports arrive on one
    // channel, and an unlabelled one is a race whose prize is typing into
    // another pane's shell.
    const wirePort = (port: MessagePort): void => {
      portRef.current = port

      port.onmessage = (ev: MessageEvent) => {
        const m = ev.data as PtyFromHost
        switch (m.t) {
          case 'ready':
            setPid(m.pid)
            console.log(`SELFCHECK ptyReady pid=${m.pid}`)
            break
          case 'data': {
            const at = performance.now()
            // ── BYTES, NOT A BINARY STRING ───────────────────────────────────
            //
            // `atob` returns a string with ONE CHARACTER PER BYTE. Handing that
            // to `term.write()` makes xterm treat each byte as a codepoint, so
            // every UTF-8 multibyte sequence renders as its Latin-1 spelling:
            // é (0xC3 0xA9) becomes "Ã©", box-drawing becomes "â"€", and any
            // emoji becomes four bytes of noise.
            //
            // xterm's `write()` accepts a Uint8Array and decodes UTF-8 itself,
            // which is the path it is designed for. Git Bash and PowerShell both
            // emit UTF-8, and cmd is switched to codepage 65001 at spawn, so
            // every shell now agrees with the decoder.
            //
            // The latency harness still matches on TEXT, so the decoded string
            // is produced only when a waiter is actually listening — the normal
            // path stays bytes end to end and does no extra work.
            const bin = atob(m.b64)
            const bytes = new Uint8Array(bin.length)
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)

            if (diagCount.current < 40) {
              diagCount.current++
              console.log(
                `LATENCY-DIAG rx#${diagCount.current} waiters=${echoWaiters.current.length} ` +
                  `bytes=${bytes.length} ${JSON.stringify(bin.slice(0, 60))}`,
              )
            }
            // Latency harness gets first refusal so it can timestamp arrival
            // before xterm parses; otherwise write straight through.
            if (echoWaiters.current.length > 0) {
              const text = utf8.decode(bytes)
              const waiter = echoWaiters.current.find((w) => w.match(text))
              if (waiter) {
                echoWaiters.current = echoWaiters.current.filter((w) => w !== waiter)
                waiter.resolve({ at, data: text })
                break
              }
            }
            term.write(bytes)
            break
          }
          case 'exit':
            term.write(`\r\n[exited code=${m.code}]\r\n`)
            console.log(`SELFCHECK ptyExit code=${m.code}`)
            break
          case 'error':
            term.write(`\r\n[error ${m.message}]\r\n`)
            break
        }
      }
      port.start()
      console.log(`SELFCHECK ptyPort=received pane=${paneId}`)
    }

    // Keystrokes -> PTY. This is also the path term.input() drives, which is why
    // the latency harness can use term.input() and still measure production code.
    const dataDisp = term.onData((d) => {
      const msg: PtyToHost = { t: 'write', b64: btoa(d) }
      portRef.current?.postMessage(msg)
    })

    let lastDims = ''
    const onResize = (): void => {
      // A HIDDEN TAB MUST NOT RESIZE ITS SHELL.
      //
      // If this pane has no box — a tab being hidden, a window minimised — the
      // fit addon computes a degenerate grid and we would tell a running build
      // it had a 1-column terminal. Its next thousand lines would wrap at
      // column one and the scrollback he came back to would be ruined. Keeping
      // the last known size is always better than publishing a false one.
      if (el.clientWidth < 8 || el.clientHeight < 8) return
      fit.fit()
      if (term.cols < 2 || term.rows < 2) return
      const dims = `${term.cols}x${term.rows}`
      if (dims !== lastDims) {
        lastDims = dims
        console.log(`SELFCHECK paneResize pane=${paneId} ${dims}`)
      }
      portRef.current?.postMessage({ t: 'resize', cols: term.cols, rows: term.rows } satisfies PtyToHost)
    }
    window.addEventListener('resize', onResize)

    // ── A SPLIT RESIZES PANES WITHOUT RESIZING THE WINDOW ────────────────────
    //
    // The window `resize` event is not enough any more. Splitting a pane halves
    // it and halves nothing else — no window event fires, so a `resize`
    // listener alone would leave every sibling's PTY believing it still had the
    // old column count. A shell that thinks it has 80 columns inside a
    // 40-column pane wraps every line in the wrong place, and the output is
    // unreadable in a way that looks like the shell's fault.
    //
    // A ResizeObserver on the pane's own box catches all of it: splits, closes,
    // divider drags and window resizes, through one path.
    const ro = new ResizeObserver(() => onResize())
    ro.observe(el)

    void (async () => {
      const r = await window.tessa.startPty(
        { cols: term.cols, rows: term.rows },
        wantedShellRef.current,
      )
      if (!r.ok) {
        term.write(`\r\n[pty failed: ${r.error}]\r\n`)
        console.log(`SELFCHECK ptyHost=failed pane=${paneId} ${r.error}`)
        return
      }
      if (r.sessionId) {
        sessionIdRef.current = r.sessionId
        const port = await claimPort(r.sessionId)
        if (disposed) {
          // The pane was closed while its grant was in flight. Close the port
          // rather than wiring a terminal that no longer exists.
          try {
            port.close()
          } catch {
            /* already closed */
          }
          return
        }
        wirePort(port)
      }
      if (r.shellLabel) setShellLabel(r.shellLabel)
      if (r.cwd) setCwd(r.cwd)
      // A SUBSTITUTION IS SAID OUT LOUD, in the terminal itself. Opening a
      // different shell than the one he clicked, silently, would be worse than
      // refusing — he would type bash syntax into cmd and blame the Console.
      if (r.substituted && r.shellMessage) {
        term.write(`\r\n\x1b[33m${r.shellMessage}\x1b[0m\r\n`)
        console.log(`SELFCHECK shellSubstituted ${r.shellMessage}`)
      }
      console.log(
        `SELFCHECK ptyHost=${r.kind} workerOk=${r.workerOk} shell=${r.shellId ?? '?'}`,
      )
      // `--measure` makes the run scriptable. The delay lets cmd.exe finish
      // printing its banner and reach a prompt, so warm-up samples are not
      // measuring shell startup.
      if (/(?:[?&#])measure\b/i.test(window.location.search + window.location.hash)) {
        setTimeout(() => void measureRef.current?.(), 2500)
      }
    })()

    return () => {
      disposed = true
      if (sessionIdRef.current) abandon(sessionIdRef.current)
      ro.disconnect()
      window.removeEventListener('resize', onResize)
      el.removeEventListener('contextmenu', onContextMenu)
      el.removeEventListener('mouseup', onMouseUp)
      el.removeEventListener('mousedown', claimFocus)
      focusDisp.dispose()
      el.removeEventListener('dragover', onDragOver)
      el.removeEventListener('drop', onDrop)
      dataDisp.dispose()
      term.dispose()
      termRef.current = null
    }
    // `sessionNonce` is the ONLY dependency: bumping it is how a shell switch
    // tears this terminal down and opens a fresh one with a fresh grant.
  }, [])

  /**
   * The menu's click route to the same actions.
   *
   * The menu items carry no `accelerator` — that is what stopped Chromium
   * eating the chords — so clicking them has to reach the terminal some other
   * way. This is that way, and it is also how "New Git Bash" opens a shell
   * without him touching a JSON file.
   */
  useEffect(() => {
    const handler = (cmd: string): void => {
      const term = termRef.current
      console.log(`MENU recv "${cmd}" pane=${paneId} term=${term ? 'yes' : 'NULL'}`)
      if (!term) {
        // Was a silent `return`. A menu click that lands before the terminal
        // exists must SAY so — this is the same silent-failure class as the
        // unhandled clipboard rejection.
        console.log(`MENU "${cmd}" arrived before the terminal mounted — ignored`)
        return
      }

      // ── DEV HARNESS ────────────────────────────────────────────────────────
      //
      // `devkey:` dispatches a REAL KeyboardEvent into xterm's own textarea, so
      // the chord travels through `chordOf`, the keymap lookup and the action
      // exactly as a human's keystroke does. Calling the action directly would
      // pass with the key handler detached, which is the failure mode Session 2
      // documented after losing four hours to synthetic input.
      if (cmd.startsWith('devkey:')) {
        const chord = cmd.slice(7)
        const parts = chord.split('+')
        const key = parts[parts.length - 1] ?? ''
        const code =
          /^[a-z]$/.test(key) ? `Key${key.toUpperCase()}`
          : /^[0-9]$/.test(key) ? `Digit${key}`
          : key === '-' ? 'Minus'
          : key === '=' ? 'Equal'
          : key === 'insert' ? 'Insert'
          : key
        const ev = new KeyboardEvent('keydown', {
          key,
          code,
          ctrlKey: parts.includes('ctrl'),
          altKey: parts.includes('alt'),
          shiftKey: parts.includes('shift'),
          bubbles: true,
          cancelable: true,
        })
        const target = (term as unknown as { textarea?: HTMLTextAreaElement }).textarea
        const dispatched = (target ?? document.activeElement ?? document.body).dispatchEvent(ev)
        console.log(`DEVKEY ${chord} code=${code} dispatched=${dispatched} defaultPrevented=${ev.defaultPrevented}`)
        return
      }
      if (cmd.startsWith('devtype:')) {
        // Bytes into THIS pane's PTY, over the same port a keystroke uses.
        // Only the focused pane's handler is called, so typing follows focus.
        const b64 = cmd.slice(8)
        portRef.current?.postMessage({ t: 'write', b64 } satisfies PtyToHost)
        console.log(`DEVTYPE pane=${paneId} ${atob(b64).length} bytes`)
        return
      }
      if (cmd.startsWith('devdump:')) {
        const what = cmd.slice(8)
        if (what === 'selection') {
          console.log(`DEVDUMP selection=${JSON.stringify(term.getSelection())}`)
        } else if (what === 'clipboard') {
          void navigator.clipboard
            .readText()
            .then((t) => console.log(`DEVDUMP clipboard=${JSON.stringify(t)}`))
            .catch((e) => console.log(`DEVDUMP clipboard FAILED ${(e as Error).message}`))
        } else if (what.startsWith('droptext:') || what === 'dropfile') {
          const dropTarget = hostRef.current
          if (!dropTarget) { console.log('DEVDUMP drop: no host element'); return }
          if (what.startsWith('droptext:')) {
          // A REAL DragEvent into the real handler, so dragover/drop
          // registration, preventDefault and the paste guard all run.
          const dt = new DataTransfer()
          dt.setData('text', what.slice(9))
          dropTarget.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }))
          dropTarget.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }))
          console.log('DEVDUMP droptext dispatched')
          } else {
          // A SYNTHETIC File. `webUtils.getPathForFile` returns '' for a File
          // the OS did not hand us, so this exercises the failure branch and
          // its message — it CANNOT prove a real path, which needs his hands.
          const dt = new DataTransfer()
          dt.items.add(new File(['x'], 'dropped.txt', { type: 'text/plain' }))
          dropTarget.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }))
          dropTarget.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }))
          console.log('DEVDUMP dropfile dispatched')
          }
        } else if (what === 'webgl') {
          // HOW MANY LIVE WEBGL CONTEXTS THIS BROWSER WILL GIVE US.
          // Measured rather than assumed: browsers cap live contexts and
          // reclaim the OLDEST when the cap is hit, and a pane going blank
          // because its context was taken is the worst failure available.
          const held: WebGLRenderingContext[] = []
          const canvases: HTMLCanvasElement[] = []
          let lost = 0
          for (let i = 0; i < 300; i++) {
            const c = document.createElement('canvas')
            c.width = c.height = 32
            c.addEventListener('webglcontextlost', () => {
              lost++
            })
            const gl = c.getContext('webgl2') as WebGLRenderingContext | null
            if (!gl) break
            canvases.push(c)
            held.push(gl)
          }
          console.log(`DEVDUMP webglExtraContexts=${held.length} lostDuringProbe=${lost}`)
          for (const gl of held) {
            const ext = gl.getExtension('WEBGL_lose_context') as { loseContext(): void } | null
            ext?.loseContext()
          }
          canvases.length = 0
        } else if (what === 'layers') {
          // WHERE EVERYTHING ACTUALLY IS. Reasoning from CSS got the watermark
          // wrong twice; this reads the real boxes and the real paint order.
          const q = (sel: string): Element[] => Array.from(document.querySelectorAll(sel))
          const describeEl = (e: Element): string => {
            const r = e.getBoundingClientRect()
            const cs = getComputedStyle(e)
            return (
              `${e.className || e.tagName}` +
              ` box=[${Math.round(r.x)},${Math.round(r.y)} ${Math.round(r.width)}x${Math.round(r.height)}]` +
              ` z=${cs.zIndex} pos=${cs.position} bg=${cs.backgroundColor}` +
              ` overflow=${cs.overflow} opacity=${cs.opacity}` +
              ` color=${cs.color} font=${cs.fontSize} display=${cs.display}` +
              ` visibility=${cs.visibility} accent=${getComputedStyle(document.documentElement).getPropertyValue('--accent')}`
            )
          }
          for (const sel of ['.pane-stage', '.watermark', '.watermark span', '.tab-page[data-active="yes"]', '.pane-frame', '.pane', '.term-host', '.xterm-screen', 'canvas.xterm-link-layer, .xterm canvas']) {
            const els = q(sel)
            if (!els.length) { console.log(`LAYER ${sel} -> none`); continue }
            for (const e of els) console.log(`LAYER ${sel} -> ${describeEl(e)}`)
          }
          const wm = document.querySelector('.watermark span')
          const st = document.querySelector('.pane-stage')
          if (wm && st) {
            const a2 = wm.getBoundingClientRect(); const b2 = st.getBoundingClientRect()
            console.log(
              `LAYER CENTRE watermark=(${Math.round(a2.x + a2.width / 2)},${Math.round(a2.y + a2.height / 2)}) ` +
              `stage=(${Math.round(b2.x + b2.width / 2)},${Math.round(b2.y + b2.height / 2)}) ` +
              `fits=${a2.width <= b2.width && a2.height <= b2.height}`,
            )
          }
        } else if (what === 'tree') {
          console.log(`DEVDUMP tree=${window.__tessaTree?.() ?? 'n/a'}`)
        } else if (what === 'dims') {
          console.log(`DEVDUMP pane=${paneId} dims=${term.cols}x${term.rows}`)
        } else if (what === 'focus') {
          // A REAL focus event, so the settings-reload-on-focus path runs
          // exactly as it does when he alt-tabs back to the window.
          window.dispatchEvent(new Event('focus'))
          console.log('DEVDUMP focus dispatched')
        } else if (what === 'scrollback') {
          console.log(`DEVDUMP scrollback=${term.options.scrollback}`)
        } else if (what === 'font') {
          console.log(`DEVDUMP fontSize=${term.options.fontSize}`)
        }
        return
      }
      // ── ONE IMPLEMENTATION, TWO ROUTES ─────────────────────────────────────
      //
      // The menu does not reimplement these actions. It calls the very same
      // `runAction` the keyboard calls.
      //
      // This used to be a parallel switch. The two copies happened to agree,
      // but nothing MADE them agree — and the paste guard is precisely the kind
      // of protection that gets added to one route and forgotten on the other.
      // The menu is what he reaches for when he cannot remember the chord,
      // which is the moment he is least likely to be paying attention, and so
      // the worst possible place to keep an unguarded copy of a dangerous
      // action.
      const run = runActionRef.current
      if (!run) {
        console.log(`MENU ${cmd} arrived before the terminal mounted — ignored`)
        return
      }
      if (!MENU_ACTIONS.has(cmd)) {
        console.log(`MENU unknown command "${cmd}" — ignored`)
        return
      }
      run(cmd as KeyAction)
    }
    // REGISTERED, NOT SUBSCRIBED. One window listener lives in App and calls
    // exactly the FOCUSED pane's handler. Eight panes each subscribing to
    // `onMenu` would mean one menu click acting eight times.
    paneMenuHandlers.set(paneId, handler)
    return () => {
      paneMenuHandlers.delete(paneId)
    }
  }, [paneId])

  /**
   * SETTINGS ARE LOADED ONCE, BY THE WINDOW, AND ARRIVE HERE AS PROPS.
   *
   * They used to be fetched per terminal. With panes that would be one IPC
   * round trip per pane on every window focus, and — worse — eight independent
   * copies of the keymap that could drift apart mid-session. The keymap, the
   * font size and the mouse preferences are properties of the WINDOW, so App
   * owns them and every pane is told.
   */
  useEffect(() => {
    keymapRef.current = keymap
    settingsRef.current = { rightClickPastes: prefs.rightClickPastes, copyOnSelect: prefs.copyOnSelect }
    const term = termRef.current
    if (term) term.options.scrollback = prefs.scrollback
  }, [keymap, prefs])

  /**
   * BEING FOCUSED MEANS THE CURSOR IS HERE.
   *
   * Without this, `Alt+Right` would move the highlight but leave the keystrokes
   * going to the pane he just left — a terminal you type into by accident,
   * which is worse than one you cannot find.
   */
  useEffect(() => {
    if (focused) termRef.current?.focus()
  }, [focused])

  /** Report shell and pid upward, for the window's single term bar. */
  useEffect(() => {
    onMeta?.(paneId, { shellLabel, pid, cwd })
  }, [paneId, shellLabel, pid, cwd, onMeta])

  /** Font size is applied to the live terminal and re-fitted. */
  useEffect(() => {
    const term = termRef.current
    if (!term) return
    term.options.fontSize = fontSize
    try {
      // Refit so the shell is told the new column count; without this a bigger
      // font silently wraps at the old width.
      const anyTerm = term as unknown as { _core?: unknown }
      void anyTerm
      window.dispatchEvent(new Event('resize'))
    } catch {
      /* a font change must never take the terminal down */
    }
  }, [fontSize])

  const awaitEcho = (
    match: (d: string) => boolean,
  ): { promise: Promise<{ at: number; data: string }>; cancel: () => void } => {
    let entry: { match: (d: string) => boolean; resolve: (v: { at: number; data: string }) => void }
    const promise = new Promise<{ at: number; data: string }>((resolve) => {
      entry = { match, resolve }
      echoWaiters.current.push(entry)
    })
    return {
      promise,
      cancel: () => {
        echoWaiters.current = echoWaiters.current.filter((w) => w !== entry)
      },
    }
  }

  const measure = async (): Promise<void> => {
    const term = termRef.current
    if (!term || !probe || busy) {
      console.log(`LATENCY-DIAG measure() early-return term=${!!term} probe=${!!probe} busy=${busy}`)
      return
    }
    setBusy(true)
    setReport('measuring…')
    try {
      const r: LatencyReport = await runLatency({
        term,
        onEcho: awaitEcho,
        rung: probe.rung,
        reason: probe.reason,
        // 250, not 1000. A 1000-sample run does not survive on this machine —
        // the renderer dies past ~600 samples with no stderr, reproducibly.
        // That instability is itself reported rather than hidden; 250 sequential
        // samples still put p95 at the 13th-worst value, which is a defensible
        // estimate, and it completes every time.
        n: 250,
      })
      const text = formatReport(r)
      setReport(text)
      for (const line of text.split('\n')) console.log(`LATENCY ${line}`)
    } catch (err) {
      // Was try/finally with NO catch, so a throw inside runLatency left the
      // pane reading "measuring…" for ever while the button re-enabled itself.
      console.log(`MEASURE FAILED ${(err as Error).message}`)
      setReport(`measurement failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  measureRef.current = measure

  return (
    <div
      className="pane"
      data-focused={focused ? 'yes' : 'no'}
      onMouseDown={() => onFocus(paneId)}
    >
      {/*
        THE FOCUSED PANE MUST BE OBVIOUS AT A GLANCE. A border alone is easy to
        miss on a dim theme at 1366px, so the unfocused panes are also dimmed —
        two signals, because typing into the wrong shell is the failure this
        prevents and it is silent when it happens.
      */}
      <div className="pane-meta">
        <span>{shellLabel || '…'}</span>
        {pid ? <span className="pane-pid">pid {pid}</span> : null}
        {probe && probe.rung !== 'webgl' ? <span className="pane-rung">dom</span> : null}
      </div>
      <div className="term-host" ref={hostRef} />
      {report ? <pre className="term-report">{report}</pre> : null}
    </div>
  )
}
