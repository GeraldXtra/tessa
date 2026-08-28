/**
 * apps/console/src/main/pty-host.ts — supervises the PTY host and owns rung selection.
 *
 * Main never sees terminal bytes. It forks the host, runs the Worker probe,
 * mints a MessageChannel, and hands one end to the host and the other to the
 * renderer. After that the bytes flow host → MessagePort → renderer directly.
 * That is CONTRACT §4.2's "the Console owns the byte stream", applied one level
 * down: even inside the Console, the byte stream avoids the coordinating process.
 */

import { execFile } from 'node:child_process'
import { join, sep } from 'node:path'
import { app, MessageChannelMain, utilityProcess, type BrowserWindow, type UtilityProcess } from 'electron'
import type { HostToMain, MainToHost, PtyHostKind } from '../shared/pty-ipc.ts'
import { PTY_PORT_CHANNEL } from '../shared/pty-ipc.ts'

export interface PtyHostResult {
  /** The daemon's sessionId for this PTY. Every report is keyed by it. */
  sessionId?: string
  kind: PtyHostKind
  workerOk: boolean
  probeMs: number
  probeError?: string
  /** REQUIRED. `startPty` throws rather than returning without an observed pid. */
  pid: number
}

const PROBE_TIMEOUT_MS = 10_000
/**
 * How long to wait for the host's `spawned` message before giving up.
 *
 * Measured context: a healthy spawn on this machine reports back well inside a
 * second (worker probe 111–233 ms, then the ConPTY spawn itself). 5 s is ~20x
 * that, so a timeout here means something is genuinely wrong, not merely slow —
 * and it must stay comfortably under the daemon's 30 s grant TTL so the grant is
 * released deliberately by `startFailed` rather than expiring silently.
 */
const SPAWN_TIMEOUT_MS = 5_000
const SHUTDOWN_GRACE_MS = 1_500

/* ── kill-ladder deadlines, each one measured rather than assumed ─────────── */

/** Poll cadence for observing death. `process.kill(pid, 0)` costs ~1 µs. */
const POLL_INTERVAL_MS = 15
/** Rung 1: host reaps its own shell. Deadline == the existing shutdown grace. */
const RUNG1_DEADLINE_MS = SHUTDOWN_GRACE_MS
/** Rung 2: taskkill /F /T is synchronous-ish; a second is generous. */
const RUNG2_DEADLINE_MS = 1_000
/** Rung 3: teardown is not instantaneous. Settle, then look again. */
const RUNG3_DEADLINE_MS = 700

/**
 * The live host, tracked so it can be torn down on quit.
 *
 * This matters more on Windows than it looks. Killing the host process does NOT
 * reap the PTY's children: `cmd.exe` and its `conhost.exe` survive as orphans.
 * Verified during Step 2 — a force-kill of Electron left 2 cmd.exe and 4
 * conhost.exe behind. The shell must be told to die, in order, before the host
 * goes away.
 */
let activeChild: UtilityProcess | null = null

/**
 * What a revoke has to be able to kill.
 *
 * The host alone is not enough. On Windows the PTY's shell is NOT a child of the
 * host in any way that dies with it, and conhost is a third process again — so a
 * revoke that only kills the host leaks both.
 *
 * `conhostPids` is captured as a BEFORE/AFTER delta around the spawn rather than
 * derived from a parent-pid relationship, because ConPTY's console host is not
 * reliably parented to anything we own.
 */
interface SessionRecord {
  host: UtilityProcess
  /** The utilityProcess's own pid. ConPTY parents conhost to IT, not to the shell. */
  hostPid?: number
  shellPid?: number
}

const sessionHosts = new Map<string, SessionRecord>()

/**
 * Existence test.
 *
 * Signal 0 sends nothing. On Windows libuv implements it as
 * `GetExitCodeProcess() != STILL_ACTIVE -> ESRCH`, so this is a genuine liveness
 * test and not merely "a handle could be opened" — a terminated process whose
 * handle is still held by a parent reads as DEAD here, which is what we want.
 * EPERM means the process exists but is not ours to signal: still alive.
 *
 * It is also ~1 µs, which is why it, and not tasklist, drives the poll loop.
 * `tasklistAlive()` is the independent second instrument at the decision point.
 */
function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'EPERM'
  }
}

