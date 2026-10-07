import { approvalsStore } from '../state/approval-store.ts';
import { closeQuick, composeStore, openQuick, requestComposeFocus } from '../state/compose-store.ts';
import { RAIL_IDS, connectionStore, railStore, type RailId } from '../state/store.ts';
import { answerCard, cardFieldHasFocus, liveEntries } from './Card.tsx';
import { debugStore, dockShownStore } from './stores.ts';

function editable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

function chordName(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.ctrlKey) parts.push('Ctrl');
  if (e.altKey) parts.push('Alt');
  if (e.shiftKey) parts.push('Shift');
  if (e.metaKey) parts.push('Meta');
  parts.push(e.key === ' ' ? 'Space' : e.key);
  return parts.join('+');
}

export function cardWaiting(): boolean {
  return liveEntries(approvalsStore.get()).length > 0;
}

export function toggleRail(id: RailId, report: (line: string) => void, via: string): void {
  if (cardWaiting()) {
    report(`KEY ${via} refused: an approval card is waiting, drawers stay closed`);
    return;
  }
  railStore.set(railStore.get() === id ? null : id);
}

export function startTyping(report: (line: string) => void, via: string): void {
  if (dockShownStore.get()) {
    requestComposeFocus('dock');
    report(`KEY ${via} -> chat dock${connectionStore.get().phase === 'connected' ? '' : ' (its input is disabled: no daemon link)'}`);
    return;
  }
  openQuick();
  report(`KEY ${via} -> quick line${cardWaiting() ? ' (an approval card is waiting; the quick line cannot answer it)' : ''}`);
}

export function installKeys(report: (line: string) => void): () => void {
  function onKey(e: KeyboardEvent): void {
    const typing = editable(e.target);
    const inCardField = typing && cardFieldHasFocus();
    const onlyCtrl = e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey;
    const noMods = !e.ctrlKey && !e.altKey && !e.metaKey;

    if (e.key === 'Escape' && noMods && !e.shiftKey) {
      if (composeStore.get().quickOpen) {
        e.preventDefault();
        closeQuick();
        report(`KEY Escape closed the quick line, nothing sent, draft kept (${composeStore.get().draft.length} chars)`);
        return;
      }
      if (railStore.get() !== null) {
        e.preventDefault();
        railStore.set(null);
        return;
      }
      if (e.target instanceof HTMLTextAreaElement && e.target.dataset['place'] === 'dock') e.target.blur();
      return;
    }

    if (onlyCtrl && (e.key === 'Enter' || e.key === 'Backspace') && cardWaiting()) {
      const decision = e.key === 'Enter' ? 'approve' : 'deny';
      const via = e.key === 'Enter' ? 'ctrl+enter' : 'ctrl+backspace';
      if (typing && !inCardField) {
        report(`CARD-ANSWER decision=${decision} via=${via} outcome=refused: focus is in a text input outside the card`);
        return;
      }
      if (inCardField && decision === 'deny') {
        report(`CARD-ANSWER decision=deny via=${via} outcome=refused: inside the card's edit field Ctrl+Backspace deletes a word`);
        return;
      }
      e.preventDefault();
      answerCard(decision, via);
      return;
    }

    if (typing) return;

    if (e.altKey && !e.ctrlKey && !e.shiftKey && !e.metaKey) {
      const digit = /^Digit([1-8])$/.exec(e.code)?.[1] ?? (/^[1-8]$/.test(e.key) ? e.key : null);
      if (digit) {
        e.preventDefault();
        const id = RAIL_IDS[Number.parseInt(digit, 10) - 1];
        if (id) toggleRail(id, report, `Alt+${digit}`);
        return;
      }
    }

    if (noMods && !e.repeat && e.key === '/') {
      e.preventDefault();
      startTyping(report, '/');
      return;
    }

    const space = e.code === 'Space' || e.key === ' ';
    if (e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey && space) {
      e.preventDefault();
      startTyping(report, chordName(e));
      return;
    }

    if (noMods && !e.shiftKey && !e.repeat && (e.key === '`' || e.code === 'Backquote')) {
      e.preventDefault();
      debugStore.set(!debugStore.get());
    }
  }
  window.addEventListener('keydown', onKey);
  return () => window.removeEventListener('keydown', onKey);
}
