import { createHash } from 'node:crypto';

import type { PermissionRequest } from '../shared/ipc-contract.ts';

export type FixtureCard = Omit<PermissionRequest, 'receivedAt' | 'fixture'>;

export const CARD_FIXTURE_KINDS = ['run', 'run768', 'runrtl', 'write', 'type', 'keys', 'app'] as const;

export type CardFixtureKind = (typeof CARD_FIXTURE_KINDS)[number];

export const MAX_CARD_ARGS_BYTES = 768 * 1024;

export function approvalFixtureFlag(argv: readonly string[], isDev: boolean): string[] {
  if (!isDev) return [];
  const flag = argv.find((a) => a.startsWith('--fixture-approval='));
  if (!flag) return [];
  return flag
    .slice('--fixture-approval='.length)
    .split(',')
    .map((k) => k.trim())
    .filter(Boolean);
}

export function approvalFixtureDelay(argv: readonly string[], isDev: boolean): { at: number; gap: number } | null {
  if (!isDev) return null;
  const flag = argv.find((a) => a.startsWith('--fixture-approval-at='));
  if (!flag) return null;
  const [at, gap] = flag.slice('--fixture-approval-at='.length).split(',').map((v) => Number.parseInt(v, 10));
  if (at === undefined || !Number.isFinite(at) || at < 0) return null;
  return { at: Math.min(at, 600_000), gap: gap !== undefined && Number.isFinite(gap) && gap > 0 ? Math.min(gap, 600_000) : 0 };
}

export function isCardFixtureKind(kind: string): kind is CardFixtureKind {
  return (CARD_FIXTURE_KINDS as readonly string[]).includes(kind);
}

export function argsBytes(args: Readonly<Record<string, unknown>>): number {
  return Buffer.byteLength(JSON.stringify(args), 'utf8');
}

