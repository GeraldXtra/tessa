import { approvalsStore } from '../state/approval-store.ts';
import { closeQuick, composePatch, composeStore, openQuick, requestComposeFocus } from '../state/compose-store.ts';
import { RAIL_IDS, connectionStore, railStore, type RailId } from '../state/store.ts';
import { escStops, stopHer } from '../state/voice.ts';
import { liveEntries } from './Card.tsx';
import { closePalette, cycleMode, modeStore, openPalette, paletteStore, setMode } from './modes.ts';
import { debugStore, dockShownStore, pushNote } from './stores.ts';

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

function source(e: KeyboardEvent): string {
  return `${chordName(e)} code=${e.code || '(empty)'} keyCode=${e.keyCode} trusted=${e.isTrusted}`;
}

export function cardWaiting(): boolean {
  return liveEntries(approvalsStore.get()).length > 0;
}

export function openRail(id: RailId, report: (line: string) => void, via: string): boolean {
  if (cardWaiting()) {
    report(`KEY ${via} refused: an approval card is waiting, drawers stay closed`);
    return false;
  }
  closePalette(report, `drawer ${id}`);
  if (modeStore.get() !== 'full') setMode('full', report, `${via}, a drawer opens in Full`);
  railStore.set(id);
  return true;
}

export function toggleRail(id: RailId, report: (line: string) => void, via: string): void {
  const shown = railStore.get() === id && !paletteStore.get().open && modeStore.get() === 'full';
  if (shown && !cardWaiting()) {
    railStore.set(null);
    return;
  }
  openRail(id, report, via);
}

export function togglePalette(report: (line: string) => void, via: string): void {
  if (paletteStore.get().open) {
    closePalette(report, via);
    return;
  }
  if (cardWaiting()) {
    report(`KEY ${via} refused: an approval card is waiting, the palette stays closed`);
    pushNote('ORB', 'Answer the approval first.', 'wait');
    return;
  }
  if (modeStore.get() === 'compact') setMode('full', report, `${via}, the palette opens in Full`);
  openPalette(report, via);
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

export function askTessa(text: string, report: (line: string) => void, via: string): void {
  const add = text.trim();
  const draft = composeStore.get().draft;
  const next = draft.length === 0 ? add : `${draft}${/\s$/.test(draft) ? '' : ' '}${add}`;
  composePatch({ draft: next, error: null });
  report(`ASK via=${via}: ${add.length} chars put in the shared draft (${draft.length} chars already there, kept), nothing sent`);
  window.setTimeout(() => startTyping(report, via), 0);
}

export function installKeys(report: (line: string) => void): () => void {
  function onKey(e: KeyboardEvent): void {
    const typing = editable(e.target);
    const onlyCtrl = e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey;
    const noMods = !e.ctrlKey && !e.altKey && !e.metaKey;

    if (onlyCtrl && (e.code === 'KeyK' || (e.code === '' && e.keyCode === 75))) {
      e.preventDefault();
      if (!e.repeat) togglePalette(report, source(e));
      return;
    }

    if (e.key === 'Escape' && noMods && !e.shiftKey) {
      if (paletteStore.get().open) {
        e.preventDefault();
        closePalette(report, 'Escape');
        return;
      }
      if (composeStore.get().quickOpen) {
        e.preventDefault();
        closeQuick();
        report(`KEY Escape closed the quick line, nothing sent, draft kept (${composeStore.get().draft.length} chars)`);
        return;
      }
      if (railStore.get() !== null && modeStore.get() === 'full') {
        e.preventDefault();
        railStore.set(null);
        report('KEY Escape closed the drawer');
        return;
      }
      if (e.target instanceof HTMLTextAreaElement && e.target.dataset['place'] === 'dock') {
        e.target.blur();
        return;
      }
      if (!e.repeat && escStops(connectionStore.get().phase === 'connected')) {
        e.preventDefault();
        stopHer(`Escape ${source(e)}`);
      }
      return;
    }

    if (onlyCtrl && (e.key === 'Enter' || e.key === 'Backspace') && cardWaiting()) {
      const decision = e.key === 'Enter' ? 'approve' : 'deny';
      const via = e.key === 'Enter' ? 'ctrl+enter' : 'ctrl+backspace';
      report(`CARD-ANSWER decision=${decision} via=${via} outcome=refused: no key answers a card, only a click on APPROVE or DENY`);
      if (!typing) e.preventDefault();
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

    if (noMods && !e.shiftKey && (e.code === 'KeyF' || (e.code === '' && e.keyCode === 70))) {
      e.preventDefault();
      if (!e.repeat) cycleMode(report, source(e));
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
