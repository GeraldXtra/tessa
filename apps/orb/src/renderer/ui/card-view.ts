import { APP_TOOL, INVISIBLE_INNER, RUN_TOOLS, TYPING_TOOL, WRITE_TOOL, carriesFiles, codeName, isFrozenCard } from '../../shared/cards.ts';

export type Seg = { t: string } | { m: string };

export type TextMode = 'text' | 'typed';

const TEXT_RX = new RegExp(`[${INVISIBLE_INNER}]`, 'gu');
const TYPED_RX = new RegExp(`[\\t\\n${INVISIBLE_INNER}]`, 'gu');

const TYPED_NAMES: Readonly<Record<string, string>> = {
  '\n': 'Enter',
  '\t': 'Tab',
  '\b': 'Backspace',
  '\x1b': 'Esc',
};

export function visibleSegments(text: string, mode: TextMode = 'text'): Seg[] {
  const rx = mode === 'typed' ? TYPED_RX : TEXT_RX;
  rx.lastIndex = 0;
  const out: Seg[] = [];
  let plain = '';
  let marks: string[] = [];
  let last = '';
  let run = 0;
  const closeRun = (): void => {
    if (run === 0) return;
    marks.push(run > 1 ? `[${last} x${run}]` : `[${last}]`);
    run = 0;
    last = '';
  };
  const flushMarks = (): void => {
    closeRun();
    if (marks.length > 0) out.push({ m: marks.join('') });
    marks = [];
  };
  const flushPlain = (): void => {
    if (plain) out.push({ t: plain });
    plain = '';
  };
  const addPlain = (s: string): void => {
    if (!s) return;
    flushMarks();
    plain += s;
  };
  const addMark = (mark: string): void => {
    flushPlain();
    if (mark === last) {
      run += 1;
      return;
    }
    closeRun();
    last = mark;
    run = 1;
  };
  let pos = 0;
  let m: RegExpExecArray | null;
  while ((m = rx.exec(text)) !== null) {
    const at = m.index;
    const ch = m[0];
    addPlain(text.slice(pos, at));
    pos = at + ch.length;
    if (ch === '\r' && text.charCodeAt(pos) === 10) {
      if (mode === 'text') continue;
      pos += 1;
      rx.lastIndex = pos;
      addMark('Enter');
      addPlain('\n');
      continue;
    }
    addMark((mode === 'typed' ? TYPED_NAMES[ch] : undefined) ?? codeName(ch));
    if (mode === 'typed' && ch === '\n') addPlain('\n');
  }
  addPlain(text.slice(pos));
  flushMarks();
  flushPlain();
  return out;
}

export function visibleString(text: string, mode: TextMode = 'text'): string {
  return visibleSegments(text, mode)
    .map((s) => ('t' in s ? s.t : s.m))
    .join('');
}

export function hasMarks(text: string, mode: TextMode = 'text'): boolean {
  return visibleSegments(text, mode).some((s) => 'm' in s);
}

export const CHUNK_LINES = 120;
export const CHUNK_CHARS = 12_000;

export function chunkText(text: string): string[] {
  const out: string[] = [];
  let start = 0;
  let lines = 0;
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) === 10) {
      lines += 1;
      if (lines >= CHUNK_LINES || i + 1 - start >= CHUNK_CHARS) {
        out.push(text.slice(start, i + 1));
        start = i + 1;
        lines = 0;
      }
    } else if (i + 1 - start >= CHUNK_CHARS * 2) {
      let cut = i + 1;
      const code = text.charCodeAt(i);
      if ((code >= 0xd800 && code <= 0xdbff) || (code === 13 && text.charCodeAt(i + 1) === 10)) cut += 1;
      out.push(text.slice(start, cut));
      start = cut;
      i = cut - 1;
      lines = 0;
    }
  }
  if (start < text.length || out.length === 0) out.push(text.slice(start));
  return out;
}

export function lineCount(text: string): number {
  if (!text) return 0;
  let n = 1;
  for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) === 10) n += 1;
  return text.endsWith('\n') ? n - 1 : n;
}

