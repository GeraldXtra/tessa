/**
 * apps/console/src/renderer/panes/port-broker.ts — one MessagePort per pane.
 *
 * THE RACE THIS EXISTS TO REMOVE
 *
 * Main hands the renderer a `MessagePort` per PTY over a single IPC channel.
 * With one terminal that was unambiguous: one listener, one port, take it.
 *
 * With panes it is a race with a bad prize. Eight panes make eight
 * `startPty` calls, eight ports arrive on the same channel, and every pane's
 * listener sees all of them. A pane that grabbed the wrong one would be wired
 * to another pane's shell — he would type in the left pane and watch the right
 * one answer, and nothing about that failure would point at its cause.
 *
 * So each port now arrives LABELLED with its sessionId, and a pane claims only
 * its own.
 *
 * THE ORDERING PROBLEM, AND WHY THIS IS A BROKER RATHER THAN A LISTENER
 *
 * The port is posted from `startPty` inside main BEFORE the `tessa:pty-start`
 * IPC call resolves — so the port routinely arrives before the pane has been
 * told which sessionId is its own. A pane cannot simply "listen for my id",
 * because at that instant it does not know it.
 *
 * The broker therefore holds both halves: ports that arrived before anyone
 * asked, and askers who arrived before their port. Whichever comes second
 * completes the pair. There is exactly ONE window listener, installed once,
 * rather than one per pane.
 */

import { PTY_PORT_CHANNEL } from '../../shared/pty-ipc.ts'

interface PortMessage {
  channel?: string
  sessionId?: string
}

/** Ports that arrived before their pane knew its sessionId. */
const orphanPorts = new Map<string, MessagePort>()
/** Panes waiting for a port that has not arrived yet. */
const waiting = new Map<string, (port: MessagePort) => void>()

let installed = false

function install(): void {
  if (installed) return
  installed = true
  window.addEventListener('message', (e: MessageEvent) => {
    const data = e.data as PortMessage | string | null
    // The old shape was the bare channel string. Anything that is not the new
    // object shape is ignored rather than guessed at — a mislabelled port is
    // the one thing this module exists to prevent.
    if (!data || typeof data !== 'object' || data.channel !== PTY_PORT_CHANNEL) return
    const port = e.ports[0]
    if (!port) return
    const sid = data.sessionId ?? ''
    const waiter = waiting.get(sid)
    if (waiter) {
      waiting.delete(sid)
      waiter(port)
    } else {
      orphanPorts.set(sid, port)
    }
  })
}

/**
 * INSTALLED AT MODULE LOAD, NOT ON FIRST CLAIM. This line is load-bearing.
 *
 * `install()` used to run only from inside `claimPort`, which looks safe
 * because the orphan bucket exists precisely to hold a port that arrived
 * before anyone asked for it. It is not safe, and the reason is an ordering
 * the bucket cannot help with: a port that arrives before the LISTENER exists
 * is not orphaned, it is DROPPED. `window.postMessage` has no backlog.
 *
 * Terminal.tsx calls `claimPort` only AFTER `await window.tessa.startPty(...)`
 * resolves, and main posts the port BEFORE that IPC call resolves — so for the
 * very first pane of a session there was a window in which no listener
 * existed at all. `claimPort` then waited forever on a port that had already
 * been thrown away.
 *
 * FOUND BY PACKAGING. In development the renderer is served as unbundled ESM
 * over HTTP and the interleaving happened to favour the renderer. In the
 * packaged build the renderer is one preloaded bundle and main wins the race
 * every time: the first terminal spawned a real shell, reported a real pid to
 * the daemon under a real grant — and then displayed nothing at all, for ever.
 * The second pane always worked, because by then the listener existed.
 *
 * Importing this module is now enough to be listening. `install()` remains
 * idempotent and `claimPort` still calls it, so nothing depends on import
 * order for correctness.
 */
install()

/**
 * The port for `sessionId`, whenever it turns up.
 *
 * Never rejects and never times out on its own: the caller owns the deadline,
 * because only the pane knows whether it is still mounted.
 */
export function claimPort(sessionId: string): Promise<MessagePort> {
  install()
  const already = orphanPorts.get(sessionId)
  if (already) {
    orphanPorts.delete(sessionId)
    return Promise.resolve(already)
  }
  return new Promise<MessagePort>((resolve) => {
    waiting.set(sessionId, resolve)
  })
}

/** A pane that unmounted before its port arrived must not leak its slot. */
export function abandon(sessionId: string): void {
  waiting.delete(sessionId)
  const p = orphanPorts.get(sessionId)
  if (p) {
    try {
      p.close()
    } catch {
      /* already gone */
    }
    orphanPorts.delete(sessionId)
  }
}

/** For the self-check line: nothing should be stranded once panes settle. */
export function brokerState(): { orphans: number; waiting: number } {
  return { orphans: orphanPorts.size, waiting: waiting.size }
}
