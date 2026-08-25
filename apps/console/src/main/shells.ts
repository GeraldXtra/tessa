/**
 * apps/console/src/main/shells.ts — which shell, and where is it really.
 *
 * Three shells, because Gerald asked for "all commands that are running on
 * command prompt, powershell and linux, mac os":
 *
 *   powershell  DEFAULT. `clear` works natively, and it is what modern tooling
 *               assumes on Windows.
 *   cmd         `cls`. Kept because some things still only work here.
 *   gitbash     THE ANSWER TO "mac os commands". ls, grep, cat, chmod, find,
 *               sed, awk, head, tail — a real Unix userland (MSYS2), not an
 *               emulation of one.
 *
 * NO ALIAS SHIMS. Defining `ls` as `dir` inside cmd was considered and rejected:
 * the shims break the moment a real flag is used (`ls -la`, `grep -r`), and they
 * teach a vocabulary that fails under pressure. A real bash is honest about what
 * it is.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * GIT BASH IS RESOLVED, NEVER HARD-CODED
 *
 * `C:\Program Files\Git\bin\bash.exe` is only the most common answer. A per-user
 * install lands in %LOCALAPPDATA%\Programs\Git, a portable install lands
 * wherever it was unzipped, and either can move. So it is looked up in the order
 * a human would: the installer's own registry key, then the `git` already on
 * PATH, then the standard locations. Whichever answers first is reported, so a
 * support question is answered by the log rather than by guessing.
 */

import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

export const SHELL_IDS = ['powershell', 'cmd', 'gitbash'] as const
export type ShellId = (typeof SHELL_IDS)[number]

export interface ShellSpec {
  id: ShellId
  /** What the UI calls it. */
  label: string
  /** Absolute path to the executable, or '' when unavailable. */
  exe: string
  args: string[]
  available: boolean
  /** How it was found — registry, PATH, a standard location. For the log. */
  how: string
  /**
   * What this shell EMITS.
   *
   * The terminal always decodes UTF-8 (see Terminal.tsx — bytes reach xterm as
   * a Uint8Array, not a binary string). PowerShell 5.1 and Git Bash emit UTF-8.
   * cmd.exe emits the console's OEM codepage, which on this machine is 850/437,
   * so anything non-ASCII would arrive as the wrong glyph. It is switched to
   * 65001 at spawn, quietly, rather than left to produce mojibake.
   */
  emits: 'utf8' | 'oem->utf8'
}

/** Read a registry value, or '' — never throws. */
function regValue(hive: string, key: string, name: string): string {
  try {
    const out = execFileSync('reg', ['query', `${hive}\\${key}`, '/v', name], {
      encoding: 'utf8',
      windowsHide: true,
      timeout: 4000,
    })
    const m = out.match(new RegExp(`${name}\\s+REG_\\w+\\s+(.+)`))
    return m?.[1]?.trim() ?? ''
  } catch {
    return ''
  }
}

/** Where is `git`, if it is on PATH at all? */
function gitOnPath(): string {
  try {
    const out = execFileSync('where', ['git'], {
      encoding: 'utf8',
      windowsHide: true,
      timeout: 4000,
    })
    return out.split(/\r?\n/).find((l) => l.trim().toLowerCase().endsWith('git.exe'))?.trim() ?? ''
  } catch {
    return ''
  }
}

/** (path, how) for Git Bash, or ('', why-not). */
export function findGitBash(): { exe: string; how: string } {
  // 1. The installer's own key. Most authoritative, and it is what Git for
  //    Windows writes on both machine-wide and per-user installs.
  for (const hive of ['HKLM', 'HKCU']) {
    const root = regValue(hive, 'SOFTWARE\\GitForWindows', 'InstallPath')
    if (root) {
      const exe = join(root, 'bin', 'bash.exe')
      if (existsSync(exe)) return { exe, how: `registry ${hive}\\SOFTWARE\\GitForWindows` }
    }
  }

  // 2. Derive it from the `git` he already has. `<root>\cmd\git.exe` or
  //    `<root>\mingw64\bin\git.exe` both sit a known distance from bin\bash.exe.
  const git = gitOnPath()
  if (git) {
    for (const up of [2, 3]) {
      let root = git
      for (let i = 0; i < up; i++) root = dirname(root)
      const exe = join(root, 'bin', 'bash.exe')
      if (existsSync(exe)) return { exe, how: `derived from git on PATH (${git})` }
    }
  }

  // 3. The usual places, last.
  const candidates = [
    join(process.env['ProgramFiles'] ?? 'C:\\Program Files', 'Git', 'bin', 'bash.exe'),
    join(process.env['ProgramFiles(x86)'] ?? 'C:\\Program Files (x86)', 'Git', 'bin', 'bash.exe'),
    join(process.env['LOCALAPPDATA'] ?? '', 'Programs', 'Git', 'bin', 'bash.exe'),
  ]
  for (const exe of candidates) {
    if (exe && existsSync(exe)) return { exe, how: `standard location ${exe}` }
  }

  return { exe: '', how: 'not found in the registry, on PATH, or in the standard locations' }
}

