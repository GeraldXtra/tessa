import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

export const SHELL_IDS = ['powershell', 'gitbash', 'cmd', 'pwsh', 'wsl'] as const
export type ShellId = (typeof SHELL_IDS)[number]

export interface ShellSpec {
  id: ShellId
  tag: string
  name: string
  version: string
  key: string
  exe: string
  available: boolean
  how: string
  integrated: boolean
}

export interface SpawnPlan {
  exe: string
  args: string[]
  env: Record<string, string>
  integrated: boolean
}

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

function regSubkeys(hive: string, key: string): string[] {
  try {
    const out = execFileSync('reg', ['query', `${hive}\\${key}`], {
      encoding: 'utf8',
      windowsHide: true,
      timeout: 4000,
    })
    return out
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l.toUpperCase().startsWith(`${hive}\\${key}\\`.toUpperCase()))
  } catch {
    return []
  }
}

export function onPath(exeName: string): string {
  const dirs = (process.env['PATH'] ?? '').split(';').map((d) => d.trim()).filter(Boolean)
  for (const d of dirs) {
    const p = join(d, exeName)
    if (existsSync(p)) return p
  }
  return ''
}

function majorMinor(v: string): string {
  const m = v.match(/^(\d+)\.(\d+)/)
  return m ? `${m[1]}.${m[2]}` : ''
}

function findGitBash(): { exe: string; how: string; version: string } {
  for (const hive of ['HKLM', 'HKCU']) {
    const root = regValue(hive, 'SOFTWARE\\GitForWindows', 'InstallPath')
    if (root) {
      const exe = join(root, 'bin', 'bash.exe')
      if (existsSync(exe)) {
        return {
          exe,
          how: `registry ${hive}\\SOFTWARE\\GitForWindows`,
          version: majorMinor(regValue(hive, 'SOFTWARE\\GitForWindows', 'CurrentVersion')),
        }
      }
    }
  }
  const git = onPath('git.exe')
  if (git) {
    for (const up of [2, 3]) {
      let root = git
      for (let i = 0; i < up; i++) root = dirname(root)
      const exe = join(root, 'bin', 'bash.exe')
      if (existsSync(exe)) return { exe, how: `derived from git on PATH (${git})`, version: '' }
    }
  }
  const candidates = [
    join(process.env['ProgramFiles'] ?? 'C:\\Program Files', 'Git', 'bin', 'bash.exe'),
    join(process.env['LOCALAPPDATA'] ?? '', 'Programs', 'Git', 'bin', 'bash.exe'),
  ]
  for (const exe of candidates) {
    if (exe && existsSync(exe)) return { exe, how: `standard location ${exe}`, version: '' }
  }
  return { exe: '', how: 'not found in the registry, on PATH, or in the standard locations', version: '' }
}

