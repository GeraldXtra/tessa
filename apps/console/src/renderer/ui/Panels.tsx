import type { ShellInfo, SettingsView } from '../../preload/index.ts'
import { Kbd } from './Menu.tsx'

function Seg<T extends string>({ opts, cur, set }: { opts: [T, string][]; cur: T | ''; set: (v: T) => void }): React.JSX.Element {
  return (
    <div className="seg">
      {opts.map(([v, l]) => (
        <button key={v} className={cur === v ? 'on' : ''} onClick={() => set(v)}>
          {l}
        </button>
      ))}
    </div>
  )
}

export function SettingsPanel({
  shells,
  settings,
  fontSize,
  onFont,
  onPatch,
}: {
  shells: ShellInfo[]
  settings: SettingsView
  fontSize: number
  onFont: (d: number) => void
  onPatch: (p: Partial<SettingsView>) => void
}): React.JSX.Element {
  return (
    <aside className="set" role="dialog" aria-label="Settings">
      <div className="sh">
        <b>SETTINGS</b>
        <span className="lab">ESC CLOSES</span>
      </div>
      <div className="sbd">
        <div className="sec">
          <div className="lab">SHELLS</div>
          {shells.map((s) => (
            <div key={s.id} className={`pf${s.available ? '' : ' miss'}`}>
              <span className="k">{s.tag}</span>
              <span>{s.name}{s.version && s.name.indexOf(s.version) < 0 ? ` ${s.version}` : ''}</span>
              <span className="lab">{s.id === settings.defaultShell ? 'DEFAULT' : s.available ? '' : 'NOT FOUND'}</span>
            </div>
          ))}
        </div>
        <div className="sec">
          <div className="lab">LOOK</div>
          <div className="sr">
            <span>
              Text size<small>{fontSize} px</small>
            </span>
            <div className="seg">
              <button onClick={() => onFont(-1)}>A&minus;</button>
              <button onClick={() => onFont(1)}>A+</button>
            </div>
          </div>
          <div className="sr">
            <span>
              Output colours<small>Standard keeps git and error colours. Mono makes all output grey.</small>
            </span>
            <Seg opts={[['standard', 'STANDARD'], ['mono', 'MONO']]} cur={settings.outputColours} set={(v) => onPatch({ outputColours: v })} />
          </div>
          <div className="sr">
            <span>
              Prompt<small>Tessa&rsquo;s is one short line in every shell. Shells opened after a change use it.</small>
            </span>
            <Seg opts={[['tessa', 'TESSA'], ['shell', 'SHELL’S OWN']]} cur={settings.prompt} set={(v) => onPatch({ prompt: v })} />
          </div>
          <div className="sr">
            <span>
              Cursor<small>It never blinks.</small>
            </span>
            <Seg opts={[['block', 'BLOCK'], ['bar', 'BAR'], ['underline', 'LINE']]} cur={settings.cursorStyle} set={(v) => onPatch({ cursorStyle: v })} />
          </div>
        </div>
        <div className="sec">
          <div className="lab">SAFETY</div>
          <div className="sr">
            <span>
              Paste guard<small>Ask before pasting more than one line into a shell that runs each line as it arrives.</small>
            </span>
            <Seg opts={[['on', 'ON'], ['off', 'OFF']]} cur={settings.pasteGuard ? 'on' : 'off'} set={(v) => onPatch({ pasteGuard: v === 'on' })} />
          </div>
        </div>
        <p className="hint">The Console is black only. Every key is listed next to its action in the palette, Ctrl Shift P. Scrollback keeps {settings.scrollback.toLocaleString('en-GB')} lines per tab.</p>
      </div>
    </aside>
  )
}

export interface Note {
  id: number
  label: string
  time: string
  text: string
  kind: '' | 'wait' | 'threat'
  out: boolean
}

export function Notes({ notes }: { notes: Note[] }): React.JSX.Element {
  return (
    <div className="toasts" aria-live="polite">
      {notes.map((n) => (
        <div key={n.id} className={`toast${n.kind ? ` ${n.kind}` : ''}${n.out ? ' out' : ''}`}>
          <div className="lab">
            <span>{n.label}</span>
            <span>{n.time}</span>
          </div>
          {n.text}
        </div>
      ))}
    </div>
  )
}

export function StartScreen({
  closedCount,
  shells,
  recent,
  keyOf,
  onReopen,
  onNew,
  onFolder,
}: {
  closedCount: number
  shells: ShellInfo[]
  recent: string[]
  keyOf: (shellId: string) => string
  onReopen: () => void
  onNew: (shellId: string) => void
  onFolder: (f: string) => void
}): React.JSX.Element {
  return (
    <div className="empty">
      <div className="wm">TESSA</div>
      <p>Nothing open. Pick up where you left off, or start something new.</p>
      <div className="acts">
        {closedCount > 0 ? (
          <button className="act main" onClick={onReopen}>
            <span>
              Reopen {closedCount} tab{closedCount > 1 ? 's' : ''} as they were
            </span>
            <Kbd k="ENTER" />
          </button>
        ) : null}
        {shells
          .filter((s) => s.available)
          .map((s) => (
            <button key={s.id} className="act" onClick={() => onNew(s.id)}>
              <span>New {s.name} tab</span>
              <Kbd k={keyOf(s.id)} />
            </button>
          ))}
      </div>
      {recent.length ? (
        <div className="recent">
          <span className="lab">RECENT FOLDERS</span>
          {recent.map((f) => (
            <button key={f} title="New PowerShell tab here" onClick={() => onFolder(f)}>
              {f}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