/** PIDs from tasklist. ~30 ms per call — spawn and decision points only. */
function tasklistPids(filter: string[]): Promise<number[]> {
  return new Promise((resolve) => {
    execFile(
      'tasklist',
      [...filter, '/FO', 'CSV', '/NH'],
      { windowsHide: true, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err || !stdout) return resolve([])
        const pids: number[] = []
        for (const line of stdout.split('\n')) {
          const m = line.match(/^"[^"]+","(\d+)"/)
          if (m?.[1]) pids.push(Number(m[1]))
        }
        resolve(pids)
      },
    )
  })
}

/**
 * Second, INDEPENDENT liveness instrument — a different syscall path entirely.
 *
 * Four things on this project have been declared verified against a broken
 * instrument. Before `killed` is asserted to a tamper-evident log, the verdict
 * is confirmed by a mechanism that shares no code with the one that produced it.
 *
 * PER-PID filters, not one full dump. Measured on this machine: the full table
 * costs 4.5 s idle and was seen at 9.5 s during a teardown, while a `PID eq N`
 * filter costs ~280–510 ms. Multiple `/FI "PID eq"` filters AND together rather
 * than OR, so a batch is genuinely one call per pid — run concurrently.
 */
async function tasklistAlive(pids: number[]): Promise<number[]> {
  if (pids.length === 0) return []
  const found = await Promise.all(
    pids.map(async (p) => ((await tasklistPids(['/FI', `PID eq ${p}`])).includes(p) ? p : null)),
  )
  return found.filter((p): p is number => p !== null)
}

/** taskkill /F /T — the whole tree. Returns every pid it CLAIMED to terminate. */
function taskkillTree(pid: number): Promise<number[]> {
  return new Promise((resolve) => {
    execFile(
      'taskkill',
      ['/F', '/T', '/PID', String(pid)],
      { windowsHide: true },
      (_err, stdout) => {
        const claimed = new Set<number>()
        for (const m of String(stdout ?? '').matchAll(/PID (\d+)/g)) {
          if (m[1]) claimed.add(Number(m[1]))
        }
        resolve([...claimed])
      },
    )
  })
}

/**
 * Every transitive descendant of `roots`, via wmic.
 *
 * Used to widen the observation set BEFORE the ladder claims anything: the
 * grandchild (`cmd.exe` -> `ping.exe`) is otherwise invisible to main, and an
 * unobserved survivor is exactly the failure this whole file exists to prevent.
 *
 * wmic is deprecated on Windows 11 and will eventually be absent. That is
 * tolerated rather than depended on: this returns `[]` if the tool is missing,
 * and `taskkill /T`'s own claimed-pid list (which ships with every Windows)
 * still widens the set. Measured at ~570 ms for the full table, so it runs
 * CONCURRENTLY with rung 1 rather than delaying the kill.
 */
