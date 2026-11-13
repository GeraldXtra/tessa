export const RUN_TOOLS: readonly string[] = ['code.run_mine', 'code.run_file'];
export const WRITE_TOOL = 'fs.write_checked';
export const TYPING_TOOL = 'input.type_checked';
export const APP_TOOL = 'app.launch_checked';

export const FROZEN_CARD_TOOLS: readonly string[] = [...RUN_TOOLS, WRITE_TOOL, TYPING_TOOL, APP_TOOL];

export function carriesFiles(args: Readonly<Record<string, unknown>>): boolean {
  const views = args['views'];
  return Array.isArray(args['files']) || (typeof views === 'object' && views !== null && !Array.isArray(views));
}

export function isFrozenCard(tool: string, args: Readonly<Record<string, unknown>>): boolean {
  return FROZEN_CARD_TOOLS.includes(tool) || carriesFiles(args);
}

export const REASKED_CODE = 'permission.pending';

export function isReasked(code: string): boolean {
  return code === REASKED_CODE;
}

export function reaskedLine(tool: string): string {
  return tool === TYPING_TOOL
    ? 'A new card is up. What holds the keyboard changed after you approved, so nothing was typed. Read the new card.'
    : 'A new card is up. Nothing ran from this one. Read the new card.';
}

export const INVISIBLE_INNER = String.raw`\0-\x08\x0B-\x1F\x7F-\x9F\u00A0\u1680\u2000-\u200A\u202F\u205F\u3000\u2028\u2029\u034F\u115F\u1160\u17B4\u17B5\u180B-\u180F\u2800\u3164\uFFA0\uFE00-\uFE0F\u{E0100}-\u{E01EF}\p{Cf}\p{Cn}\p{Cs}`;

const INVISIBLE = new RegExp(`^[${INVISIBLE_INNER}]$`, 'u');
const INVISIBLE_ALL = new RegExp(`[\\t\\n${INVISIBLE_INNER}]`, 'gu');

export function isInvisible(ch: string): boolean {
  return INVISIBLE.test(ch);
}

export function codeName(ch: string): string {
  const cp = ch.codePointAt(0) ?? 0;
  return `U+${cp.toString(16).toUpperCase().padStart(4, '0')}`;
}

export function markInvisibleText(text: string): string {
  return text.replace(INVISIBLE_ALL, (ch) => (ch === '\t' || ch === '\n' ? ' ' : `[${codeName(ch)}]`));
}