function sha(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

function lines(prefix: string, count: number): string {
  let out = '';
  for (let i = 1; i <= count; i++) {
    const n = String(i).padStart(5, '0');
    out += i % 4 === 1 ? `# ${prefix} ${n}: placeholder fixture text, not real code\n` : `value_${n} = "placeholder ${prefix} ${n}"\n`;
  }
  return out;
}

function sized(prefix: string, chars: number): string {
  let out = '';
  let i = 0;
  while (out.length < chars) {
    i += 1;
    out += `value_${String(i).padStart(5, '0')} = "placeholder ${prefix}, fixture text only"\n`;
  }
  return out.slice(0, chars);
}

const ROOT = 'C:\\Example\\fixture-project';

interface FileSpec {
  file: string;
  text: string | null;
  hers: boolean;
  bytes?: number;
  view?: string;
}

function runArgs(files: readonly FileSpec[], extra: Record<string, unknown>): Record<string, unknown> {
  const rows: Record<string, unknown>[] = [];
  const views: Record<string, string> = {};
  for (const f of files) {
    const path = `${ROOT}\\${f.file}`;
    const row: Record<string, unknown> = {
      file: f.file,
      path,
      sha256: sha(f.text ?? f.file),
      bytes: f.bytes ?? Buffer.byteLength(f.text ?? '', 'utf8'),
      hers: f.hers,
    };
    if (f.text !== null) {
      row['lines'] = f.text.split('\n').length - (f.text.endsWith('\n') ? 1 : 0);
      views[f.file] = f.text;
    } else {
      row['view'] = f.view ?? `Too long to show with the rest of the card: open ${path} to read it.`;
    }
    rows.push(row);
  }
  return {
    program: `${ROOT}\\${files[0]?.file ?? 'main.py'}`,
    files: rows,
    views,
    ...extra,
    set_sha256: sha(rows.map((r) => `${String(r['file'])}:${String(r['sha256'])}`).join('\n')),
  };
}

function card(id: string, tool: string, args: Record<string, unknown>): FixtureCard {
  return { requestId: `fixture-${id}`, tier: 'red', tool, provenance: 'agent', expiresAt: '', args, frozen: Object.keys(args) };
}

function runCard(): FixtureCard {
  const args = runArgs(
    [
      { file: 'main.py', text: lines('main', 60), hers: true },
      { file: 'helper.py', text: lines('helper', 24), hers: false },
      { file: 'lib\\shapes.py', text: lines('shapes', 12), hers: true },
    ],
    {
      unchecked: [
        { folder: 'node_modules', why: 'A dependency folder. It is not walked.', files: 1234 },
        { folder: '.git', why: "Git's own settings and objects. Not checked.", files: null },
        { folder: 'the rest of its folder', why: 'Only the script, what it imports and the files that could load in their place are on the card.', files: 37 },
      ],
      whose: 'mixed',
      scope: 'script',
    },
  );
  return card('run-01', 'code.run_mine', args);
}

function rtlCard(): FixtureCard {
  const tricky =
    '# placeholder fixture text, not real code\n' +
    'access_level = "user"\n' +
    'if access_level != "user\u202E \u2066# placeholder check\u2069 \u2066":\n' +
    '    print("placeholder branch")\n' +
    'zero\u200Bwidth = 1\n' +
    'a lone carriage return\rhides the start of this line\n' +
    'tag characters: \u{E0041}\u{E0042}\u{E0043}\n' +
    'no\u00A0break space and a soft\u00ADhyphen\n' +
    'a run of controls: \u0001\u0001\u0001\u0001\u0001\n' +
    'line ends in CRLF\r\n';
  const args = runArgs(
    [
      { file: 'notes\u202Etxt.py', text: tricky, hers: true },
      { file: 'plain.py', text: lines('plain', 8), hers: false },
    ],
    { unchecked: [], whose: 'mixed', scope: 'script' },
  );
  return card('runrtl-01', 'code.run_mine', args);
}

export const FILE_TEXT_CHARS = 64 * 1024;

function run768Card(): FixtureCard {
  const files: FileSpec[] = [];
  for (let i = 1; i <= 8; i++) files.push({ file: `part${String(i).padStart(2, '0')}.py`, text: sized(`part ${i}`, FILE_TEXT_CHARS), hers: i % 2 === 0 });
  files[1] = { file: 'part02.py', text: ('a\u202Eb\u2066'.repeat(4096) + '\n' + sized('part 2', FILE_TEXT_CHARS)).slice(0, FILE_TEXT_CHARS), hers: true };
  for (let i = 9; i <= 200; i++) files.push({ file: `module${String(i).padStart(3, '0')}.py`, text: null, hers: i % 3 === 0, bytes: 70_000 + i });
  const unchecked = [
    { folder: 'node_modules', why: 'A dependency folder. It is not walked.', files: 18_000 },
    { folder: '__pycache__', why: 'A build folder. It is not walked.', files: 410 },
  ];
  const plain = sized('part 8', FILE_TEXT_CHARS);
  const build = (controls: number): Record<string, unknown> => {
    const c = Math.max(0, Math.min(FILE_TEXT_CHARS, controls));
    files[7] = { file: 'part08.py', text: '\u0001'.repeat(c) + plain.slice(0, FILE_TEXT_CHARS - c), hers: true };
    return runArgs(files, { unchecked, whose: 'mixed', scope: 'folder' });
  };
  const target = MAX_CARD_ARGS_BYTES - 64;
  let controls = 0;
  let args = build(controls);
  for (let pass = 0; pass < 6; pass++) {
    const gap = target - argsBytes(args);
    if (gap >= 0 && gap < 64) break;
    controls += Math.floor(gap / 5);
    args = build(controls);
  }
  while (argsBytes(args) > MAX_CARD_ARGS_BYTES && controls > 0) {
    controls -= 16;
    args = build(controls);
  }
  return card('run768-01', 'code.run_mine', args);
}

function writeCard(): FixtureCard {
  const text =
    'placeholder note, line one\n' +
    'placeholder note, line two\n' +
    '<b>this is not bold</b> <img src=x onerror="alert(1)"> &amp; [not a link](https://example.invalid) **not markdown**\n';
  return card('write-01', 'fs.write_checked', {
    op: 'create_file',
    path: 'C:\\Example\\TessaTest\\placeholder-note.txt',
    folder: 'C:\\Example\\TessaTest',
    why: 'This folder is not one of your own folders, so writing here needs your yes.',
    kind: 'text',
    files: 1,
    bytes: Buffer.byteLength(text, 'utf8'),
    sha256: sha(text),
    hashed: true,
    text,
  });
}

function typeCard(): FixtureCard {
  return card('type-01', 'input.type_checked', {
    op: 'type',
    text: 'placeholder words\nsecond line\tafter a tab',
    window: 'Placeholder Document - WordPad',
    process: 'wordpad.exe',
    program: 'C:\\Program Files\\Windows NT\\Accessories\\wordpad.exe',
    pid: 4242,
    hwnd: '0x000A0B2C',
    window_class: 'WordPadClass',
    focus: 'RICHEDIT50W',
    focus_hwnd: '0x000B0C3D',
    why: 'Typing anywhere but Notepad needs your yes.',
  });
}

function keysCard(): FixtureCard {
  return card('keys-01', 'input.type_checked', {
    op: 'key',
    keys: 'ctrl+v',
    window: 'Placeholder Window - Example App',
    process: 'example.exe',
    program: 'C:\\Example\\App\\example.exe',
    pid: 4343,
    hwnd: '0x000C0D4E',
    window_class: 'ExampleWindowClass',
    focus: 'Edit',
    focus_hwnd: '0x000D0E5F',
    why: 'Typing anywhere but Notepad needs your yes.',
    pastes: 'This pastes whatever is on the clipboard.',
  });
}

function appCard(): FixtureCard {
  const subject = 'CN=Example Publisher Ltd, O=Example Publisher Ltd, C=GB';
  return card('app-01', 'app.launch_checked', {
    ...runArgs([{ file: 'example.exe', text: null, hers: false, bytes: 2_345_678, view: 'A program file. Its text is not shown.' }], { unchecked: [], whose: 'yours' }),
    name: 'Example App',
    publisher: subject,
    approving: 'Approving trusts every app signed by Example Publisher Ltd in your own folders, after updates too.',
    signature: {
      status: 'valid',
      publisher: subject,
      issuer: 'CN=Example Code Signing CA, O=Example CA Ltd',
      revocation: "not revoked in Windows' cached lists",
      why: '',
    },
  });
}

const BUILDERS: Readonly<Record<CardFixtureKind, () => FixtureCard>> = {
  run: runCard,
  run768: run768Card,
  runrtl: rtlCard,
  write: writeCard,
  type: typeCard,
  keys: keysCard,
  app: appCard,
};

export function cardFixture(kind: string): FixtureCard[] | undefined {
  return isCardFixtureKind(kind) ? [BUILDERS[kind]()] : undefined;
}
