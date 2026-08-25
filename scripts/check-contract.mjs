#!/usr/bin/env node
/**
 * scripts/check-contract.mjs
 *
 * Fails the build if any generated artefact is stale relative to its source.
 *
 * The failure this prevents: someone edits tokens.json or enums.json, forgets to
 * regenerate, and commits. For tokens the Orb quietly renders last week's
 * colours. For the protocol it is worse — a drifted enum is a runtime bug across
 * a process boundary, and the two surfaces disagree about what a value means
 * with nothing to catch it until something misbehaves at 2am.
 *
 * Regenerates every output into a temp directory and diffs against what is
 * committed. Writes nothing to the working tree.
 *
 * Run: node scripts/check-contract.mjs
 */

import { execFileSync } from 'node:child_process';
import { readFileSync, existsSync, mkdtempSync, cpSync, rmSync } from 'node:fs';
import { readdir, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

let failures = 0;
const fail = (msg) => {
  console.error(`  FAIL  ${msg}`);
  failures++;
};
const pass = (msg) => console.log(`  ok    ${msg}`);

console.log('\ncontract freshness check\n');

/* ── 1. protocol enums — the generator has a built-in --check ─────────────── */

try {
  execFileSync('node', [join(ROOT, 'packages/protocol/build-enums.mjs'), '--check'], {
    stdio: 'pipe',
  });
  pass('packages/protocol generated output matches schema/enums.json');
} catch (err) {
  const out = (err.stderr?.toString() || '') + (err.stdout?.toString() || '');
  fail(`protocol generated output is STALE\n${out.trim()}`);
}

/* ── 2. design tokens — regenerate into a temp tree and diff ──────────────── */

const tmp = mkdtempSync(join(tmpdir(), 'tessa-contract-'));
try {
  const stage = join(tmp, 'tokens');
  cpSync(join(ROOT, 'packages/tokens'), stage, {
    recursive: true,
    filter: (src) => !src.includes('node_modules'),
  });
  rmSync(join(stage, 'dist'), { recursive: true, force: true });

  execFileSync('node', [join(stage, 'build.mjs')], { stdio: 'pipe' });

  for (const file of ['tokens.css', 'tokens.py']) {
    const committed = join(ROOT, 'packages/tokens/dist', file);
    const fresh = join(stage, 'dist', file);
    if (!existsSync(committed)) {
      fail(`packages/tokens/dist/${file} missing — run: npm run tokens`);
      continue;
    }
    if (readFileSync(committed, 'utf8') !== readFileSync(fresh, 'utf8')) {
      fail(`packages/tokens/dist/${file} is STALE — run: npm run tokens`);
    } else {
      pass(`packages/tokens/dist/${file} matches tokens.json`);
    }
  }
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

/* ── 3. no hard-coded hex colours in surface code (CONTRACT §9) ───────────── */

/**
 * Hard-coded CSS colour.
 *
 * Two refinements over the naive /#[0-9a-fA-F]{3,8}/, both added after it
 * produced false positives on GitHub issue references like `electron#20550`
 * and `vscode#175335` in source comments. A checker that cries wolf is worse
 * than no checker — it teaches you to ignore it.
 *
 *   (?<![A-Za-z0-9_])  the '#' must not follow an identifier character, so
 *                      `electron#20550` and `vscode#175335` are not colours
 *   {3}|{4}|{6}|{8}    only VALID CSS hex lengths; 5- and 7-digit runs like
 *                      #20550 cannot be colours at all
 */
const HEX = /(?<![A-Za-z0-9_])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-fA-F])/;
const SKIP_DIRS = new Set(['node_modules', 'dist', '.git', 'gen', 'data']);
let scanned = 0;
let hexHits = 0;

async function walk(dir, onFile) {
  let entries;
  try {
    entries = await readdir(dir);
  } catch {
    return;
  }
  for (const name of entries) {
    if (SKIP_DIRS.has(name)) continue;
    const full = join(dir, name);
    const st = await stat(full);
    if (st.isDirectory()) await walk(full, onFile);
    else onFile(full, name);
  }
}

for (const r of ['apps/console/src', 'apps/orb/src']) {
  await walk(join(ROOT, r), (full, name) => {
    if (!/\.(ts|tsx|css|scss)$/.test(name)) return;
    scanned++;
    readFileSync(full, 'utf8')
      .split('\n')
      .forEach((line, i) => {
        if (HEX.test(line) && !line.includes('tokens')) {
          fail(`hard-coded colour in ${relative(ROOT, full)}:${i + 1} — use a token (CONTRACT §9)`);
          hexHits++;
        }
      });
  });
}
if (hexHits === 0) pass(`no hard-coded colours (${scanned} surface files scanned)`);

/* ── 4. no hard-coded daemon port outside the contract (CONTRACT §1) ──────── */

const PORT_RE = /\b47600\b/;
const PORT_ALLOWED = [
  'packages/protocol/src/index.ts',
  'packages/protocol/schema',
  'packages/protocol/test',
  'core/server.py',
  'core/tests',
  'CONTRACT.md',
  'plan.md',
  'README.md',
  'docs',
  'scripts/check-contract.mjs',
];
let portHits = 0;
await walk(ROOT, (full, name) => {
  if (!/\.(ts|tsx|js|mjs|py|json)$/.test(name)) return;
  const rel = relative(ROOT, full).replace(/\\/g, '/');
  if (PORT_ALLOWED.some((a) => rel.startsWith(a))) return;
  if (PORT_RE.test(readFileSync(full, 'utf8'))) {
    fail(`hard-coded port 47600 in ${rel} — discover it from runtime.json (CONTRACT §1)`);
    portHits++;
  }
});
if (portHits === 0) pass('no hard-coded ports outside the contract');

/* ── result ──────────────────────────────────────────────────────────────── */

console.log('');
if (failures) {
  console.error(`${failures} contract check(s) failed\n`);
  process.exit(1);
}
console.log('contract is current\n');
