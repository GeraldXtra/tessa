import type { AuditEntry } from '../../shared/ipc-contract.ts';

export const pad2 = (n: number): string => String(n).padStart(2, '0');
export const hm = (d: Date): string => `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
export const hms = (d: Date): string => `${hm(d)}:${pad2(d.getSeconds())}`;

export function hmsOf(iso: string): string {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? hms(new Date(t)) : '--:--:--';
}

export function hmOf(iso: string): string {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? hm(new Date(t)) : '--:--';
}

export function dur(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${pad2(Math.floor(s / 3600))}:${pad2(Math.floor(s / 60) % 60)}:${pad2(s % 60)}`;
}

export function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${pad2(Math.floor(s / 60) % 100)}:${pad2(s % 60)}`;
}

export const naira = (n: number): string => `₦${Math.round(n).toLocaleString('en-NG')}`;

export const MONTHS = [
  'JANUARY',
  'FEBRUARY',
  'MARCH',
  'APRIL',
  'MAY',
  'JUNE',
  'JULY',
  'AUGUST',
  'SEPTEMBER',
  'OCTOBER',
  'NOVEMBER',
  'DECEMBER',
] as const;
export const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'] as const;

export function gb(bytes: number): string {
  return (bytes / 1024 ** 3).toFixed(bytes >= 100 * 1024 ** 3 ? 0 : 1);
}

export type Tier = 'green' | 'amber' | 'red';

export function tierOf(entry: { tier?: string }): Tier | null {
  const t = entry.tier;
  return t === 'green' || t === 'amber' || t === 'red' ? t : null;
}

export const TIER_MEANING: Readonly<Record<Tier, string>> = {
  green: 'Runs unattended, always',
  amber: 'Unattended only inside a job you explicitly authorised',
  red: 'Always requires explicit approval, no exceptions',
};

const BOOKKEEPING = ['auth.', 'daemon.', 'protocol.', 'voice.', 'vault.'];

export function isHerAction(entry: AuditEntry): boolean {
  const tool = entry.tool;
  if (!tool || tool === 'agent.message') return false;
  if (tool.startsWith('pty.')) return entry.actor === 'agent';
  return !BOOKKEEPING.some((prefix) => tool.startsWith(prefix));
}

export type ResultKind = 'ok' | 'held' | 'bad' | 'no';

const RESULTS: readonly (readonly [RegExp, string, ResultKind])[] = [
  [/^APPROVED-OVER-FENCE\b/, 'APPROVED OVER FENCE', 'ok'],
  [/^APPROVED-BUT-FAILED\b/, 'FAILED AFTER YES', 'bad'],
  [/^APPROVED-THEN-CRASHED\b/, 'FAILED AFTER YES', 'bad'],
  [/^APPROVED \(EDITED\)/, 'APPROVED EDITED', 'ok'],
  [/^APPROVED\b/, 'APPROVED', 'ok'],
  [/^APPROVAL REFUSED\b/, 'REFUSED', 'bad'],
  [/^APPROVAL FAILED\b/, 'FAILED', 'bad'],
  [/^PENDING-APPROVAL\b/, 'CARD RAISED', 'held'],
  [/^REQUESTED\b/, 'REQUESTED', 'ok'],
  [/^HOLD-DROPPED\b/, 'DROPPED', 'no'],
  [/^HELD\b/, 'HELD', 'held'],
  [/^CANCELLED\b/, 'CANCELLED', 'no'],
  [/^REFUSED-HOLD\b/, 'REFUSED', 'bad'],
  [/^REFUSED-VOICEPRINT\b/, 'REFUSED', 'bad'],
  [/^REFUSED\b/, 'REFUSED', 'bad'],
  [/^SCHEDULED FAILED\b/, 'FAILED', 'bad'],
  [/^FAILED\b/, 'FAILED', 'bad'],
  [/^SPEC-BUG\b/, 'SPEC BUG', 'bad'],
  [/^PLAN-REFUSED\b/, 'REFUSED', 'bad'],
  [/^PLAN-ENDED\b/, 'ENDED', 'ok'],
  [/^PLAN\b/, 'PLANNED', 'ok'],
  [/^INJECTION-SEEN\b/, 'INJECTION SEEN', 'bad'],
  [/^CLEARED-EXTERNAL\b/, 'CLEARED', 'ok'],
  [/^STRIPPED-FORGEABLE\b/, 'STRIPPED', 'bad'],
  [/^DENIED\b/, 'DENIED', 'no'],
  [/^EXPIRED\b/, 'EXPIRED', 'no'],
  [/^Awaiting owner approval\b/, 'ASKED YOU', 'held'],
  [/^ran(\(legacy\))?\s/, 'DONE', 'ok'],
];

export function resultOf(summary: string): { word: string; kind: ResultKind } | null {
  for (const [re, word, kind] of RESULTS) if (re.test(summary)) return { word, kind };
  return null;
}

export function seqOf(entry: AuditEntry): number {
  const n = Number(entry.id);
  return Number.isFinite(n) ? n : -1;
}

export function bytesText(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.round(n / 1024)} KB`;
}
