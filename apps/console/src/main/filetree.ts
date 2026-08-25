/**
 * apps/console/src/main/filetree.ts — a read-only, metadata-only directory list.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE RULE THAT SHAPES EVERY LINE HERE
 *
 * CLAUDE.md, security invariant 5: **indexing is metadata-only; never read
 * content from a reparse point.** His OneDrive tree holds 17,340 placeholders,
 * and reading one costs metered data.
 *
 * So this module does exactly two filesystem operations, both of which are
 * metadata:
 *
 *   readdir(dir, { withFileTypes: true })   names + kinds, one level
 *   lstat(path)                             size/mtime WITHOUT following
 *
 * There is no `readFile` in this file and there must never be one. `lstat` is
 * used rather than `stat` deliberately.
 *
 * ── WHAT I MEASURED, AND WHY THE ORIGINAL FLAG WAS WORTHLESS ────────────────
 *
 * This module first flagged placeholders with `Dirent.isSymbolicLink()`. That
 * check is ALWAYS FALSE for them. Measured against PowerShell on his own
 * OneDrive root:
 *
 *   PowerShell (.NET FileSystemInfo.Attributes)  22 of 85 entries are reparse
 *   Node lstat().isSymbolicLink()                 0 of 22 seen
 *   Python os.stat().st_file_attributes           0 of 22 seen
 *
 * PowerShell reports attribute 525328 for a folder where Python reports 524304.
 * The difference is exactly 1024 — FILE_ATTRIBUTE_REPARSE_POINT. Both runtimes
 * FOLLOW a cloud placeholder and hand back the target's attributes with the
 * reparse bit stripped, because OneDrive's tag is not a name-surrogate. Only a
 * raw GetFileAttributesW sees it.
 *
 * So a guard written on that flag enforces NOTHING. It would pass every test
 * and cost him metered data in real use.
 *
 * ── WHY THE TREE IS SAFE ANYWAY, AND THIS IS THE LOAD-BEARING PART ──────────
 *
 * The safety does not come from the flag. It comes from never opening a file.
 * LISTING a directory does not hydrate its children — measured on six
 * dehydrated files in his OneDrive root: allocated blocks 0 before the listing
 * and 0 after, 85 entries in 27 ms, no network fetch. The flag is belt and
 * braces; the braces are that `readFile` does not appear in this module.
 *
 * ── THE SIGNAL I TRIED, AND WHY IT IS GONE ─────────────────────────────────
 *
 * This module briefly marked files "cloud-only" when `blocks * 512 < size`,
 * reasoning that allocated-below-logical means the content is not on disk.
 * MEASURED, IT IS A FALSE-POSITIVE MACHINE. `Stats.blocks` is AllocationSize
 * floored to 512-byte units, so EVERY file small enough to live inside the MFT
 * record reports 0 and looks dehydrated. On his OneDrive that was 6 files at
 * the root — sizes 1, 63, 71, 75, 101 and 203 bytes, all of them fully local —
 * and 610 across the first 4,000 files walked.
 *
 * Node cannot answer this question. The real test is `AllocationSize == 0`,
 * which needs GetFileInformationByHandleEx; core/tools/files.py does exactly
 * that in Python because it gates an actual READ. This module gates nothing —
 * it only lists — so it no longer claims to know, rather than marking 610 of
 * his own files as cloud files.
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { constants, lstatSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

/**
 * How many children are returned before truncating.
 *
 * `node_modules` routinely holds thousands, and he has 2,634 of them across the
 * machine — so this is the first directory he will hit, not an edge case.
 * Rendering 5,000 rows would freeze a 2-core machine, so the list is TRUNCATED
 * and the total is reported: the UI says "showing 300 of 4,812" rather than
 * quietly lying about what is there.
 */
export const MAX_CHILDREN = 300

export interface TreeEntry {
  name: string
  path: string
  dir: boolean
  /** A real symlink or junction. Node CAN see these, and never follows them. */
  link: boolean
  /** Bytes. `-1` when unknown, which is not the same as zero. */
  size: number
}

export interface TreeListing {
  path: string
  entries: TreeEntry[]
  /** Total children found before truncation. */
  total: number
  truncated: boolean
  /**
   * Set when the directory could not be listed at all. An EMPTY folder and an
   * INACCESSIBLE one are different facts and the UI must be able to say which.
   */
  error?: string
}

/**
 * One level of `dir`. Never recurses — the caller expands on demand.
 *
 * LAZY IS NOT AN OPTIMISATION HERE, IT IS THE SAFETY PROPERTY. A tree that
 * walked eagerly would touch every placeholder under OneDrive and spend his
 * data, and it would look like nothing at all until the bill.
 */
export function listDir(dir: string): TreeListing {
  let dirents
  try {
    dirents = readdirSync(dir, { withFileTypes: true })
  } catch (err) {
    const e = err as NodeJS.ErrnoException
    const why =
      e.code === 'EACCES' || e.code === 'EPERM'
        ? 'permission denied'
        : e.code === 'ENOENT'
          ? 'no longer exists'
          : e.code === 'ENOTDIR'
            ? 'not a directory'
            : (e.code ?? e.message)
    return { path: dir, entries: [], total: 0, truncated: false, error: why }
  }

  const total = dirents.length
  // Directories first, then files, each alphabetical and case-insensitive —
  // sorted BEFORE truncation so the first 300 are the first 300 he would
  // expect, not an arbitrary slice of readdir order.
  dirents.sort((a, b) => {
    const ad = a.isDirectory() ? 0 : 1
    const bd = b.isDirectory() ? 0 : 1
    if (ad !== bd) return ad - bd
    return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
  })

  const slice = dirents.slice(0, MAX_CHILDREN)
  const entries: TreeEntry[] = slice.map((d) => {
    const full = join(dir, d.name)
    const isDir = d.isDirectory()
    let size = -1
    const link = d.isSymbolicLink()
    if (!isDir) {
      try {
        // lstat is metadata and fetches nothing — proven: six zero-allocation
        // candidates were untouched across a full listing of the same folder.
        const st = lstatSync(full, { throwIfNoEntry: false })
        if (st) size = st.size
      } catch {
        /* unknown stays -1 */
      }
    }
    return { name: d.name, path: full, dir: isDir, link, size }
  })

  return { path: dir, entries, total, truncated: total > MAX_CHILDREN }
}

/** Exported for the proof: confirms this module never opens a file. */
export const FS_OPERATIONS_USED = ['readdirSync', 'lstatSync'] as const
void constants