function enumerateDescendants(roots: number[]): Promise<number[]> {
  return new Promise((resolve) => {
    execFile(
      'wmic',
      ['process', 'get', 'ProcessId,ParentProcessId', '/format:csv'],
      { windowsHide: true, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => {
        if (err || !stdout) return resolve([])
        const lines = stdout.split('\n').map((l) => l.trim()).filter(Boolean)
        const header = lines.find((l) => l.toLowerCase().includes('processid'))
        if (!header) return resolve([])
        const cols = header.split(',').map((c) => c.trim().toLowerCase())
        const iParent = cols.indexOf('parentprocessid')
        const iPid = cols.indexOf('processid')
        if (iParent < 0 || iPid < 0) return resolve([])

        const children = new Map<number, number[]>()
        for (const line of lines) {
          if (line === header) continue
          const f = line.split(',')
          const parent = Number(f[iParent])
          const pid = Number(f[iPid])
          if (!Number.isInteger(parent) || !Number.isInteger(pid)) continue
          const bucket = children.get(parent)
          if (bucket) bucket.push(pid)
          else children.set(parent, [pid])
        }

        const out = new Set<number>()
        const queue = [...roots]
        while (queue.length) {
          const cur = queue.shift() as number
          for (const kid of children.get(cur) ?? []) {
            if (out.has(kid) || roots.includes(kid)) continue
            out.add(kid)
            queue.push(kid)
          }
        }
        resolve([...out])
      },
    )
  })
}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

/**
 * Poll until every target is gone or the deadline expires.
 *
 * Records the FIRST instant each pid was observed dead, in ms from `t0`, so the
 * reported latency is when death was observed rather than when the rung that
 * caused it happened to finish.
 */
async function pollForDeath(
  targets: number[],
  deaths: Map<number, number>,
  t0: number,
  deadlineMs: number,
): Promise<number[]> {
  for (;;) {
    const alive = targets.filter(pidAlive)
    for (const pid of targets) {
      if (!alive.includes(pid) && !deaths.has(pid)) deaths.set(pid, Date.now() - t0)
    }
    if (alive.length === 0) return []
    if (Date.now() - t0 >= deadlineMs) return alive
    await sleep(POLL_INTERVAL_MS)
  }
}

function log(msg: string): void {
  console.log(`[pty-host] ${msg}`)
}

/**
 * Graceful teardown of EVERY PTY, not just the last one.
 *
 * THE BUG THIS REPLACES, MEASURED
 *
 * `activeChild` is a single module-level variable, overwritten on every spawn.
 * With one terminal that was the whole world. With eight panes across four tabs
 * there are EIGHT hosts, and this function tore down exactly one of them — the
 * most recent. The other seven died only as a side effect of the Electron
 * process exiting, and that is a race conhost.exe can win: measured, one
 * `conhost.exe --headless --width 188` survived a full close with its parent
 * already gone.
 *
 * ConPTY parents its console host to the process that CREATED the pseudoconsole
 * — the PTY host, not the shell — so reaping conhost means killing the HOST's
 * tree, which is what `taskkillTree(hostPid)` does. That was already the rung-2
 * logic in `killPtyObserved` for the revoke path; the quit path never used it.
 *
 * Order matters: ask every host to kill its shell first and let them all work in
 * parallel, THEN sweep the trees. Sequential teardown of eight PTYs would spend
 * eight grace periods and Windows does not wait politely for a quitting app.
 */
export async function shutdownPtyHost(): Promise<void> {
  const records = [...sessionHosts.entries()]
  const extra = activeChild && !records.some(([, r]) => r.host === activeChild)
    ? [['<unregistered>', { host: activeChild, hostPid: activeChild.pid, shellPid: undefined }] as const]
    : []
  const all = [...records, ...extra]
  if (all.length === 0) {
    activeChild = null
    return
  }
  log(`tearing down ${all.length} PTY host(s)`)

  // 1. Ask them all to die, at once.
  for (const [, rec] of all) {
    try {
      rec.host.postMessage({ t: 'shutdown' })
    } catch {
      /* already gone */
    }
  }

  // 2. One shared grace period, not one each.
  await Promise.all(
    all.map(
      ([, rec]) =>
        new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, SHUTDOWN_GRACE_MS)
          rec.host.once('exit', () => {
            clearTimeout(timer)
            resolve()
          })
        }),
    ),
  )

  // 3. Sweep each host TREE. This is the line that reaps conhost — killing the
  //    host process alone leaves it, because conhost is its child and Windows
  //    does not cascade.
  const swept: number[] = []
  await Promise.all(
    all.map(async ([, rec]) => {
      const hostPid = rec.hostPid
      if (hostPid === undefined) return
      try {
        const claimed = await taskkillTree(hostPid)
        swept.push(...claimed)
      } catch {
        /* the tree was already gone, which is the good case */
      }
    }),
  )

  // 4. Belt and braces: kill any host object still holding on.
  for (const [, rec] of all) {
    try {
      rec.host.kill()
    } catch {
      /* already exited */
    }
  }

  sessionHosts.clear()
  activeChild = null
  log(`host teardown complete — ${all.length} host(s), taskkill claimed ${swept.length} pid(s)`)
}

/**
 * Fork the utilityProcess and wait for its Worker probe verdict.
 *
 * Resolves with the child and the verdict. The child is NOT killed on a failed
 * probe — main decides what to do, and killing here would race that decision.
 */
/**
 * The utilityProcess entry script, resolved for BOTH layouts.
 *
 * In development `__dirname` is `apps/console/out/main` and `pty-host.js` sits
 * beside `index.js`, exactly as the second rollup input puts it. In a packaged
 * build `__dirname` is `...\resources\app.asar\out\main` — and this host cannot
 * run from there. TWO separate reasons, and both have to hold:
 *
 *   1. `pty-host.js` does `require('@lydell/node-pty')`, and a native `.node`
 *      binary cannot be loaded out of an asar archive at all. electron-builder
 *      therefore unpacks the addon to
 *      `resources\app.asar.unpacked\node_modules\@lydell\...`.
 *
 *   2. Node resolves that `require` by walking up from the DIRECTORY OF THE
 *      SCRIPT. A host running from inside `app.asar\out\main` walks
 *      `app.asar\node_modules` and finds the PACKED copy — the one that cannot
 *      load. Only a host running from `app.asar.unpacked\out\main` walks
 *      `app.asar.unpacked\node_modules` and reaches the unpacked one.
 *
 * Point 2 is the part that is easy to miss: unpacking the addon is necessary
 * and NOT sufficient. So `out/main/pty-host.js` is itself listed in
 * `asarUnpack` (see electron-builder.yml) and the path is rewritten to match.
 *
 * The rewrite is a plain string swap because that is precisely what
 * electron-builder guarantees: `app.asar.unpacked` is a sibling directory whose
 * internal shape is identical to the archive's. `sep` is used rather than a
 * literal so the match cannot accidentally fire on a path fragment.
 *
 * WHY NOT JUST `asar: false`. Turning the archive off entirely would work and
 * would also unpack ~1,900 renderer and source files onto disk as loose files,
 * slowing first launch and making the install directory trivially editable.
 * Two unpack entries are the smaller, more honest change.
 */
