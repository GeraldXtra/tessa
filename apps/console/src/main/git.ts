import { execFile } from 'node:child_process'
import { readFileSync, statSync } from 'node:fs'
import { dirname, isAbsolute, join, resolve } from 'node:path'

import { onPath } from './shells.ts'

export interface GitInfo {
  cwd: string
  repo: boolean
  branch: string
  changed: number | null
  note: string
  ms: number
}

const MIN_GAP_MS = 1000

let gitExe: string | null = null
let lastRunAt = 0
let running: Promise<GitInfo> | null = null
let runs = 0
let lastCpuMs = 0

export function gitStats(): { runs: number; lastMs: number } {
  return { runs, lastMs: lastCpuMs }
}

function oneDriveRoots(): string[] {
  return ['OneDrive', 'OneDriveConsumer', 'OneDriveCommercial']
    .map((k) => process.env[k])
    .filter((v): v is string => typeof v === 'string' && v.length > 2)
    .map((v) => resolve(v).toLowerCase())
}

export function inOneDrive(p: string): boolean {
  const r = resolve(p).toLowerCase()
  return oneDriveRoots().some((root) => r === root || r.startsWith(root + '\\'))
}

function isDir(p: string): boolean {
  try {
    return statSync(p).isDirectory()
  } catch {
    return false
  }
}

function isFile(p: string): boolean {
  try {
    return statSync(p).isFile()
  } catch {
    return false
  }
}

export function findRepo(cwd: string): { root: string; head: string } | null {
  let d = resolve(cwd)
  for (let i = 0; i < 64; i++) {
    const g = join(d, '.git')
    if (isDir(g)) return { root: d, head: join(g, 'HEAD') }
    if (isFile(g)) {
      try {
        const line = readFileSync(g, 'utf8').trim()
        const m = line.match(/^gitdir:\s*(.+)$/)
        if (m?.[1]) {
          const gd = isAbsolute(m[1]) ? m[1] : join(d, m[1])
          return { root: d, head: join(gd, 'HEAD') }
        }
      } catch {
        return null
      }
    }
    const up = dirname(d)
    if (up === d) return null
    d = up
  }
  return null
}

export function readBranch(head: string): string {
  try {
    const t = readFileSync(head, 'utf8').trim()
    if (t.startsWith('ref: refs/heads/')) return t.slice('ref: refs/heads/'.length)
    if (/^[0-9a-f]{7,}$/i.test(t)) return t.slice(0, 7)
    return ''
  } catch {
    return ''
  }
}

function countPorcelainZ(out: string): number {
  let n = 0
  const parts = out.split('\0')
  for (let i = 0; i < parts.length; i++) {
    const rec = parts[i] ?? ''
    if (rec.length < 3) continue
    n++
    const x = rec[0]
    if (x === 'R' || x === 'C') i++
  }
  return n
}

export function gitInfo(cwd: string, withCount: boolean): Promise<GitInfo> {
  const t0 = Date.now()
  const base: GitInfo = { cwd, repo: false, branch: '', changed: null, note: '', ms: 0 }
  if (!cwd || !isDir(cwd)) return Promise.resolve({ ...base, note: 'folder not readable' })
  if (inOneDrive(cwd)) return Promise.resolve({ ...base, note: 'OneDrive folder, git not read' })
  const repo = findRepo(cwd)
  if (!repo) return Promise.resolve({ ...base, ms: Date.now() - t0 })
  const info: GitInfo = { ...base, repo: true, branch: readBranch(repo.head) }
  if (!withCount) return Promise.resolve({ ...info, ms: Date.now() - t0 })
  if (running) return running
  const wait = Math.max(0, lastRunAt + MIN_GAP_MS - Date.now())
  if (gitExe === null) gitExe = onPath('git.exe')
  if (!gitExe) return Promise.resolve({ ...info, note: 'git.exe not on PATH', ms: Date.now() - t0 })
  const exe = gitExe
  running = new Promise<GitInfo>((res) => {
    setTimeout(() => {
      lastRunAt = Date.now()
      const started = Date.now()
      execFile(
        exe,
        ['--no-optional-locks', 'status', '--porcelain=v1', '-z', '--untracked-files=normal'],
        { cwd: repo.root, windowsHide: true, timeout: 10_000, maxBuffer: 8 * 1024 * 1024, encoding: 'utf8' },
        (err, stdout) => {
          runs++
          lastCpuMs = Date.now() - started
          running = null
          if (err) res({ ...info, note: 'git status failed', ms: Date.now() - t0 })
          else res({ ...info, changed: countPorcelainZ(String(stdout)), ms: Date.now() - t0 })
        },
      )
    }, wait)
  })
  return running
}
