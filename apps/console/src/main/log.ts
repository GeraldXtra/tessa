/**
 * apps/console/src/main/log.ts — the packaged app's only voice.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHY THIS EXISTS AT ALL
 * ─────────────────────────────────────────────────────────────────────────────
 * Every diagnostic in main is a `console.log`. In development that lands in the
 * terminal `electron-vite dev` was started from, which is why it was never a
 * problem. In a PACKAGED app opened from a Start Menu shortcut there is no
 * terminal at all: Electron's main process is a GUI-subsystem executable, no
 * parent console inherits its stdout, and every line is written to a handle
 * that goes nowhere.
 *
 * That is not a cosmetic loss. `res.hello`, a WebSocket close code, a CONTRACT
 * §6.5 grant id, and a native-module load failure are ALL reported through
 * exactly that channel. Without a file, the first thing that goes wrong in an
 * installed build is also the thing that becomes impossible to see — and the
 * two most likely failures at packaging time (an asar-trapped addon and a
 * rejected handshake) look identical from the outside.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHY IT TEES `console` RATHER THAN INTRODUCING A LOGGER
 * ─────────────────────────────────────────────────────────────────────────────
 * A logger object would mean editing every call site in pty-host.ts,
 * ws-client.ts, grants.ts and index.ts — and those call sites are already
 * correct. They say the right things at the right moments. Wrapping `console`
 * keeps all of them, unchanged, and adds a destination.
 *
 * The original methods are still called, so `npm run dev` behaves exactly as it
 * did before this file existed.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHERE IT WRITES, AND WHY NOT `app.getPath('userData')`
 * ─────────────────────────────────────────────────────────────────────────────
 * `%LOCALAPPDATA%\Tessa\logs\` — the same root as `runtime.json`, the settings
 * file and the Orb's theme, because this is one product with one data
 * directory. `userData` would scatter the Console's diagnostics into
 * `%APPDATA%\Tessa Console\` where nothing else about Tessa lives, and it would
 * move again the moment `productName` changed.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHY THE WRITES ARE SYNCHRONOUS
 * ─────────────────────────────────────────────────────────────────────────────
 * The line worth having is the last one before a crash, and an async queue
 * loses precisely that line. The volume is a few hundred lines per session, not
 * a stream — the PTY's bytes never come through here (CONTRACT §4.2 keeps them
 * off the main process entirely), so there is no hot path to protect.
 */

import { appendFileSync, mkdirSync, renameSync, statSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Rotate at 2 MB, keep one previous file. CLAUDE.md caps `data/` growth on a
 * machine with limited free space; an unbounded log is the easiest way to
 * violate that by accident, and two files bound the worst case at 4 MB.
 */
const MAX_BYTES = 2 * 1024 * 1024

let logPath = ''
let started = false

/** Absolute path of the current log file, or '' before `startFileLog`. */
export function logFilePath(): string {
  return logPath
}

function stamp(): string {
  return new Date().toISOString().replace('T', ' ').replace('Z', '')
}

function fmt(v: unknown): string {
  if (typeof v === 'string') return v
  if (v instanceof Error) return `${v.name}: ${v.message}`
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

function write(level: string, text: string): void {
  if (!logPath) return
  try {
    appendFileSync(logPath, `${stamp()} ${level} ${text}\n`, 'utf8')
  } catch {
    /* A log that throws is worse than a log that is missing a line. */
  }
}

function rotate(): void {
  try {
    if (statSync(logPath).size >= MAX_BYTES) {
      // fs.renameSync maps to MoveFileExW with MOVEFILE_REPLACE_EXISTING on
      // Windows, so an existing .1 is overwritten rather than throwing EEXIST.
      renameSync(logPath, `${logPath}.1`)
    }
  } catch {
    /* No file yet, or it is locked — either way, append and move on. */
  }
}

/**
 * Begin tee-ing `console` to disk. Idempotent; safe to call before `app.whenReady`.
 *
 * `banner` is written first so every session in the file is separable — an
 * installed app that is opened, closed and opened again otherwise produces one
 * undifferentiated stream and the "which run was that?" question has no answer.
 */
export function startFileLog(banner: string): string {
  if (started) return logPath
  started = true

  const base = process.env['LOCALAPPDATA'] ?? process.env['APPDATA'] ?? ''
  if (!base) return ''
  const dir = join(base, 'Tessa', 'logs')
  try {
    mkdirSync(dir, { recursive: true })
  } catch {
    return ''
  }
  logPath = join(dir, 'console.log')
  rotate()

  const tee =
    (level: string, orig: (...a: unknown[]) => void) =>
    (...args: unknown[]): void => {
      try {
        orig(...args)
      } catch {
        /* stdout can be a dead handle in a packaged GUI process. */
      }
      write(level, args.map(fmt).join(' '))
    }

  /* eslint-disable no-console */
  console.log = tee('INFO ', console.log.bind(console))
  console.warn = tee('WARN ', console.warn.bind(console))
  console.error = tee('ERROR', console.error.bind(console))
  /* eslint-enable no-console */

  write('INFO ', `───── ${banner}`)
  return logPath
}