function ptyHostEntry(): string {
  const entry = join(__dirname, 'pty-host.js')
  if (!app.isPackaged) return entry
  return entry.replace(`app.asar${sep}`, `app.asar.unpacked${sep}`)
}

function forkAndProbe(): Promise<{ child: UtilityProcess; probe: Extract<HostToMain, { t: 'probe' }> }> {
  return new Promise((resolve, reject) => {
    const entry = ptyHostEntry()

    const child = utilityProcess.fork(entry, [], {
      serviceName: 'tessa-pty-host',
      // stdio inherit so a native-module load failure prints somewhere visible
      // instead of vanishing.
      stdio: 'inherit',
    })

    const timer = setTimeout(() => {
      reject(new Error(`pty-host did not report a probe verdict within ${PROBE_TIMEOUT_MS}ms`))
    }, PROBE_TIMEOUT_MS)

    child.on('message', (msg: HostToMain) => {
      if (msg?.t === 'probe') {
        clearTimeout(timer)
        resolve({ child, probe: msg })
      } else if (msg?.t === 'log') {
        log(msg.message)
      } else if (msg?.t === 'spawn-failed') {
        log(`spawn-failed: ${msg.message}`)
      }
    })

    child.on('exit', (code) => {
      clearTimeout(timer)
      reject(new Error(`pty-host exited before probing (code ${code})`))
    })
  })
}

/**
 * Start a PTY and wire it straight to the renderer.
 *
 * THE GATE: if the Worker probe fails, this throws with the reason rather than
 * limping on. Rungs 2 and 3 (main-process host, forked Node with
 * ELECTRON_RUN_AS_NODE=1) plug in here and speak the identical MessagePort
 * protocol — see src/shared/pty-ipc.ts. They are deliberately not written yet:
 * the probe tells us empirically whether they are needed, and writing an
 * untested fallback for a problem this machine may not have is waste.
 */
