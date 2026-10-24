import { spawn } from 'node:child_process'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { dirname, isAbsolute, join, resolve, sep } from 'node:path'

import { inOneDrive } from './git.ts'
import { onPath } from './shells.ts'

export type LinkRequest =
  | { kind: 'url'; target: string }
  | { kind: 'path'; target: string; base: string; line?: number; col?: number }

export interface LinkResult {
  ok: boolean
  opened?: 'browser' | 'explorer' | 'editor'
  message: string
}

export interface Launcher {
  openExternal: (url: string) => Promise<void>
  spawnDetached: (exe: string, args: string[], env?: Record<string, string>) => void
}

let editorCache: { exe: string; cli: string } | null | undefined

function findVsCode(): { exe: string; cli: string } | null {
  if (editorCache !== undefined) return editorCache
  const cmd = onPath('code.cmd')
  if (!cmd) {
    editorCache = null
    return null
  }
  try {
    const text = readFileSync(cmd, 'utf8')
    const m = text.match(/"%~dp0\.\.\\Code\.exe"\s+"%~dp0\.\.\\([^"]*cli\.js)"/i)
    const binDir = dirname(cmd)
    const exe = resolve(binDir, '..', 'Code.exe')
    const cli = m?.[1] ? resolve(binDir, '..', m[1]) : ''
    editorCache = cli && existsSync(exe) && existsSync(cli) ? { exe, cli } : null
  } catch {
    editorCache = null
  }
  return editorCache
}

function short(p: string): string {
  return p.length > 80 ? `...${p.slice(-77)}` : p
}

export async function openLink(req: LinkRequest, launcher: Launcher): Promise<LinkResult> {
  if (req.kind === 'url') {
    let u: URL
    try {
      u = new URL(req.target)
    } catch {
      return { ok: false, message: 'That is not a web address. Nothing opened.' }
    }
    if (u.protocol !== 'http:' && u.protocol !== 'https:') {
      return { ok: false, message: `Only http and https open from the Console. ${u.protocol} was not opened.` }
    }
    await launcher.openExternal(u.toString())
    return { ok: true, opened: 'browser', message: `Opened ${short(u.toString())} in your browser.` }
  }

  let p = req.target.trim().replace(/^["']|["']$/g, '')
  if (!p.trim()) return { ok: false, message: 'That link is empty. Nothing opened.' }
  if (/^[a-zA-Z][a-zA-Z0-9+.-]+:/.test(p)) {
    return { ok: false, message: 'That is an address, not a file. Only http and https addresses open from the Console.' }
  }
  const msys = p.match(/^\/([a-zA-Z])(\/.*)?$/)
  if (msys) p = `${msys[1]!.toUpperCase()}:${(msys[2] ?? '/').replace(/\//g, '\\')}`
  p = p.replace(/\//g, '\\')
  if (p.startsWith('\\\\')) return { ok: false, message: 'Network paths do not open from the Console.' }
  if (p.indexOf(':', 2) >= 0) return { ok: false, message: 'That path names a stream or an address. Nothing opened.' }
  if (!isAbsolute(p)) {
    if (!req.base || !isAbsolute(req.base)) return { ok: false, message: 'No folder to resolve that path against. Nothing opened.' }
    p = resolve(req.base, p)
  } else {
    p = resolve(p)
  }

  let st
  try {
    st = statSync(p)
  } catch {
    return { ok: false, message: `${short(p)} does not exist. Nothing opened.` }
  }

  if (st.isDirectory()) {
    const explorer = join(process.env['SystemRoot'] ?? 'C:\\Windows', 'explorer.exe')
    launcher.spawnDetached(explorer, [p.endsWith(sep) ? p : p + sep])
    return { ok: true, opened: 'explorer', message: `Opened ${short(p)} in File Explorer.` }
  }
  if (!st.isFile()) return { ok: false, message: `${short(p)} is not a file or a folder. Nothing opened.` }
  if (inOneDrive(p)) {
    return { ok: false, message: `${short(p)} is in OneDrive. Not opened, so nothing downloads on the metered connection.` }
  }
  const code = findVsCode()
  if (!code) return { ok: false, message: 'VS Code (code) is not on PATH. Nothing opened.' }
  const at = req.line ? `${p}:${req.line}${req.col ? `:${req.col}` : ''}` : p
  launcher.spawnDetached(code.exe, [code.cli, '--goto', at], { ELECTRON_RUN_AS_NODE: '1', VSCODE_DEV: '' })
  return { ok: true, opened: 'editor', message: `Opened ${short(p)}${req.line ? ` at line ${req.line}` : ''} in VS Code.` }
}

export function realLauncher(openExternal: (url: string) => Promise<void>): Launcher {
  return {
    openExternal,
    spawnDetached: (exe, args, env) => {
      const child = spawn(exe, args, {
        detached: false,
        stdio: 'ignore',
        windowsHide: true,
        shell: false,
        env: env ? { ...process.env, ...env } : process.env,
      })
      child.on('error', () => undefined)
      child.unref()
    },
  }
}
