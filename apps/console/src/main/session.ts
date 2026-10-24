import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

import { isShellId, type ShellId } from './shells.ts'

export interface SavedTab {
  shellId: ShellId
  cwd: string
  name: string
}

export interface SavedSession {
  tabs: SavedTab[]
  recent: string[]
}

const MAX_TABS = 24
const MAX_RECENT = 3

export function sessionPath(): string {
  const base = process.env['LOCALAPPDATA'] ?? process.env['APPDATA'] ?? ''
  return join(base, 'Tessa', 'console-session.json')
}

function cleanPath(p: unknown): string {
  if (typeof p !== 'string') return ''
  const s = p.trim()
  if (!s || s.length > 1024 || /[\u0000-\u001f]/.test(s)) return ''
  return s
}

export function sanitiseSession(raw: unknown): SavedSession {
  const out: SavedSession = { tabs: [], recent: [] }
  if (typeof raw !== 'object' || raw === null) return out
  const o = raw as Record<string, unknown>
  if (Array.isArray(o['tabs'])) {
    for (const t of o['tabs'].slice(0, MAX_TABS)) {
      if (typeof t !== 'object' || t === null) continue
      const r = t as Record<string, unknown>
      if (!isShellId(r['shellId'])) continue
      const cwd = cleanPath(r['cwd'])
      if (!cwd) continue
      const name = typeof r['name'] === 'string' ? r['name'].replace(/[\u0000-\u001f]/g, '').slice(0, 40) : ''
      out.tabs.push({ shellId: r['shellId'], cwd, name })
    }
  }
  if (Array.isArray(o['recent'])) {
    for (const f of o['recent']) {
      const p = cleanPath(f)
      if (p && !out.recent.some((x) => x.toLowerCase() === p.toLowerCase())) out.recent.push(p)
      if (out.recent.length >= MAX_RECENT) break
    }
  }
  return out
}

export function loadSession(): { session: SavedSession; existed: boolean; problem?: string } {
  const path = sessionPath()
  if (!existsSync(path)) return { session: { tabs: [], recent: [] }, existed: false }
  try {
    return { session: sanitiseSession(JSON.parse(readFileSync(path, 'utf8'))), existed: true }
  } catch (err) {
    return { session: { tabs: [], recent: [] }, existed: true, problem: (err as Error).message }
  }
}

export function saveSession(s: SavedSession): void {
  const path = sessionPath()
  mkdirSync(dirname(path), { recursive: true })
  const tmp = `${path}.tmp`
  writeFileSync(tmp, JSON.stringify({ version: 1, ...sanitiseSession(s) }, null, 2) + '\n', 'utf8')
  renameSync(tmp, path)
}