export async function startPty(
  win: BrowserWindow,
  opts: {
    shell: string
    args: string[]
    cwd: string
    cols: number
    rows: number
    sessionId?: string
    /** DEV HARNESS ONLY — see pty-ipc.ts. */
    stallSpawnMs?: number
    /** DEV HARNESS ONLY — see pty-ipc.ts. */
    capturePath?: string
  },
): Promise<PtyHostResult> {
  const { child, probe } = await forkAndProbe()
  activeChild = child

  log(
    probe.workerOk
      ? `worker_threads probe PASSED in utilityProcess (${probe.ms.toFixed(0)} ms)`
      : `worker_threads probe FAILED in utilityProcess: ${probe.error}`,
  )

  if (!probe.workerOk) {
    activeChild = null
    child.kill()
    throw new Error(
      `utilityProcess cannot host node-pty (worker_threads unavailable: ${probe.error}). ` +
        `Drop to rung 2 (main-process host) or rung 3 (fork with ELECTRON_RUN_AS_NODE=1); ` +
        `both speak the same MessagePort protocol.`,
    )
  }

  // Mint the channel: one end to the host, the other to the renderer.
  const { port1, port2 } = new MessageChannelMain()

  const spawnMsg: MainToHost = {
    t: 'spawn',
    shell: opts.shell,
    args: opts.args,
    cwd: opts.cwd,
    cols: opts.cols,
    rows: opts.rows,
    ...(opts.stallSpawnMs === undefined ? {} : { stallSpawnMs: opts.stallSpawnMs }),
    ...(opts.capturePath === undefined ? {} : { capturePath: opts.capturePath }),
  }
  child.postMessage(spawnMsg, [port1])

  /*
   * NEVER RESOLVE WITHOUT AN OBSERVED PID.
   *
   * This previously resolved `undefined` on timeout AND on spawn-failed, and the
   * caller then reported `cmd.pty.report{started}` regardless — redeeming a §6.5
   * grant for a PTY that might not exist. That is the same defect as the false
   * `killed`: an audit entry asserting something nobody observed. The audit log
   * does not get to be optimistic.
   *
   * So both failure paths throw, and the caller's existing catch reports
   * `startFailed`, which is exactly the enum value that exists to reclaim a
   * grant for a PTY that never came up.
   *
   * The host is torn down on the way out. On a TIMEOUT the shell may still be
   * mid-spawn and appear a moment later; without this it would survive as an
   * unauthorized PTY whose grant we just released — the worst of both.
   */
  const spawned = await new Promise<{ ok: true; pid: number } | { ok: false; reason: string }>(
    (resolve) => {
      const timer = setTimeout(
        () => resolve({ ok: false, reason: `host sent no 'spawned' within ${SPAWN_TIMEOUT_MS} ms` }),
        SPAWN_TIMEOUT_MS,
      )
      child.on('message', (msg: HostToMain) => {
        if (msg?.t === 'spawned') {
          clearTimeout(timer)
          resolve({ ok: true, pid: msg.pid })
        } else if (msg?.t === 'spawn-failed') {
          clearTimeout(timer)
          resolve({ ok: false, reason: msg.message })
        }
      })
      child.once('exit', (code) => {
        clearTimeout(timer)
        resolve({ ok: false, reason: `pty-host exited during spawn (code ${code})` })
      })
    },
  )

  if (!spawned.ok) {
    log(`spawn FAILED: ${spawned.reason} — tearing down the host, NOT reporting started`)
    activeChild = null
    try {
      child.kill()
    } catch {
      /* already gone */
    }
    throw new Error(spawned.reason)
  }
  const pid = spawned.pid

  // Hand the renderer its end. From here main is out of the data path.
  //
  // THE PAYLOAD NAMES THE SESSION, and with panes it must. This used to send
  // `null`: with one terminal the renderer could take whichever port arrived,
  // because there was only ever one. With eight panes eight ports arrive and
  // every pane's listener sees all of them, so an unlabelled port is a race —
  // and the prize for losing it is typing into someone else's shell.
  win.webContents.postMessage(PTY_PORT_CHANNEL, { sessionId: opts.sessionId ?? '' }, [port2])

  // NOTHING is enumerated here on purpose.
  //
  // An earlier version identified this session's conhost.exe by a before/after
  // name delta around the spawn. It cost three `tasklist` calls on the spawn hot
  // path, and under load that delay outlived the daemon's 30 s grant TTL — the
  // grant expired before the PTY finished starting. It was also guesswork: our
  // own `tasklist` invocations are console apps that briefly own a conhost each,
  // so the delta had to be double-sampled just to exclude its own instrument.
  //
  // conhost is identified at REVOKE time instead, by PARENTAGE — measured twice
  // on this machine, ConPTY parents it to the PTY host (20184 -> 21940,
  // 7864 -> 1684), never to the shell. That is exact, costs nothing until a
  // revoke actually happens, and the ladder already awaits that walk.
  if (opts.sessionId) {
    sessionHosts.set(opts.sessionId, { host: child, hostPid: child.pid, shellPid: pid })
  }

  return {
    kind: 'utilityProcess',
    workerOk: true,
    probeMs: probe.ms,
    probeError: probe.error,
    pid,
    ...(opts.sessionId ? { sessionId: opts.sessionId } : {}),
  }
}


/* ═══════════════════════════════════════════════════ DEV HARNESS (Step 5 only) */

/**
 * Type into the live PTY from main. DEV ONLY — see `DevInput` in pty-ipc.ts.
 *
 * Step 5's exit criterion is that npm, pip, git and claude run in the CONSOLE'S
 * OWN terminal. There is no supported way to drive xterm's keyboard from outside
 * the renderer, so the harness writes to the same `term.write()` those keystrokes
 * reach. The PTY, the grant, the ConPTY and the rendering path are all the real
 * ones; only the origin of the bytes differs.
 */
export function devType(sessionId: string, text: string): boolean {
  const rec = sessionHosts.get(sessionId)
  if (!rec) return false
  rec.host.postMessage({ t: 'devInput', b64: Buffer.from(text, 'utf8').toString('base64') })
  return true
}

