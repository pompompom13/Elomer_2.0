import { useEffect, useMemo, useRef, useState } from 'react'
import type { CityItem } from '../types'
import { people } from '../format'

interface Props {
  cities: CityItem[]
  value: string
  onChange: (slug: string) => void
}

/** Выбор города из справочника: поиск по названию и региону. */
export function CityPicker({ cities, value, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const cur = cities.find((c) => c.slug === value)

  const list = useMemo(() => {
    const ql = q.trim().toLowerCase().replace('ё', 'е')
    const norm = (s: string) => s.toLowerCase().replace('ё', 'е')
    return cities
      .filter((c) => !ql || norm(c.name).includes(ql) || norm(c.region).includes(ql))
      .sort((a, b) => {
        if (ql) {
          const sa = norm(a.name).startsWith(ql) ? 0 : 1, sb = norm(b.name).startsWith(ql) ? 0 : 1
          if (sa !== sb) return sa - sb
        }
        return (b.population ?? 0) - (a.population ?? 0)
      })
      .slice(0, 60)
  }, [cities, q])

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  function pick(slug: string) {
    onChange(slug)
    setOpen(false)
    setQ('')
  }

  return (
    <div className="picker" ref={box}>
      <button type="button" id="city" className="picker-btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span>{cur?.name ?? 'Выберите город'}</span>
        {cur?.region && cur.region !== cur.name && <small>{cur.region}</small>}
      </button>
      {open && (
        <div className="picker-pop" role="dialog" aria-label="Выбор города">
          <input type="text" autoFocus placeholder="Город или регион" value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setOpen(false)
              if (e.key === 'Enter' && list[0]) { e.preventDefault(); pick(list[0].slug) }
            }} />
          <ul role="listbox">
            {list.map((c) => (
              <li key={c.slug} role="option" aria-selected={c.slug === value} onClick={() => pick(c.slug)}>
                <span className="nm">{c.name}</span>
                <span className="rg">{c.region !== c.name ? c.region : ''}</span>
                <span className="stat">
                  {c.loaded ? `${c.lpu} ЛПУ, ${c.pharmacies} аптек` : c.population ? people(c.population) : ''}
                </span>
              </li>
            ))}
            {list.length === 0 && <li className="none">Такого города нет в справочнике. Загрузите свою базу через раздел «База учреждений».</li>}
          </ul>
        </div>
      )}
    </div>
  )
}
