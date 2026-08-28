/**
 * apps/console/scripts/make-icon.mjs — a PLACEHOLDER icon, generated not drawn.
 *
 * The repo ships no application artwork. Rather than invent any, this writes
 * the plainest identifiable mark possible: the background token filled edge to
 * edge, and a single letter T built from two rectangles in the accent token.
 * No curves, no gradient, no shading — if it looks like design, it is a bug.
 *
 * WHY GENERATED AND NOT COMMITTED AS A BINARY
 *   - The two colours are READ FROM packages/tokens/tokens.json at build time,
 *     so the icon cannot drift from the palette and no hex literal exists in
 *     this repo's source because of it.
 *   - It is trivially replaceable: when real artwork arrives, delete this
 *     script and commit `build/icon.ico`. Nothing else references it.
 *
 * WHY A HAND-WRITTEN ICO
 *   Adding an image library for a placeholder would cost a dependency on a
 *   metered connection. The ICO container is a 6-byte header, a 16-byte
 *   directory entry per image and a headless BMP each — about forty lines.
 *
 * Run: node apps/console/scripts/make-icon.mjs
 */

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..', '..')

const tokens = JSON.parse(readFileSync(join(REPO, 'packages/tokens/tokens.json'), 'utf8'))
const BG = tokens.color['bg-void'].value
const FG = tokens.color.accent.value

/** '#RRGGBB' -> [r,g,b]. The only place this file understands hex at all. */
function rgb(hex) {
  const h = hex.replace('#', '')
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}

const [BR, BG_, BB] = rgb(BG)
const [FR, FG_, FB] = rgb(FG)

/**
 * One square image as a headless BMP (BITMAPINFOHEADER + BGRA + AND mask).
 *
 * `biHeight` is DOUBLE the real height — the ICO format's oldest quirk. The
 * second half is the 1-bit AND mask, which is all zeros here because every
 * pixel is opaque; the alpha channel in the BGRA data is what actually decides.
 */
function bmp(n) {
  const bar = Math.max(1, Math.round(n * 0.12)) // stroke width of the T
  const inset = Math.round(n * 0.24) // margin around the glyph
  const top = inset
  const bottom = n - inset
  const stemL = Math.round((n - bar) / 2)

  const xor = Buffer.alloc(n * n * 4)
  for (let y = 0; y < n; y++) {
    // BMP rows run bottom-up.
    const row = (n - 1 - y) * n * 4
    for (let x = 0; x < n; x++) {
      const onCross = y >= top && y < top + bar && x >= inset && x < n - inset
      const onStem = y >= top && y < bottom && x >= stemL && x < stemL + bar
      const on = onCross || onStem
      const o = row + x * 4
      xor[o] = on ? FB : BB
      xor[o + 1] = on ? FG_ : BG_
      xor[o + 2] = on ? FR : BR
      xor[o + 3] = 255
    }
  }
  const and = Buffer.alloc((n * n) / 8) // zeros = fully opaque

  const hdr = Buffer.alloc(40)
  hdr.writeUInt32LE(40, 0)
  hdr.writeInt32LE(n, 4)
  hdr.writeInt32LE(n * 2, 8)
  hdr.writeUInt16LE(1, 12)
  hdr.writeUInt16LE(32, 14)
  hdr.writeUInt32LE(0, 16)
  hdr.writeUInt32LE(xor.length + and.length, 20)
  return Buffer.concat([hdr, xor, and])
}

const SIZES = [256, 64, 48, 32, 16]
const images = SIZES.map(bmp)

const dir = Buffer.alloc(6)
dir.writeUInt16LE(0, 0)
dir.writeUInt16LE(1, 2) // 1 = icon
dir.writeUInt16LE(SIZES.length, 4)

let offset = 6 + 16 * SIZES.length
const entries = SIZES.map((n, i) => {
  const e = Buffer.alloc(16)
  e.writeUInt8(n === 256 ? 0 : n, 0) // 0 means 256 — the field is one byte
  e.writeUInt8(n === 256 ? 0 : n, 1)
  e.writeUInt8(0, 2)
  e.writeUInt8(0, 3)
  e.writeUInt16LE(1, 4)
  e.writeUInt16LE(32, 6)
  e.writeUInt32LE(images[i].length, 8)
  e.writeUInt32LE(offset, 12)
  offset += images[i].length
  return e
})

const out = join(HERE, '..', 'build', 'icon.ico')
mkdirSync(dirname(out), { recursive: true })
writeFileSync(out, Buffer.concat([dir, ...entries, ...images]))
console.log(`icon.ico written: ${out} (${SIZES.join('/')} px, bg=${BG} mark=${FG})`)