function findWindowsPowerShell(): { exe: string; how: string; version: string } {
  const exe = join(process.env['SystemRoot'] ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
  if (!existsSync(exe)) return { exe: '', how: 'powershell.exe not found', version: '' }
  const v = regValue('HKLM', 'SOFTWARE\\Microsoft\\PowerShell\\3\\PowerShellEngine', 'PowerShellVersion')
  return { exe, how: 'Windows PowerShell (in-box)', version: majorMinor(v) }
}

function findPwsh(): { exe: string; how: string; version: string } {
  const fixed = join(process.env['ProgramFiles'] ?? 'C:\\Program Files', 'PowerShell', '7', 'pwsh.exe')
  if (existsSync(fixed)) return { exe: fixed, how: 'Program Files\\PowerShell\\7', version: '7' }
  const p = onPath('pwsh.exe')
  if (p) return { exe: p, how: `pwsh.exe on PATH (${p})`, version: '' }
  return { exe: '', how: 'pwsh.exe not in Program Files\\PowerShell\\7 or on PATH', version: '' }
}

function findCmd(): { exe: string; how: string } {
  const exe = process.env['COMSPEC'] ?? join(process.env['SystemRoot'] ?? 'C:\\Windows', 'System32', 'cmd.exe')
  return existsSync(exe) ? { exe, how: '%COMSPEC%' } : { exe: '', how: 'cmd.exe not found' }
}

function findWsl(): { exe: string; how: string } {
  const exe = join(process.env['SystemRoot'] ?? 'C:\\Windows', 'System32', 'wsl.exe')
  if (!existsSync(exe)) return { exe: '', how: 'wsl.exe not found' }
  const distros = regSubkeys('HKCU', 'Software\\Microsoft\\Windows\\CurrentVersion\\Lxss')
    .map((k) => regValue('HKCU', k.slice('HKCU\\'.length), 'DistributionName'))
    .filter(Boolean)
  const usable = distros.filter((d) => !/^docker-desktop/i.test(d))
  if (!usable.length) {
    return {
      exe: '',
      how: distros.length
        ? `wsl.exe present, but the only distributions are ${distros.join(', ')}`
        : 'wsl.exe present, but no distribution is installed',
    }
  }
  return { exe, how: `WSL distributions: ${usable.join(', ')}` }
}

export function resolveShells(): ShellSpec[] {
  const ps = findWindowsPowerShell()
  const bash = findGitBash()
  const cmd = findCmd()
  const pwsh = findPwsh()
  const wsl = findWsl()
  return [
    { id: 'powershell', tag: 'PS', name: 'PowerShell', version: ps.version, key: '1', exe: ps.exe, available: !!ps.exe, how: ps.how, integrated: true },
    { id: 'gitbash', tag: 'BASH', name: 'Git Bash', version: bash.version, key: '2', exe: bash.exe, available: !!bash.exe, how: bash.how, integrated: true },
    { id: 'cmd', tag: 'CMD', name: 'Command Prompt', version: '', key: '3', exe: cmd.exe, available: !!cmd.exe, how: cmd.how, integrated: true },
    { id: 'pwsh', tag: 'PWSH', name: 'PowerShell 7', version: '', key: '4', exe: pwsh.exe, available: !!pwsh.exe, how: pwsh.how, integrated: false },
    { id: 'wsl', tag: 'WSL', name: 'WSL', version: '', key: '5', exe: wsl.exe, available: !!wsl.exe, how: wsl.how, integrated: false },
  ]
}

export function isShellId(v: unknown): v is ShellId {
  return typeof v === 'string' && (SHELL_IDS as readonly string[]).includes(v)
}

export function shellScriptDir(isPackaged: boolean, resourcesPath: string, mainDir: string): string {
  return isPackaged ? join(resourcesPath, 'shell') : resolve(mainDir, '..', '..', 'resources', 'shell')
}

export function planSpawn(
  spec: ShellSpec,
  scriptDir: string,
  extraEnv: Record<string, string>,
  log: (m: string) => void,
): SpawnPlan {
  const env = { ...extraEnv }
  if (spec.id === 'powershell') {
    const file = join(scriptDir, 'tessa.ps1')
    if (existsSync(file)) {
      const quoted = file.replace(/'/g, "''")
      return {
        exe: spec.exe,
        args: ['-NoLogo', '-NoExit', '-Command', `. ([ScriptBlock]::Create([System.IO.File]::ReadAllText('${quoted}')))`],
        env,
        integrated: true,
      }
    } else {
      log(`shell script missing (${file}) - PowerShell opens without command lines`)
      return {
        exe: spec.exe,
        args: ['-NoLogo', '-NoExit', '-Command', '[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();[Console]::InputEncoding=[System.Text.UTF8Encoding]::new();$OutputEncoding=[System.Text.UTF8Encoding]::new()'],
        env,
        integrated: false,
      }
    }
  }
  if (spec.id === 'gitbash') {
    const file = join(scriptDir, 'tessa.bash')
    if (existsSync(file)) {
      return { exe: spec.exe, args: ['--init-file', file.replace(/\\/g, '/'), '-i'], env: { ...env, TESSA_SHELL_LOGIN: '1' }, integrated: true }
    }
    log(`shell script missing (${file}) - Git Bash opens without command lines`)
    return { exe: spec.exe, args: ['--login', '-i'], env, integrated: false }
  }
  if (spec.id === 'cmd') {
    const file = join(scriptDir, 'tessa.cmd')
    if (existsSync(file)) {
      return { exe: spec.exe, args: ['/K', file], env: { ...env, TESSA_OWN_PROMPT: process.env['PROMPT'] ?? '$P$G' }, integrated: true }
    }
    log(`shell script missing (${file}) - Command Prompt opens without command lines`)
    return { exe: spec.exe, args: ['/K', 'chcp 65001>nul'], env, integrated: false }
  }
  if (spec.id === 'pwsh') return { exe: spec.exe, args: ['-NoLogo'], env, integrated: false }
  return { exe: spec.exe, args: [], env, integrated: false }
}

export function describeShell(s: ShellSpec): string {
  return `${s.id} (${s.name}${s.version ? ` ${s.version}` : ''}) -> ${s.exe ? resolve(s.exe) : 'NOT FOUND'} [${s.how}]`
}
