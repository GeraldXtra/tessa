import { Fragment, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { MUTE_WHY, type OrbMode } from '../../shared/ipc-contract.ts';
import { companionStore, useStore } from '../state/store.ts';
import { THEME_IDS, type ThemeId } from '../theme.ts';
import { askTessa, openRail } from './keys.ts';
import { closePalette, paletteStore, setMode, setPaletteQuery } from './modes.ts';
import { DRAWERS } from './Rails.tsx';
import { pinsStore, runPinned, runThemeSwitch } from './stores.ts';

type Group = 'OPEN' | 'MODE' | 'THEME' | 'VOICE' | 'PINNED' | 'ASK';

interface Command {
  g: Group;
  label: string;
  key: string;
  run: (() => void) | null;
  why: string;
}

const THEME_LABEL: Readonly<Record<ThemeId, string>> = {
  gold: 'Gold',
  magenta: 'Magenta',
  cyan: 'Cyan',
  violet: 'Violet',
  emerald: 'Emerald',
  red: 'Red',
  ultron: 'Ultron',
};

const MODES: readonly (readonly [OrbMode, string])[] = [
  ['full', 'Full'],
  ['ambient', 'Ambient'],
  ['compact', 'Compact'],
];

export const QUIET_WHY = 'Needs the daemon: quiet hours are not built there yet.';
export const NO_PINS_WHY = 'Pinned commands are not built.';

const report = (line: string): void => window.tessa.reportMetrics(line);

function commandList(query: string, pins: readonly string[], name: string): Command[] {
  const list: Command[] = [];
  DRAWERS.forEach((d, i) =>
    list.push({ g: 'OPEN', label: `Open ${d.name}`, key: `ALT ${i + 1}`, run: () => openRail(d.id, report, `palette:${d.id}`), why: '' }),
  );
  for (const [mode, label] of MODES) {
    list.push({ g: 'MODE', label: `${label} mode`, key: 'F', run: () => setMode(mode, report, 'palette'), why: '' });
  }
  THEME_IDS.forEach((t, i) =>
    list.push({ g: 'THEME', label: `${THEME_LABEL[t]} theme`, key: `CTRL SHIFT ${i + 1}`, run: () => runThemeSwitch(t, 'palette'), why: '' }),
  );
  list.push({ g: 'VOICE', label: 'Mute the mic', key: '', run: null, why: MUTE_WHY });
  list.push({ g: 'VOICE', label: 'Start quiet hours', key: '', run: null, why: QUIET_WHY });
  if (pins.length > 0) {
    pins.forEach((p, i) => list.push({ g: 'PINNED', label: p, key: '', run: () => runPinned(p, report, `palette:${i + 1}`), why: '' }));
  } else {
    list.push({ g: 'PINNED', label: 'No pinned commands yet', key: '', run: null, why: NO_PINS_WHY });
  }
  const q = query.trim().toLowerCase();
  const out = q ? list.filter((c) => c.label.toLowerCase().includes(q) || c.g.toLowerCase().includes(q)) : list;
  if (q) out.push({ g: 'ASK', label: `Ask ${name}: ${query.trim()}`, key: 'ENTER', run: () => askTessa(query, report, 'palette'), why: '' });
  return out;
}

export function Palette({ width }: { width: number }) {
  const palette = useStore(paletteStore);
  const pins = useStore(pinsStore);
  const companion = useStore(companionStore);
  const commands = useMemo(() => commandList(palette.query, pins, companion.name), [palette.query, pins, companion.name]);
  const runnable = commands.flatMap((c, i) => (c.run ? [i] : []));
  const [pos, setPos] = useState(0);
  const sel = runnable.length > 0 ? (runnable[Math.min(pos, runnable.length - 1)] ?? -1) : -1;
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    inputRef.current?.focus();
    report(`PALETTE focus=${document.activeElement === inputRef.current ? 'input' : (document.activeElement?.tagName ?? 'none')} rows=${commands.length}`);
  }, [palette.opened]);

  useLayoutEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [sel]);

  const run = (i: number, via: string): void => {
    const c = commands[i];
    if (!c) return;
    if (!c.run) {
      report(`PALETTE "${c.label}" not run via=${via}: ${c.why}`);
      return;
    }
    report(`PALETTE run "${c.label}" group=${c.g} via=${via}`);
    closePalette(report, 'ran');
    c.run();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    const plain = !e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey;
    if (plain && e.key === 'ArrowDown') {
      e.preventDefault();
      e.stopPropagation();
      setPos(Math.min(Math.max(0, runnable.length - 1), pos + 1));
    } else if (plain && e.key === 'ArrowUp') {
      e.preventDefault();
      e.stopPropagation();
      setPos(Math.max(0, pos - 1));
    } else if (plain && e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      run(sel, 'enter');
    } else if (plain && e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closePalette(report, 'Escape');
    }
  };

  let group: Group | '' = '';
  return (
    <section className="slot pal br" style={{ width }} role="dialog" aria-label="Command palette">
      <div className="pi">
        <span className="p">{'>'}</span>
        <input
          ref={inputRef}
          type="text"
          value={palette.query}
          placeholder={`Type a command, or ask ${companion.name}`}
          aria-label="Command"
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            setPaletteQuery(e.target.value);
            setPos(0);
          }}
          onKeyDown={onKeyDown}
        />
        <kbd>ESC</kbd>
      </div>
      <div className="pl" ref={listRef} role="listbox" aria-label="Commands">
        {commands.map((c, i) => {
          const head = c.g !== group;
          group = c.g;
          return (
            <Fragment key={`${c.g}-${c.label}`}>
              {head ? <div className="gh lab">{c.g}</div> : null}
              <button
                type="button"
                className="it"
                role="option"
                aria-selected={i === sel}
                aria-disabled={c.run === null}
                data-cmd={c.label}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => run(i, 'click')}
              >
                <span>{c.label}</span>
                <em>{c.key}</em>
                {c.why ? <span className="why">{c.why}</span> : null}
              </button>
            </Fragment>
          );
        })}
      </div>
    </section>
  );
}