const KEY_NAMES: Readonly<Record<string, string>> = {
  ctrl: 'Ctrl',
  control: 'Ctrl',
  shift: 'Shift',
  alt: 'Alt',
  menu: 'Alt',
  win: 'Win',
  lwin: 'Win',
  rwin: 'Win',
  meta: 'Win',
  super: 'Win',
  cmd: 'Win',
  enter: 'Enter',
  return: 'Enter',
  esc: 'Esc',
  escape: 'Esc',
  tab: 'Tab',
  space: 'Space',
  spacebar: 'Space',
  backspace: 'Backspace',
  back: 'Backspace',
  del: 'Delete',
  delete: 'Delete',
  ins: 'Insert',
  insert: 'Insert',
  home: 'Home',
  end: 'End',
  pgup: 'PageUp',
  pageup: 'PageUp',
  prior: 'PageUp',
  pgdn: 'PageDown',
  pagedown: 'PageDown',
  next: 'PageDown',
  up: 'Up',
  down: 'Down',
  left: 'Left',
  right: 'Right',
  capslock: 'CapsLock',
  printscreen: 'PrintScreen',
  prtsc: 'PrintScreen',
  apps: 'Menu',
};

export function keyName(raw: string): string {
  const k = raw.trim();
  const known = KEY_NAMES[k.toLowerCase()];
  if (known) return known;
  if (/^f([1-9]|1[0-9]|2[0-4])$/i.test(k)) return k.toUpperCase();
  if (Array.from(k).length === 1) return visibleString(k.toUpperCase());
  return visibleString(k.charAt(0).toUpperCase() + k.slice(1));
}

function chordOf(parts: readonly string[]): string {
  return `[${parts.filter((p) => p.trim().length > 0).map(keyName).join('+')}]`;
}

export interface KeysView {
  chords: string[] | null;
  raw: string;
}

export function keysView(value: unknown): KeysView {
  const raw = typeof value === 'string' ? value : JSON.stringify(value) ?? String(value);
  if (typeof value === 'string') {
    const tokens = value.split(/[\s,]+/).filter(Boolean);
    return { chords: tokens.length > 0 ? tokens.map((t) => chordOf(t.split('+'))) : null, raw };
  }
  if (Array.isArray(value) && value.length > 0) {
    if (value.every((v) => typeof v === 'string')) {
      return { chords: (value as string[]).map((v) => chordOf(v.split('+'))), raw };
    }
    if (value.every((v) => Array.isArray(v) && v.every((x) => typeof x === 'string'))) {
      return { chords: (value as string[][]).map((v) => chordOf(v)), raw };
    }
  }
  return { chords: null, raw };
}

export interface Row {
  label: string;
  value: string;
  mono?: boolean;
  sha?: boolean;
}

export interface FileRow {
  file: string;
  path: string;
  sha256: string;
  bytes: number | null;
  hers: boolean | null;
  lines: number | null;
  view: string;
  text: string | null;
  orphan: boolean;
}

export interface UncheckedRow {
  folder: string;
  why: string;
  files: number | null;
}

export interface TextBlock {
  label: string;
  text: string;
  mode: TextMode;
}

export type CardKind = 'run' | 'write' | 'type' | 'app' | 'plain';

export interface CardModel {
  kind: CardKind;
  frozen: boolean;
  heading: string;
  rows: Row[];
  more: Row[];
  files: FileRow[];
  unchecked: UncheckedRow[];
  blocks: TextBlock[];
  keys: KeysView | null;
  warnings: string[];
  approving: string | null;
  rest: Row[];
}

const MONO_KEYS = new Set(['path', 'program', 'command', 'cwd', 'args', 'argv', 'file', 'source', 'target', 'folder', 'exe', 'script', 'entry', 'url', 'window_class', 'focus']);