function findPowerShell(): { exe: string; how: string } {
  // Windows PowerShell 5.1 ships with the OS and is what this machine has.
  // PowerShell 7 (`pwsh.exe`) is preferred when present — it is not, here.
  const pwsh = join(process.env['ProgramFiles'] ?? '', 'PowerShell', '7', 'pwsh.exe')
  if (existsSync(pwsh)) return { exe: pwsh, how: 'PowerShell 7' }
  const ps = join(
    process.env['SystemRoot'] ?? 'C:\\Windows',
    'System32',
    'WindowsPowerShell',
    'v1.0',
    'powershell.exe',
  )
  if (existsSync(ps)) return { exe: ps, how: 'Windows PowerShell 5.1 (in-box)' }
  return { exe: '', how: 'no powershell.exe or pwsh.exe found' }
}

function findCmd(): { exe: string; how: string } {
  const exe = process.env['COMSPEC'] ?? join(process.env['SystemRoot'] ?? 'C:\\Windows', 'System32', 'cmd.exe')
  return existsSync(exe)
    ? { exe, how: exe === process.env['COMSPEC'] ? '%COMSPEC%' : 'System32' }
    : { exe: '', how: 'cmd.exe not found' }
}

/** Every shell, whether or not it is installed. Ordered as the UI shows them. */
export function resolveShells(): ShellSpec[] {
  const ps = findPowerShell()
  const cmd = findCmd()
  const bash = findGitBash()

  return [
    {
      id: 'powershell',
      label: 'PowerShell',
      exe: ps.exe,
      // -NoLogo, and NOT -NoProfile: his profile is his environment, and a
      // terminal that silently ignores it is not his shell.
      //
      // THE ENCODING LINE IS NOT OPTIONAL, AND I HAD THIS WRONG.
      //
      // I first marked PowerShell as emitting UTF-8. Measured on a real PTY it
      // does not: Windows PowerShell 5.1 writes the OEM codepage, so `café`
      // arrived as `caf` + a replacement character and box-drawing and emoji
      // were dropped entirely before they ever reached the terminal. That is a
      // shell-side loss — no amount of correct decoding in the renderer can
      // recover a byte the shell never sent.
      //
      // PowerShell 7 does default to UTF-8, which is why this is written to be
      // harmless there rather than conditional on the version.
      args: [
        '-NoLogo',
        '-NoExit',
        '-Command',
        '[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();' +
          '[Console]::InputEncoding=[System.Text.UTF8Encoding]::new();' +
          '$OutputEncoding=[System.Text.UTF8Encoding]::new()',
      ],
      available: !!ps.exe,
      how: ps.how,
      emits: 'oem->utf8',
    },
    {
      id: 'cmd',
      label: 'Command Prompt',
      exe: cmd.exe,
      // Switch the codepage to UTF-8 on the way in, quietly. Without it cmd
      // emits the OEM codepage and every non-ASCII glyph arrives wrong — the
      // exact mojibake he has already seen once in this project.
      args: ['/K', 'chcp 65001>nul'],
      available: !!cmd.exe,
      how: cmd.how,
      emits: 'oem->utf8',
    },
    {
      id: 'gitbash',
      label: 'Git Bash',
      exe: bash.exe,
      // --login so the MSYS2 PATH is built and the Unix userland is actually on
      // it; -i so it is an interactive shell with a prompt and job control.
      args: ['--login', '-i'],
      available: !!bash.exe,
      how: bash.how,
      emits: 'utf8',
    },
  ]
}

export const DEFAULT_SHELL: ShellId = 'powershell'

/**
 * Pick a shell, falling back rather than failing.
 *
 * Returns what was chosen AND what was asked for, so the caller can tell him
 * plainly that a substitution happened. Silently opening a different shell than
 * the one he clicked would be worse than an error.
 */
export function pickShell(
  wanted: ShellId | undefined,
  shells = resolveShells(),
): { spec: ShellSpec; substituted: boolean; wanted: ShellId; message: string } {
  const want = (wanted && SHELL_IDS.includes(wanted) ? wanted : DEFAULT_SHELL) as ShellId
  const first = shells.find((s) => s.id === want)
  if (first?.available) return { spec: first, substituted: false, wanted: want, message: '' }

  const fallback = shells.find((s) => s.available)
  if (!fallback) {
    return {
      spec: shells[0] as ShellSpec,
      substituted: false,
      wanted: want,
      message: 'No shell could be found on this machine — not even cmd.exe. Nothing can be spawned.',
    }
  }
  return {
    spec: fallback,
    substituted: true,
    wanted: want,
    message:
      `${first?.label ?? want} is not installed on this machine ` +
      `(${first?.how ?? 'not found'}). Opening ${fallback.label} instead.`,
  }
}

/** Absolute, normalised, for the audit line and the log. */
export function describeShell(s: ShellSpec): string {
  return `${s.id} (${s.label}) -> ${s.exe ? resolve(s.exe) : 'UNAVAILABLE'} [${s.how}]`
}
