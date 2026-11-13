import type { OrbNotification } from '../../shared/ipc-contract.ts';
import type { NoteKind } from './stores.ts';

export interface NoteView {
  src: string;
  msg: string;
  kind: NoteKind;
  sticky: boolean;
}

export function noteFor(note: OrbNotification): NoteView {
  if (note.from === 'daemon') {
    const msg = note.title && note.body ? `${note.title}. ${note.body}` : note.title || note.body;
    return {
      src: note.level === 'info' ? 'DAEMON' : `DAEMON ${note.level.toUpperCase()}`,
      msg,
      kind: note.level === 'error' ? 'threat' : note.level === 'warn' ? 'wait' : '',
      sticky: note.level === 'error',
    };
  }
  return { src: note.level === 'info' ? 'ORB' : note.level.toUpperCase(), msg: `${note.title}. ${note.body}`, kind: '', sticky: false };
}

export interface NoteLike {
  id: number;
  until: number;
  sticky: boolean;
  cleared: number | null;
}

export function noteLive(n: NoteLike, now: number, fadeMs: number): boolean {
  if (n.sticky && n.cleared === null) return true;
  return Math.min(n.until, n.cleared ?? Number.POSITIVE_INFINITY) + fadeMs > now;
}

export function noteStack<T extends NoteLike>(notes: readonly T[], now: number, fadeMs: number, max: number): { shown: T[]; more: number } {
  const live = notes.filter((n) => noteLive(n, now, fadeMs));
  const pinned = live.filter((n) => n.sticky && n.cleared === null);
  const rest = live.filter((n) => !(n.sticky && n.cleared === null));
  const ordered = [...pinned, ...rest];
  const shown = ordered.slice(0, Math.max(max, pinned.length));
  return { shown, more: ordered.length - shown.length };
}
