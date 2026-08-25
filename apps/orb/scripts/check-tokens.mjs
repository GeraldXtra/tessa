#!/usr/bin/env node
/**
 * apps/orb/scripts/check-tokens.mjs
 *
 * Fails fast if the generated design tokens are missing.
 *
 * `packages/tokens/dist/` is gitignored — it only exists after `npm run generate`.
 * The Orb imports `@tessa/tokens/css`, so a fresh clone would otherwise fail deep
 * inside a Vite resolve error that says nothing about the real cause.
 *
 * This script deliberately only READS. `packages/` is owner-shared (CLAUDE.md);
 * the Orb never runs the tokens generator itself, it just refuses to start
 * without its output.
 */

import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, '..', '..', '..');
const TOKENS_CSS = join(REPO_ROOT, 'packages', 'tokens', 'dist', 'tokens.css');

if (!existsSync(TOKENS_CSS)) {
  console.error(
    '\n  packages/tokens/dist/tokens.css is missing.\n\n' +
    '  It is generated output (gitignored) and every colour in the Orb comes from it.\n' +
    '  Run this from the repo root, then try again:\n\n' +
    '      npm run generate\n'
  );
  process.exit(1);
}
