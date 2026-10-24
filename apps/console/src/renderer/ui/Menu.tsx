import { useEffect, useLayoutEffect, useRef, useState } from 'react'

export function Brackets(): React.JSX.Element {
  return (
    <div className="brk">
      <i className="tl" />
      <i className="tr" />
      <i className="bl" />
      <i className="br" />
    </div>
  )
}

export function Kbd({ k }: { k: string }): React.JSX.Element | null {
  if (!k) return null
  return <kbd>{k}</kbd>
}

export interface MenuItem {
  tag: string
  label: string
  sub?: string
  keycap?: string
  disabled?: boolean
  run: () => void
}

export interface MenuSpec {
  kind: 'picker' | 'tab' | 'block'
  x: number
  y: number
  width: number
  head: [string, string]
  groups: MenuItem[][]
}

export function Menu({
  spec,
  winW,
  winH,
  onClose,
  activeIndex,
  setActiveIndex,
}: {
  spec: MenuSpec
  winW: number
  winH: number
  onClose: () => void
  activeIndex: number
  setActiveIndex: (i: number) => void
}): React.JSX.Element {
  const ref = useRef<HTMLDivElement | null>(null)
  const [pos, setPos] = useState({ left: spec.x, top: spec.y })
  useLayoutEffect(() => {
    const h = ref.current?.offsetHeight ?? 0
    setPos({
      left: Math.max(8, Math.min(spec.x, winW - spec.width - 8)),
      top: Math.max(46, Math.min(spec.y, Math.max(46, winH - h - 36))),
    })
  }, [spec.x, spec.y, spec.width, winW, winH])
  useEffect(() => {
    const down = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    window.addEventListener('mousedown', down, true)
    return () => window.removeEventListener('mousedown', down, true)
  }, [onClose])
  let flat = -1
  return (
    <div ref={ref} className="menu" role="menu" style={{ left: pos.left, top: pos.top, width: spec.width }}>
      <Brackets />
      <div className="mh">
        <span className="lab">{spec.head[0]}</span>
        <span className="lab">{spec.head[1]}</span>
      </div>
      {spec.groups.map((g, gi) => (
        <div key={gi}>
          {gi > 0 ? <div className="sep" /> : null}
          {g.map((it) => {
            if (!it.disabled) flat++
            const myIndex = it.disabled ? -1 : flat
            return (
              <button
                key={`${it.tag}-${it.label}`}
                className={`it${myIndex === activeIndex ? ' sel' : ''}`}
                role="menuitem"
                disabled={it.disabled}
                onMouseEnter={() => myIndex >= 0 && setActiveIndex(myIndex)}
                onClick={() => {
                  if (it.disabled) return
                  onClose()
                  it.run()
                }}
              >
                <span className="k">{it.tag}</span>
                <span>
                  {it.label}
                  {it.sub ? <small>{it.sub}</small> : null}
                </span>
                {it.keycap ? <em>{it.keycap}</em> : <em className="none" />}
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}

export function enabledItems(spec: MenuSpec): MenuItem[] {
  return spec.groups.flat().filter((i) => !i.disabled)
}
