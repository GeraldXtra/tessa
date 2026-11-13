import { useEffect, useMemo, useRef, useState } from 'react'

import { Brackets } from './Menu.tsx'

export interface PaletteItem {
  group: string
  label: string
  keycap: string
  run: () => void
}

export function Palette({ items, onClose }: { items: PaletteItem[]; onClose: () => void }): React.JSX.Element {
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const list = useMemo(() => {
    const s = q.toLowerCase().trim()
    return items.filter((it) => !s || `${it.group} ${it.label}`.toLowerCase().includes(s))
  }, [items, q])
  useEffect(() => {
    inputRef.current?.focus()
  }, [])
  useEffect(() => {
    listRef.current?.querySelector('.sel')?.scrollIntoView({ block: 'nearest' })
  }, [i, list])
  const runAt = (k: number): void => {
    const it = list[k]
    onClose()
    if (it) it.run()
  }
  let group = ''
  return (
    <div className="menu pal" role="dialog" aria-label="Command palette">
      <Brackets />
      <div className="pin">
        <span>&gt;</span>
        <input
          ref={inputRef}
          className="pal-in"
          value={q}
          placeholder="Type a command"
          autoComplete="off"
          aria-label="Command"
          onChange={(e) => {
            setQ(e.target.value)
            setI(0)
          }}
          onKeyDown={(e) => {
            const n = list.length
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              setI((v) => (v + 1) % Math.max(n, 1))
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              setI((v) => (v - 1 + Math.max(n, 1)) % Math.max(n, 1))
            } else if (e.key === 'Enter') {
              e.preventDefault()
              runAt(i)
            } else if (e.key === 'Escape') {
              e.preventDefault()
              onClose()
            }
          }}
        />
        <span className="lab">ESC CLOSES</span>
      </div>
      <div className="list" ref={listRef}>
        {list.length === 0 ? <div className="gh lab">NOTHING MATCHES</div> : null}
        {list.map((it, k) => {
          const head = it.group !== group ? <div className="gh lab" key={`g-${it.group}`}>{it.group}</div> : null
          group = it.group
          return (
            <div key={`${it.group}-${it.label}`}>
              {head}
              <button className={`it${k === i ? ' sel' : ''}`} onMouseEnter={() => setI(k)} onClick={() => runAt(k)}>
                <span className="k" />
                <span>{it.label}</span>
                {it.keycap ? <em>{it.keycap}</em> : <em className="none" />}
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}