/** Resize the live PTY from main. DEV ONLY — for the Step 5 resize check. */
export function devResize(sessionId: string, cols: number, rows: number): boolean {
  const rec = sessionHosts.get(sessionId)
  if (!rec) return false
  rec.host.postMessage({ t: 'devResize', cols, rows })
  return true
}

/* ══════════════════════════════════════════ CONTRACT §6.5 lifecycle reporting */

/**
 * Report a PTY lifecycle event to the daemon.
 *
 * The daemon never sees terminal bytes (CONTRACT §4.2), so these five events are
 * the ONLY way its audit trail and session roster stay true. Each one matters
 * for a different reason:
 *
 *   started      — REDEEMS the grant. The daemon refuses a `started` with no
 *                  live grant and answers it with a revoke, so this is what
 *                  actually closes the §6.5 loop rather than merely logging it.
 *   startFailed  — RECLAIMS a grant for a PTY that never came up. Exactly why
 *                  this value was added to the enum pre-approval; without it an
 *                  authorization is stranded and the daemon's view drifts.
 *   exited       — the session ended on its own.
 *   killed       — we killed it, usually because the daemon told us to.
 *   cwdChanged / titleChanged — roster accuracy, so the Orb can render what is
 *                  running without the Console pushing UI state.
 *
 * Never throws: a failed report must not take down a working terminal. It is
 * logged instead, because a silent failure here degrades the audit trail
 * invisibly, which is the one outcome worse than a noisy one.
 */
export async function reportPty(
  client: { isConnected: boolean; request: (t: never, p: unknown) => Promise<unknown> },
  sessionId: string,
  event: 'started' | 'exited' | 'cwdChanged' | 'titleChanged' | 'killed' | 'startFailed',
  detail?: string | number,
): Promise<void> {
  if (!client.isConnected) {
    log(`report ${event} for ${sessionId.slice(0, 8)} dropped — daemon not connected`)
    return
  }
  try {
    await client.request('cmd.pty.report' as never, {
      sessionId,
      event,
      ...(detail === undefined ? {} : { detail }),
    })
  } catch (err) {
    log(`report ${event} failed for ${sessionId.slice(0, 8)}: ${(err as Error).message}`)
  }
}

/** The measured result of a revoke. Every field is observed, none is assumed. */
export interface KillOutcome {
  /** True ONLY if two independent instruments agree every target is gone. */
  observedDead: boolean
  /** The revoke named a session this Console never spawned. Nothing to report. */
  unknownSession?: true
  hostPid?: number
  shellPid?: number
  /** Every pid the verdict actually covers, after widening. */
  observed: number[]
  /** pid -> ms from revoke-received to the FIRST observation of its death. */
  deaths: Record<number, number>
  /** ms until the LAST target was observed dead. Absent if any survived. */
  msToDeath?: number
  survivors: number[]
  rungs: string[]
}

/**
 * Honour `evt.pty.revoke` (CONTRACT §4.2) — and do NOT lie about the result.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THE INVARIANT THIS FUNCTION EXISTS TO HOLD
 *
 *   `cmd.pty.report{killed}` is emitted ONLY after the process is OBSERVED DEAD.
 *
 * Not on a timer, not because `kill()` returned, not because the host exited.
 * The previous version reported `killed` unconditionally and the daemon audited
 * it at seq 134 and 140 while pid 14764 was still running. The hash chain was
 * intact and the ENTRY WAS FALSE — a tamper-evident log asserting something that
 * did not happen is a worse defect than the leaked process, because it makes the
 * record untrustworthy rather than merely incomplete.
 *
 * So this returns an outcome and the CALLER decides what to report. A survivor
 * produces a loud log and NO audit entry, because CONTRACT's PtyReportEvent is a
 * closed enum with no value meaning "ordered to kill, could not comply" — and
 * inventing one is a breaking change that is Gerald's to rule on, not mine.
 * ─────────────────────────────────────────────────────────────────────────────
 */
