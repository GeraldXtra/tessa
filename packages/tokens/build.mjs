#!/usr/bin/env node
/**
 * packages/tokens/build.mjs
 *
 * Generates dist/tokens.css and dist/tokens.py from tokens.json.
 *
 * The CSS custom-property names emitted here are LAW — they are enumerated in
 * CONTRACT.md §9 and both apps/console and apps/orb depend on them verbatim.
 * That is why the group -> prefix mapping below is EXPLICIT rather than derived
 * from the group name: a naming scheme change must be a deliberate contract
 * edit, not an accident of refactoring this script.
 *
 * No dependencies. Run: node build.mjs
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, 'tokens.json');
const DIST = join(HERE, 'dist');

/**
 * group -> how each key in that group becomes a CSS custom property name.
 * Must match CONTRACT.md §9 exactly.
 */
const NAMING = {
  color:      (key) => `--${key}`,               // bg-void      -> --bg-void
  font:       (key) => `--font-${key}`,          // mono         -> --font-mono
  fontSize:   (key) => `--fs-${key}`,            // label        -> --fs-label
  lineHeight: (key) => `--lh-${key}`,            // tight        -> --lh-tight
  label:      (key) => `--label-${key}`,         // tracking     -> --label-tracking
  space:      (key) => `--sp-${key}`,            // 1            -> --sp-1
  radius:     (key) => (key === 'panel' ? '--panel-radius' : `--radius-${key}`),
  layout:     (key) => `--${key}`,               // rail-w       -> --rail-w
  motion:     (key) => `--motion-${key}`,        // fast         -> --motion-fast
};

/** Groups that are metadata, not tokens. */
const SKIP = new Set(['$schema', 'meta', 'rules']);

const tokens = JSON.parse(readFileSync(SRC, 'utf8'));

/* ------------------------------------------------------------------ collect */

/** @type {{cssName: string, value: string, comment?: string, group: string, key: string}[]} */
const flat = [];

for (const [group, entries] of Object.entries(tokens)) {
  if (SKIP.has(group)) continue;

  const namer = NAMING[group];
  if (!namer) {
    throw new Error(
      `tokens.json has group "${group}" with no entry in NAMING. ` +
      `Add an explicit mapping — CSS variable names are part of CONTRACT.md §9.`
    );
  }

  for (const [key, entry] of Object.entries(entries)) {
    if (entry == null || typeof entry !== 'object' || !('value' in entry)) {
      throw new Error(`tokens.json ${group}.${key} must be an object with a "value" field.`);
    }
    flat.push({
      cssName: namer(key),
      value: String(entry.value),
      comment: entry.comment,
      group,
      key,
    });
  }
}

/* Fail loudly on a duplicate name — two groups colliding would silently drop one. */
const seen = new Map();
for (const t of flat) {
  if (seen.has(t.cssName)) {
    const prev = seen.get(t.cssName);
    throw new Error(
      `Duplicate CSS variable ${t.cssName}: ` +
      `${prev.group}.${prev.key} and ${t.group}.${t.key}`
    );
  }
  seen.set(t.cssName, t);
}

/* --------------------------------------------------------------------- CSS */

const GENERATED = 'GENERATED FILE — DO NOT EDIT. Source: packages/tokens/tokens.json';

let css = `/* ${GENERATED} */\n`;
css += `/* ${tokens.meta.source} */\n\n:root {\n`;

let lastGroup = null;
for (const t of flat) {
  if (t.group !== lastGroup) {
    css += `${lastGroup === null ? '' : '\n'}  /* ${t.group} */\n`;
    lastGroup = t.group;
  }
  const pad = ' '.repeat(Math.max(1, 22 - t.cssName.length));
  css += `  ${t.cssName}:${pad}${t.value};`;
  css += t.comment ? `  /* ${t.comment} */\n` : '\n';
}
css += `}\n`;

/* The sub-1600px rule is a contract obligation (CONTRACT §9.2), so it ships
   with the tokens rather than being re-derived in each surface. */
css += `
/* CONTRACT §9.2 — the owner's display is 1366x768.
   rail 48 + left 240 + right 280 + transcript 320 = 888px of chrome,
   leaving ~478px of centre stage. Both surfaces MUST collapse panels below
   --full-layout-min-width. This class is provided so neither surface
   hard-codes the breakpoint. */
@media (max-width: ${parseInt(tokens.layout['full-layout-min-width'].value, 10) - 1}px) {
  :root { --layout-mode: compact; }
}
@media (min-width: ${tokens.layout['full-layout-min-width'].value}) {
  :root { --layout-mode: full; }
}
`;

/* ------------------------------------------------------------------ Python */

const pyIdent = (group, key) =>
  `${group}_${key}`.replace(/-/g, '_').replace(/([a-z0-9])([A-Z])/g, '$1_$2').toUpperCase();

let py = `"""${GENERATED}\n\n${tokens.meta.source}\n"""\n\n`;
py += `from typing import Final\n\nTOKENS_VERSION: Final[int] = ${tokens.meta.version}\n\n`;

lastGroup = null;
for (const t of flat) {
  if (t.group !== lastGroup) {
    py += `\n# --- ${t.group} ---\n`;
    lastGroup = t.group;
  }
  py += `${pyIdent(t.group, t.key)}: Final[str] = ${JSON.stringify(t.value)}`;
  py += t.comment ? `  # ${t.comment}\n` : '\n';
}

py += `\n# Style rules binding both surfaces (CONTRACT §9.1)\n`;
for (const [k, v] of Object.entries(tokens.rules)) {
  if (k === 'comment') continue;
  const name = k.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toUpperCase();
  py += `${name}: Final[bool] = ${v ? 'True' : 'False'}\n`;
}

py += `\nCSS_VAR_BY_TOKEN: Final[dict[str, str]] = {\n`;
for (const t of flat) py += `    ${JSON.stringify(pyIdent(t.group, t.key))}: ${JSON.stringify(t.cssName)},\n`;
py += `}\n`;

/* -------------------------------------------------------------------- write */

mkdirSync(DIST, { recursive: true });
writeFileSync(join(DIST, 'tokens.css'), css, 'utf8');
writeFileSync(join(DIST, 'tokens.py'), py, 'utf8');

console.log(`tokens: wrote ${flat.length} tokens -> dist/tokens.css, dist/tokens.py`);