function str(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

export function shown(v: unknown): string {
  if (typeof v === 'string') return v;
  if (v === undefined) return '';
  const json = JSON.stringify(v, null, 2);
  return json === undefined ? String(v) : json;
}

export function restRows(args: Readonly<Record<string, unknown>>, used: ReadonlySet<string>): Row[] {
  return Object.keys(args)
    .filter((k) => !used.has(k))
    .map((k) => {
      const v = args[k];
      return { label: k.toUpperCase(), value: shown(v), mono: MONO_KEYS.has(k) || typeof v !== 'string' };
    });
}

const SCOPES: Readonly<Record<string, string>> = {
  script: 'The script, what it imports, and the files beside it that could load in their place',
  folder: 'Every runnable file of its folder',
};

const WHOSE: Readonly<Record<string, string>> = {
  mine: 'She wrote all of it',
  yours: 'None of it is hers',
  mixed: 'Some of it is hers',
};

function explained(table: Readonly<Record<string, string>>, v: unknown): string {
  const s = shown(v);
  const said = typeof v === 'string' ? table[v] : undefined;
  return said ? `${said} (${s})` : s;
}

function fileRows(args: Readonly<Record<string, unknown>>): FileRow[] {
  const viewsRaw = args['views'];
  const views: Record<string, unknown> = typeof viewsRaw === 'object' && viewsRaw !== null && !Array.isArray(viewsRaw) ? (viewsRaw as Record<string, unknown>) : {};
  const rows: FileRow[] = [];
  const named = new Set<string>();
  const list = Array.isArray(args['files']) ? (args['files'] as unknown[]) : [];
  for (const item of list) {
    const r = typeof item === 'object' && item !== null ? (item as Record<string, unknown>) : { file: shown(item) };
    const file = str(r['file']) ?? str(r['path']) ?? shown(r['file']);
    named.add(file);
    const text = Object.prototype.hasOwnProperty.call(views, file) ? str(views[file]) : null;
    rows.push({
      file,
      path: str(r['path']) ?? '',
      sha256: str(r['sha256']) ?? shown(r['sha256']),
      bytes: num(r['bytes']),
      hers: typeof r['hers'] === 'boolean' ? (r['hers'] as boolean) : null,
      lines: num(r['lines']) ?? (text !== null ? lineCount(text) : null),
      view: str(r['view']) ?? (r['view'] === undefined ? '' : shown(r['view'])),
      text,
      orphan: false,
    });
  }
  for (const key of Object.keys(views)) {
    if (named.has(key)) continue;
    const text = shown(views[key]);
    rows.push({ file: key, path: '', sha256: '', bytes: null, hers: null, lines: lineCount(text), view: '', text, orphan: true });
  }
  return rows;
}

function uncheckedRows(v: unknown): UncheckedRow[] {
  if (!Array.isArray(v)) return [];
  return v.map((item) => {
    const r = typeof item === 'object' && item !== null ? (item as Record<string, unknown>) : { folder: shown(item) };
    return { folder: str(r['folder']) ?? shown(r['folder']), why: str(r['why']) ?? shown(r['why']), files: num(r['files']) };
  });
}

function present(args: Readonly<Record<string, unknown>>, k: string): boolean {
  return Object.prototype.hasOwnProperty.call(args, k) && args[k] !== undefined && args[k] !== null;
}

function pushRow(rows: Row[], used: Set<string>, args: Readonly<Record<string, unknown>>, key: string, label: string, opts: { mono?: boolean; sha?: boolean; value?: string } = {}): void {
  if (!present(args, key)) return;
  used.add(key);
  rows.push({ label, value: opts.value ?? shown(args[key]), ...(opts.mono ? { mono: true } : {}), ...(opts.sha ? { sha: true } : {}) });
}

const RUN_KEYS = ['files', 'views', 'unchecked', 'whose', 'set_sha256', 'downloaded', 'scope'];

function runPart(args: Readonly<Record<string, unknown>>, used: Set<string>, rows: Row[], warnings: string[]): { files: FileRow[]; unchecked: UncheckedRow[] } {
  for (const k of RUN_KEYS) if (present(args, k)) used.add(k);
  pushRow(rows, used, args, 'scope', 'SCOPE', { value: explained(SCOPES, args['scope']) });
  pushRow(rows, used, args, 'whose', 'WHOSE', { value: explained(WHOSE, args['whose']) });
  pushRow(rows, used, args, 'set_sha256', 'SET SHA256', { mono: true, sha: true });
  if (present(args, 'downloaded') && args['downloaded'] !== false) {
    const d = args['downloaded'];
    warnings.push(`Windows marks a file here as downloaded from the internet${d === true ? '' : `: ${shown(d)}`}`);
  }
  return { files: fileRows(args), unchecked: uncheckedRows(args['unchecked']) };
}

export function cardModel(tool: string, args: Readonly<Record<string, unknown>>): CardModel {
  const used = new Set<string>();
  const rows: Row[] = [];
  const more: Row[] = [];
  const warnings: string[] = [];
  const blocks: TextBlock[] = [];
  let files: FileRow[] = [];
  let unchecked: UncheckedRow[] = [];
  let keys: KeysView | null = null;
  let approving: string | null = null;
  let kind: CardKind = 'plain';
  let heading = '';

  if (tool === WRITE_TOOL) {
    kind = 'write';
    heading = 'SHE WILL WRITE THIS';
    pushRow(rows, used, args, 'op', 'OPERATION');
    pushRow(rows, used, args, 'path', 'PATH', { mono: true });
    pushRow(rows, used, args, 'source', 'SOURCE', { mono: true });
    pushRow(rows, used, args, 'why', 'WHY');
    pushRow(more, used, args, 'folder', 'FOLDER', { mono: true });
    pushRow(more, used, args, 'kind', 'KIND');
    pushRow(more, used, args, 'files', 'FILES');
    pushRow(more, used, args, 'bytes', 'BYTES');
    pushRow(more, used, args, 'sha256', 'SHA256', { mono: true, sha: true });
    pushRow(more, used, args, 'hashed', 'HASHED');
    if (present(args, 'text')) {
      used.add('text');
      blocks.push({ label: 'THE WHOLE TEXT', text: shown(args['text']), mode: 'text' });
    }
  } else if (tool === TYPING_TOOL) {
    kind = 'type';
    heading = 'SHE WILL TYPE THIS';
    if (present(args, 'text')) {
      used.add('text');
      blocks.push({ label: 'THE EXACT KEYS SHE TYPES', text: shown(args['text']), mode: 'typed' });
    }
    if (present(args, 'keys')) {
      used.add('keys');
      keys = keysView(args['keys']);
    }
    pushRow(rows, used, args, 'window', 'INTO THE WINDOW');
    pushRow(rows, used, args, 'program', 'PROGRAM', { mono: true });
    pushRow(rows, used, args, 'process', 'PROCESS', { mono: true });
    pushRow(rows, used, args, 'pid', 'PID');
    pushRow(rows, used, args, 'window_class', 'WINDOW CLASS', { mono: true });
    pushRow(rows, used, args, 'focus', 'FOCUSED ELEMENT', { mono: true });
    pushRow(rows, used, args, 'hwnd', 'WINDOW HANDLE', { mono: true });
    pushRow(rows, used, args, 'focus_hwnd', 'ELEMENT HANDLE', { mono: true });
    pushRow(rows, used, args, 'op', 'OPERATION');
    pushRow(rows, used, args, 'why', 'WHY');
    if (present(args, 'pastes') && args['pastes'] !== false) {
      used.add('pastes');
      const p = args['pastes'];
      warnings.push(typeof p === 'string' && p.trim() ? p : 'This pastes whatever is on the clipboard');
    }
  } else if (tool === APP_TOOL) {
    kind = 'app';
    heading = 'SHE WILL OPEN THIS APP';
    pushRow(rows, used, args, 'publisher', 'PUBLISHER');
    const sig = args['signature'];
    if (present(args, 'signature')) {
      used.add('signature');
      if (typeof sig === 'object' && sig !== null && !Array.isArray(sig)) {
        const s = sig as Record<string, unknown>;
        const labels: Readonly<Record<string, string>> = { status: 'SIGNATURE', publisher: 'SIGNED BY', issuer: 'ISSUER', revocation: 'REVOCATION', why: 'SIGNATURE NOTE' };
        for (const k of ['status', 'publisher', 'issuer', 'revocation', 'why']) {
          if (s[k] !== undefined && s[k] !== null && s[k] !== '') rows.push({ label: labels[k] as string, value: shown(s[k]) });
        }
        for (const k of Object.keys(s)) {
          if (!(k in labels)) rows.push({ label: `SIGNATURE ${k.toUpperCase()}`, value: shown(s[k]), mono: typeof s[k] !== 'string' });
        }
      } else {
        rows.push({ label: 'SIGNATURE', value: shown(sig), mono: true });
      }
    }
    if (present(args, 'approving')) {
      used.add('approving');
      approving = shown(args['approving']);
    }
    if (carriesFiles(args)) ({ files, unchecked } = runPart(args, used, rows, warnings));
  } else if (RUN_TOOLS.includes(tool) || carriesFiles(args)) {
    kind = 'run';
    heading = 'SHE WILL RUN THESE FILES';
    ({ files, unchecked } = runPart(args, used, rows, warnings));
  }

  const rest = kind === 'plain' ? [] : restRows(args, used);
  return { kind, frozen: isFrozenCard(tool, args), heading, rows, more, files, unchecked, blocks, keys, warnings, approving, rest };
}

export function shortSha(sha: string): string {
  return /^[0-9a-f]{16,}$/i.test(sha) ? sha.slice(0, 12) : sha;
}

export function bytesText(n: number | null): string {
  if (n === null) return '';
  return `${n.toLocaleString('en-US')} B`;
}

export function hersText(hers: boolean | null): string {
  return hers === true ? 'HERS' : hers === false ? 'NOT HERS' : 'NOT KNOWN';
}

export function uncheckedCount(n: number | null): string {
  return n === null ? 'not counted' : `${n.toLocaleString('en-US')} file${n === 1 ? '' : 's'}`;
}
