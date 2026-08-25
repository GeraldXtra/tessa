#!/usr/bin/env node
/**
 * packages/protocol/build-enums.mjs
 *
 * Generates the closed-enum definitions for BOTH languages from
 * schema/enums.json, so the contract has exactly one source of truth.
 *
 *   -> src/enums.generated.ts          (re-exported by src/index.ts)
 *   -> gen/python/tessa_protocol/enums.py   (imported by core/)
 *
 * Before this existed the enums were hand-maintained in CONTRACT.md,
 * index.ts, core/*.py and two test files. Four hand-written copies of one
 * contract drift; that is the entire reason the schema exists.
 *
 * No dependencies. Run: node build-enums.mjs [--check]
 *
 * --check writes nothing and exits non-zero if the committed output differs
 *         from what would be generated. Used by scripts/check-contract.mjs.
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, 'schema', 'enums.json');
const TS_OUT = join(HERE, 'src', 'enums.generated.ts');
const PY_DIR = join(HERE, 'gen', 'python', 'tessa_protocol');
const PY_OUT = join(PY_DIR, 'enums.py');

const CHECK = process.argv.includes('--check');

const schema = JSON.parse(readFileSync(SRC, 'utf8'));
const { enums, meta } = schema;

const BANNER_TS = `// GENERATED FILE — DO NOT EDIT.
// Source: packages/protocol/schema/enums.json
// Regenerate: node packages/protocol/build-enums.mjs
//
// CONTRACT.md §7.4 — sets marked \`closed\` are exhaustive. Adding a value to a
// closed set is a BREAKING change requiring a PROTOCOL_VERSION bump.`;

const BANNER_PY = `"""GENERATED FILE — DO NOT EDIT.

Source: packages/protocol/schema/enums.json
Regenerate: node packages/protocol/build-enums.mjs

CONTRACT.md §7.4 — sets marked closed are exhaustive. Adding a value to a
closed set is a BREAKING change requiring a PROTOCOL_VERSION bump.
"""`;

/* ─────────────────────────────────────────────────────────────── TypeScript */

const tsLines = [BANNER_TS, ''];
tsLines.push(`export const PROTOCOL_VERSION = ${meta.protocolVersion} as const;`, '');

for (const [name, def] of Object.entries(enums)) {
  const closedNote = def.closed ? 'CLOSED SET' : 'OPEN SET — consumers MUST have a default branch';
  tsLines.push('/**');
  tsLines.push(` * ${def.description}`);
  tsLines.push(` *`);
  tsLines.push(` * ${closedNote}.`);
  tsLines.push(' */');

  if (def.numeric) {
    tsLines.push(`export const ${def.tsConst} = {`);
    for (const v of def.values) {
      tsLines.push(`  ${v.name}: ${v.value},${v.note ? `  // ${v.note}` : ''}`);
    }
    tsLines.push(`} as const;`);
    tsLines.push(`export type ${name} = (typeof ${def.tsConst})[keyof typeof ${def.tsConst}];`);
  } else {
    tsLines.push(`export const ${def.tsConst} = [`);
    for (const v of def.values) {
      tsLines.push(`  ${JSON.stringify(v.value)},${v.note ? `  // ${v.note.slice(0, 100)}` : ''}`);
    }
    tsLines.push(`] as const;`);
    tsLines.push(`export type ${name} = (typeof ${def.tsConst})[number];`);
  }

  // Sendable subset — currently only Decision, but the mechanism is general:
  // some values are daemon-emitted only and must never be sent by a surface.
  const sendable = def.values.filter((v) => v.sendable === true);
  if (sendable.length && sendable.length !== def.values.length) {
    const constName = `${def.tsConst}_SENDABLE`;
    tsLines.push('');
    tsLines.push(`/** Values a SURFACE may send. Others are daemon-emitted only. */`);
    tsLines.push(`export const ${constName} = [`);
    for (const v of sendable) tsLines.push(`  ${JSON.stringify(v.value)},`);
    tsLines.push(`] as const;`);
    tsLines.push(`export type Sendable${name} = (typeof ${constName})[number];`);
  }
  tsLines.push('');
}

const tsOut = tsLines.join('\n');

/* ───────────────────────────────────────────────────────────────── Python */

const pyLines = [BANNER_PY, '', 'from typing import Final, Literal', '', ''];
pyLines.push(`PROTOCOL_VERSION: Final[int] = ${meta.protocolVersion}`, '');

for (const [name, def] of Object.entries(enums)) {
  const closedNote = def.closed ? 'CLOSED SET' : 'OPEN SET — handle unknown values gracefully';
  pyLines.push(`# ── ${name} — ${closedNote}`);
  pyLines.push(`# ${def.description}`);

  if (def.numeric) {
    for (const v of def.values) {
      const constName = `CLOSE_${v.name.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toUpperCase()}`;
      pyLines.push(`${constName}: Final[int] = ${v.value}${v.note ? `  # ${v.note}` : ''}`);
    }
    pyLines.push(`${def.pyName}: Final[frozenset[int]] = frozenset({${def.values.map((v) => v.value).join(', ')}})`);
  } else {
    const literals = def.values.map((v) => JSON.stringify(v.value)).join(', ');
    if (def.closed) {
      pyLines.push(`${name} = Literal[${literals}]`);
    }
    pyLines.push(`${def.pyName}: Final[frozenset[str]] = frozenset({${literals}})`);

    const sendable = def.values.filter((v) => v.sendable === true);
    if (sendable.length && sendable.length !== def.values.length) {
      pyLines.push(
        `${def.pyName}_SENDABLE: Final[frozenset[str]] = frozenset({${sendable
          .map((v) => JSON.stringify(v.value))
          .join(', ')}})  # a surface may only SEND these`
      );
    }
  }
  pyLines.push('');
}

const pyOut = pyLines.join('\n');

const PY_INIT = `${BANNER_PY}

from .enums import *  # noqa: F401,F403
from .enums import PROTOCOL_VERSION  # noqa: F401
`;

/* ────────────────────────────────────────────────────────────────── write */

const targets = [
  [TS_OUT, tsOut],
  [PY_OUT, pyOut],
  [join(PY_DIR, '__init__.py'), PY_INIT],
];

if (CHECK) {
  let stale = false;
  for (const [path, content] of targets) {
    if (!existsSync(path)) {
      console.error(`STALE: ${path} does not exist — run: node packages/protocol/build-enums.mjs`);
      stale = true;
      continue;
    }
    if (readFileSync(path, 'utf8') !== content) {
      console.error(`STALE: ${path} differs from schema/enums.json`);
      stale = true;
    }
  }
  if (stale) {
    console.error('\nGenerated protocol output is out of date. Regenerate and commit.');
    process.exit(1);
  }
  console.log('protocol enums: generated output is current');
  process.exit(0);
}

mkdirSync(dirname(TS_OUT), { recursive: true });
mkdirSync(PY_DIR, { recursive: true });
for (const [path, content] of targets) writeFileSync(path, content, 'utf8');

const closed = Object.values(enums).filter((e) => e.closed).length;
const total = Object.values(enums).reduce((n, e) => n + e.values.length, 0);
console.log(
  `protocol enums: ${Object.keys(enums).length} enums (${closed} closed), ${total} values -> enums.generated.ts, gen/python/`
);
