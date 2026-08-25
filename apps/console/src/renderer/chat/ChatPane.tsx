/**
 * apps/console/src/renderer/chat/ChatPane.tsx — Tessa, typed.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THIS PANE HOSTS HER, NOT A SHELL.
 *
 * It is a CLIENT OF THE DAEMON, not a feature of the terminal. It sends text
 * and renders what comes back on `evt.transcript.message`; every decision
 * about what runs — the router, the tiers, the fence, the memory — happens in
 * core/ where it happens for voice.
 *
 * ONE CONVERSATION, SHARED WITH VOICE. The pane subscribes to the same
 * broadcast a spoken turn produces, so anything he says to the Orb appears
 * here too. That is deliberate and it is what stops there being two Tessas who
 * do not know about each other.
 *
 * PROVENANCE GUTTERS. Every line carries a coloured gutter naming where it came
 * from — his words, hers, program output, external content. Typing is exactly
 * where he would paste something out of a web page without thinking, so the
 * security model is drawn rather than described.
 * ════════════════════════════════════════════════════════════════════════════
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { chordOf, type KeyAction } from '../terminal/keymap.ts'
import { paneMenuHandlers } from '../terminal/Terminal.tsx'

/**
 * LAYOUT CHORDS STILL WORK WHEN SHE HAS FOCUS.
 *
 * Found by running it: with a chat pane focused the harness reported "no
 * focused pane to act on" for every chord, because only a terminal registers a
 * key handler. That is not a harness quirk — it means Ctrl+Shift+W could not
 * close the pane he was typing in, and Ctrl+Tab could not leave it. A pane he
 * cannot get out of by keyboard is a trap.
 *
 * Only LAYOUT actions are intercepted. Copy, paste and select-all are
 * deliberately absent: a textarea already does all three natively, and stealing
 * them would break the one place in this app where normal text editing is the
 * right behaviour.
 */
const LAYOUT_ACTIONS = new Set<KeyAction>([
  'splitRight', 'splitDown', 'closePane',
  'focusLeft', 'focusRight', 'focusUp', 'focusDown',
  'newTab', 'nextTab', 'prevTab', 'zoomPane', 'toggleTree',
  'fontIncrease', 'fontDecrease', 'fontReset',
])

export interface ChatLine {
  id: string
  role: string
  text: string
  /** 'typed' | 'voice' — which surface produced it. */
  via: string
  ts: number
}

export interface ChatPaneProps {
  paneId: string
  focused: boolean
  fontSize: number
  onFocus: (paneId: string) => void
  /** Connection state of the daemon link, for the down-line. */
  connected: boolean
  keymap: Record<string, KeyAction>
  onPaneAction: (paneId: string, action: KeyAction) => boolean
}

/** How her state reads while he waits. */
const STATE_LABEL: Record<string, string> = {
  thinking: 'thinking…',
  working: 'working…',
  listening: 'listening…',
  speaking: 'speaking…',
}

let nextLineId = 0