export async function killPtyObserved(sessionId: string): Promise<KillOutcome> {
  const rec = sessionHosts.get(sessionId)
  const rungs: string[] = []
  if (!rec) {
    // NOT `observedDead: true`. We observed nothing. Reporting `killed` for a
    // session this Console never spawned would put a second false entry in the
    // audit log for the sake of a tidier return value.
    return {
      observedDead: false,
      unknownSession: true,
      observed: [],
      deaths: {},
      survivors: [],
      rungs: ['no such session in this Console — nothing killed, nothing observed'],
    }
  }

  const t0 = Date.now()
  const { host, hostPid, shellPid } = rec
  const deaths = new Map<number, number>()

  /**
   * MUTABLE on purpose. `pollForDeath` re-reads it every iteration, so a pid
   * discovered mid-ladder joins the observation set immediately instead of
   * being silently excluded from the verdict.
   */
  const targets: number[] = shellPid === undefined ? [] : [shellPid]
  const widen = (pids: number[], source: string): void => {
    const fresh = pids.filter((p) => p > 0 && p !== hostPid && !targets.includes(p))
    if (fresh.length === 0) return
    targets.push(...fresh)
    rungs.push(`observation set widened by ${source}: +[${fresh.join(', ')}]`)
  }

  let hostExitMs: number | null = null
  host.once('exit', () => {
    hostExitMs = Date.now() - t0
  })
  // The host parses `taskkill /T`'s output and tells us every pid it claimed to
  // terminate. A claim widens what we must OBSERVE; it never substitutes for it.
  host.on('message', (msg: HostToMain) => {
    if (msg?.t === 'reaped') widen(msg.pids, 'host taskkill /T claim')
  })
  // Started NOW so it overlaps rung 1 — the enumeration must not delay the
  // kill. But it is AWAITED before any verdict (see `crossCheck`): measured at
  // 263–818 ms idle, it lost the race against a 515 ms ladder on the first
  // attempt and widened the set only after `killed` had already been reported.
  // An observation set that widens after the verdict is not an observation set.
  const descendants = enumerateDescendants([
    ...(shellPid === undefined ? [] : [shellPid]),
    ...(hostPid === undefined ? [] : [hostPid]),
  ])
  let descendantsMerged = false

  const finish = (survivors: number[]): KillOutcome => {
    sessionHosts.delete(sessionId)
    if (host === activeChild) activeChild = null
    if (hostExitMs !== null) rungs.push(`host process exited at ${hostExitMs} ms`)
    const observedDead = survivors.length === 0
    return {
      observedDead,
      ...(hostPid === undefined ? {} : { hostPid }),
      ...(shellPid === undefined ? {} : { shellPid }),
      observed: [...targets],
      deaths: Object.fromEntries(deaths),
      ...(observedDead && deaths.size > 0 ? { msToDeath: Math.max(...deaths.values()) } : {}),
      survivors,
      rungs,
    }
  }

  /**
   * The gate. BOTH instruments must agree before `killed` may be reported.
   *
   * `tasklistAlive` is a different syscall path from `process.kill(pid, 0)`. If
   * they disagree, the disagreement IS the finding and the verdict is ALIVE —
   * silence from one instrument is never taken as a death certificate. Returns
   * the effective survivor set, which is `[]` only on unanimous agreement.
   */
  const crossCheck = async (survivors: number[]): Promise<number[]> => {
    if (survivors.length > 0) return survivors

    // Barrier: no verdict until the descendant walk has been folded in.
    if (!descendantsMerged) {
      descendantsMerged = true
      const kids = await descendants
      if (kids.length === 0) {
        // Named, not swallowed: without the walk the verdict covers only the
        // shell and whatever taskkill claimed, so conhost and any grandchild
        // would be killed but never OBSERVED. wmic is deprecated on Windows 11
        // and this is the day it goes missing.
        rungs.push('descendant walk returned NOTHING — verdict covers a narrower set than the tree')
      }
      widen(kids, 'wmic descendant walk')
      // Anything discovered late may already be dead; `deaths` then records the
      // instant it was FIRST OBSERVED dead, which is an upper bound on when it
      // actually died. An upper bound is honest; a guess is not.
      const nowAlive = targets.filter(pidAlive)
      for (const pid of targets) {
        if (!nowAlive.includes(pid) && !deaths.has(pid)) deaths.set(pid, Date.now() - t0)
      }
      if (nowAlive.length > 0) {
        rungs.push(`widened set is NOT dead: [${nowAlive.join(', ')}] still alive`)
        return nowAlive
      }
    }

    const stillListed = await tasklistAlive(targets)
    if (stillListed.length === 0) {
      rungs.push(`cross-checked: tasklist agrees all targets are gone (${Date.now() - t0} ms)`)
      return []
    }
    rungs.push(
      `INSTRUMENTS DISAGREE: process.kill(0) says dead, tasklist still lists ` +
        `[${stillListed.join(', ')}] — verdict is ALIVE`,
    )
    for (const pid of stillListed) deaths.delete(pid)
    return stillListed
  }

  // ── rung 1: the host reaps its own shell; we watch the PIDS, not the host ─
  //
  // Deliberately NOT "await host exit". The host exiting is evidence about the
  // host, not about cmd.exe — and conflating the two is exactly how a `killed`
  // got audited at seq 134/140 while pid 14764 was still running.
  try {
    host.postMessage({ t: 'shutdown' })
  } catch {
    /* host already gone; the ladder continues regardless */
  }
  let survivors = await pollForDeath(targets, deaths, t0, RUNG1_DEADLINE_MS)
  rungs.push(
    `rung1 host-shutdown, polled to ${RUNG1_DEADLINE_MS} ms -> ` +
      `${survivors.length === 0 ? 'all gone' : `alive [${survivors.join(', ')}]`} (${Date.now() - t0} ms)`,
  )
  survivors = await crossCheck(survivors)
  if (survivors.length === 0) return finish([])

  // ── rung 2: taskkill /F /T on each survivor, AND on the host's own tree ───
  //
  // The host tree matters for conhost specifically: ConPTY parents its console
  // host to the process that created the pseudoconsole — the PTY host, not the
  // shell. `/T` on the shell reaps grandchildren but would leave conhost behind.
  const killTargets = [...survivors, ...(hostPid === undefined ? [] : [hostPid])]
  rungs.push(
    `rung2 taskkill /F /T on [${killTargets.join(', ')}] ` +
      `(host pid ${hostPid ?? 'n/a'} included so conhost is reaped)`,
  )
  for (const pid of killTargets) widen(await taskkillTree(pid), `rung2 taskkill ${pid} claim`)
  survivors = await pollForDeath(targets, deaths, t0, RUNG1_DEADLINE_MS + RUNG2_DEADLINE_MS)
  rungs.push(
    `rung2 polled -> ${survivors.length === 0 ? 'all gone' : `alive [${survivors.join(', ')}]`} (${Date.now() - t0} ms)`,
  )
  survivors = await crossCheck(survivors)
  if (survivors.length === 0) return finish([])

  // ── rung 3: settle and look again. Teardown is not instantaneous ─────────
  rungs.push(`rung3 settle ${RUNG3_DEADLINE_MS} ms, re-poll [${survivors.join(', ')}]`)
  survivors = await pollForDeath(
    targets,
    deaths,
    t0,
    RUNG1_DEADLINE_MS + RUNG2_DEADLINE_MS + RUNG3_DEADLINE_MS,
  )
  survivors = await crossCheck(survivors)
  if (survivors.length === 0) return finish([])

  try {
    host.kill()
  } catch {
    /* already exited */
  }
  rungs.push(`LADDER EXHAUSTED at ${Date.now() - t0} ms; survivors [${survivors.join(', ')}]`)
  return finish(survivors)
}