export function ChatPane({
  paneId,
  focused,
  fontSize,
  onFocus,
  connected,
  keymap,
  onPaneAction,
}: ChatPaneProps): React.JSX.Element {
  const [lines, setLines] = useState<ChatLine[]>([])
  const [draft, setDraft] = useState('')
  const [agentState, setAgentState] = useState('idle')
  const [sending, setSending] = useState(false)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const seen = useRef<Set<string>>(new Set())

  const push = useCallback((role: string, text: string, via: string, id?: string) => {
    // DEDUPED BY messageId. His own line is echoed back by the daemon on the
    // shared broadcast; without this it would appear twice — once optimistically
    // and once from the wire.
    const key = id || `local-${(nextLineId += 1)}`
    if (id) {
      if (seen.current.has(id)) return
      seen.current.add(id)
    }
    setLines((l) => [...l, { id: key, role, text, via, ts: Date.now() }])
  }, [])

  // ── the shared thread ─────────────────────────────────────────────────────
  useEffect(() => {
    const offMsg = window.tessa.onTranscript?.((m) => {
      push(m.role, m.text, m.via, m.messageId)
    })
    const offState = window.tessa.onAgentState?.((s) => setAgentState(s))
    return () => {
      offMsg?.()
      offState?.()
    }
  }, [push])

  // Newest at the bottom, like every chat he has ever used.
  useEffect(() => {
    const el = bodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines, agentState])

  useEffect(() => {
    if (focused) inputRef.current?.focus()
  }, [focused])

  // The menu route needs a handler here too, or Terminal > Close Pane silently
  // does nothing while she has focus.
  useEffect(() => {
    const handler = (cmd: string): void => {
      // ── THE DEV HARNESS MUST REACH HER TOO ──────────────────────────
      //
      // Without this the whole pane is untestable: every chord and every dump
      // routes to the focused pane, and a focused chat pane silently swallowed
      // them. A blind spot in the harness is how a feature ships unproven.
      if (cmd.startsWith('devkey:')) {
        const action = keymapRef.current[cmd.slice(7)]
        if (action && LAYOUT_ACTIONS.has(action)) {
          console.log(`DEVKEY ${cmd.slice(7)} -> ${action} (chat pane)`)
          onPaneAction(paneId, action)
        } else {
          console.log(`DEVKEY ${cmd.slice(7)} -> not a layout action in a chat pane`)
        }
        return
      }
      if (cmd === 'devdump:tree') {
        console.log(`DEVDUMP tree=${window.__tessaTree?.() ?? 'n/a'}`)
        return
      }
      if (cmd.startsWith('devtype:')) {
        // Type into her input exactly as a person would, then send.
        const text = atob(cmd.slice(8))
        console.log(`DEVTYPE chat pane ${text.length} chars`)
        setDraft((d) => d + text.replace(/[\r\n]+$/, ''))
        if (/[\r\n]$/.test(text)) window.setTimeout(() => void sendRef.current?.(), 60)
        return
      }
      if (cmd.startsWith('devdump:')) {
        console.log(`MENU ${cmd} — chat pane, nothing to dump`)
        return
      }
      if (LAYOUT_ACTIONS.has(cmd as KeyAction)) onPaneAction(paneId, cmd as KeyAction)
    }
    paneMenuHandlers.set(paneId, handler)
    return () => {
      paneMenuHandlers.delete(paneId)
    }
  }, [paneId, onPaneAction])

  const keymapRef = useRef(keymap)
  keymapRef.current = keymap
  const sendRef = useRef<(() => Promise<void>) | null>(null)

  const send = useCallback(async () => {
    const text = draft.trim()
    if (!text || sending) return
    setDraft('')
    setSending(true)
    try {
      const r = await window.tessa.agentSend(text)
      if (!r.ok) {
        // HER REGISTER, NOT AN ERROR DIALOG. A daemon that is not running is a
        // fact about the machine, and she says it the way she says anything.
        push(
          'system',
          r.error === 'notConnected'
            ? 'I am not connected to my own daemon, Emperor. Start Tessa Core and ask me again.'
            : `Something went wrong reaching me: ${r.error ?? 'unknown'}.`,
          'typed',
        )
      }
    } catch (err) {
      push('system', `Something went wrong reaching me: ${(err as Error).message}.`, 'typed')
    } finally {
      setSending(false)
    }
  }, [draft, sending, push])

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // LAYOUT FIRST. Split, close, focus, tabs and zoom keep working while he
      // is typing to her; everything else falls through to the textarea, which
      // is why copy and paste behave the way they do in any other input.
      const action = keymap[chordOf(e.nativeEvent)]
      if (action && LAYOUT_ACTIONS.has(action)) {
        e.preventDefault()
        e.stopPropagation()
        onPaneAction(paneId, action)
        return
      }
      // ENTER SENDS, SHIFT+ENTER IS A NEWLINE. Both stop here: letting them
      // bubble would hand the chord to the pane keymap, and Ctrl+Shift+W would
      // close the pane he is typing in.
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        e.stopPropagation()
        void send()
      }
    },
    [send, keymap, onPaneAction, paneId],
  )

  sendRef.current = send

  const busy = agentState !== 'idle'

  return (
    <div
      className="pane chat"
      data-focused={focused ? 'yes' : 'no'}
      onMouseDown={() => onFocus(paneId)}
    >
      <div className="pane-meta">
        <span>TESSA</span>
        <span className="chat-state" data-busy={busy ? 'yes' : 'no'}>
          {connected ? (STATE_LABEL[agentState] ?? 'ready') : 'not connected'}
        </span>
      </div>

      <div className="chat-body" ref={bodyRef} style={{ fontSize }}>
        {lines.length === 0 ? (
          <div className="chat-empty">
            Type to Tessa. She hears the same conversation you speak to the Orb.
          </div>
        ) : null}
        {lines.map((l) => (
          <div key={l.id} className="chat-line" data-role={l.role}>
            {/*
              THE PROVENANCE GUTTER. Four sources, four colours, and the label
              says which. `external` is the one that matters — content she
              fetched on his behalf is never his speech and never hers.
            */}
            <span className="chat-gutter" data-prov={provenanceOf(l.role)} aria-hidden="true" />
            <div className="chat-text">
              <span className="chat-who">
                {l.role === 'user' ? 'you' : l.role === 'system' ? 'tessa core' : 'tessa'}
                {l.via === 'voice' ? ' · spoken' : ''}
              </span>
              {l.text}
            </div>
          </div>
        ))}
        {busy ? (
          <div className="chat-line" data-role="assistant">
            <span className="chat-gutter" data-prov="agent" aria-hidden="true" />
            <div className="chat-text chat-working">{STATE_LABEL[agentState] ?? 'working…'}</div>
          </div>
        ) : null}
      </div>

      <textarea
        ref={inputRef}
        className="chat-input"
        rows={2}
        value={draft}
        placeholder={connected ? 'Ask Tessa…  (Enter sends, Shift+Enter for a new line)' : 'Tessa Core is not running'}
        disabled={!connected}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => onFocus(paneId)}
        spellCheck={false}
      />
    </div>
  )
}

/**
 * CONTRACT §4.1 provenance, mapped to the four gutters TRACE uses.
 *
 * `system` covers the daemon speaking about itself — a connection failure is
 * not Tessa's opinion and should not be dressed as one.
 */
function provenanceOf(role: string): string {
  if (role === 'user') return 'human'
  if (role === 'assistant') return 'agent'
  if (role === 'tool') return 'program'
  if (role === 'external') return 'external'
  return 'program'
}