/**
 * Kill on revoke, then report ONLY what was observed.
 *
 * The asymmetry is deliberate: a confirmed death is audited, a survivor is not.
 */
export async function killPty(
  client: { isConnected: boolean; request: (t: never, p: unknown) => Promise<unknown> },
  sessionId: string,
  reason: string,
): Promise<KillOutcome> {
  const short = sessionId.slice(0, 8)
  const outcome = await killPtyObserved(sessionId)
  for (const r of outcome.rungs) log(`  ${r}`)
  log(
    `  host=${outcome.hostPid ?? 'n/a'} shell=${outcome.shellPid ?? 'n/a'} ` +
      `OBSERVED SET=[${outcome.observed.join(', ') || 'none'}]`,
  )
  for (const [pid, ms] of Object.entries(outcome.deaths)) {
    log(`  pid ${pid} OBSERVED DEAD at +${ms} ms`)
  }

  if (outcome.unknownSession) {
    log(`revoke for ${short} names no PTY this Console owns — reporting nothing`)
    return outcome
  }

  if (outcome.observedDead) {
    log(
      `session ${short} OBSERVED DEAD (all targets) after ${outcome.msToDeath} ms — ` +
        `NOW reporting killed. Order matters: observation first, report second.`,
    )
    await reportPty(client, sessionId, 'killed', reason)
  } else {
    // Loud, and deliberately NO audit entry. See the note above killPtyObserved.
    log(
      `!! REVOKE NOT SATISFIED for session ${short}: ` +
        `pids [${outcome.survivors.join(', ')}] SURVIVED the full kill ladder. ` +
        `NOT reporting 'killed' — the audit log must not assert a death that did not happen. ` +
        `CONTRACT PtyReportEvent has no value for "ordered to kill, could not comply"; ` +
        `adding one is a breaking change under §7.3 for Gerald to rule on.`,
    )
  }
  return outcome
}
