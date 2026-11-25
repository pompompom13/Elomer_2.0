import { useMemo, useState } from 'react'
import type { CityData, Filters, NetItem, Ownership } from '../types'
import { num, plural } from '../format'
import { CareLevelField, countForCare } from './CareLevelField'

export const ALL_CATEGORIES: { key: string; label: string }[] = [
  { key: 'polyclinic_adult', label: 'Поликлиники' },
  { key: 'polyclinic_child', label: 'Детские' },
  { key: 'hospital', label: 'Больницы' },
  { key: 'private_center', label: 'Медцентры' },
  { key: 'cardio_center', label: 'Профильные центры' },
  { key: 'womens', label: 'Женские консультации' },
  { key: 'dental', label: 'Стоматологии' },
]

export const DEFAULT_FILTERS: Filters = {
  lpu_care: 'all',
  categories: ALL_CATEGORIES.map((c) => c.key),
  lpu_ownership: ['state', 'private'],
  pharmacy_ownership: ['state', 'private'],
  lpu_networks: null,
  pharmacy_networks: null,
  pharmacy_min_turnover: null,
}

/** Пороги оборота аптеки (юрлицо в расчёте на аптеку, руб. в месяц). */
export const TURNOVER_STEPS: { v: number | null; label: string }[] = [
  { v: null, label: 'любой' },
  { v: 500_000, label: 'от 0,5 млн ₽' },
  { v: 1_000_000, label: 'от 1 млн ₽' },
  { v: 2_000_000, label: 'от 2 млн ₽' },
  { v: 3_000_000, label: 'от 3 млн ₽' },
  { v: 5_000_000, label: 'от 5 млн ₽' },
]

interface Props {
  city: CityData | null
  filters: Filters
  onChange: (f: Filters) => void
  specialty: string
}

export const SRC: Record<string, string> = {
  city: 'по статистике врачей города',
  region: 'по статистике врачей региона',
  prodoctorov: 'по ПроДокторов, приведённым к масштабу статистики',
  density: 'по плотности врачей на жителя (города с данными ПроДокторов)',
  estimate: 'оценка по типам учреждений',
}

const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

/** Какие учреждения попадут в план: тип, собственность, сети. */
export function FiltersPanel({ city, filters: f, onChange, specialty }: Props) {
  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => onChange({ ...f, [k]: v })

  const counts = useMemo(() => {
    const r = { lpu: { state: 0, private: 0 }, pharmacy: { state: 0, private: 0 }, docs: 0 }
    if (!city) return r
    for (const x of city.facilities) {
      if (x.needs_coords) continue
      const ownership = x.ownership ?? 'private'
      if (x.kind === 'lpu') {
        if (!f.categories.includes(x.category === 'ambulatory' ? 'polyclinic_adult' : x.category)) continue
        if (ownership === 'private' && f.lpu_networks && !f.lpu_networks.includes(x.network ?? '')) continue
        const estimate = countForCare(x.est ?? 0, x.care_level, f.lpu_care)
        if (!estimate) continue
        r.lpu[ownership]++
        if (f.lpu_ownership.includes(ownership)) r.docs += estimate
      } else {
        if (x.in_registry === false) continue
        if (f.pharmacy_networks && !f.pharmacy_networks.includes(x.network ?? '')) continue
        if (f.pharmacy_min_turnover && (x.turnover ?? 0) < f.pharmacy_min_turnover) continue
        r.pharmacy[ownership]++
      }
    }
    return r
  }, [city, f])
  const selectedLpu = f.lpu_ownership.reduce((total, value) => total + counts.lpu[value], 0)
  const selectedPharmacies = f.pharmacy_ownership.reduce((total, value) => total + counts.pharmacy[value], 0)

  const own = (key: 'lpu_ownership' | 'pharmacy_ownership', v: Ownership, label: string, n: number) => (
    <label className="chk">
      <input type="checkbox" checked={f[key].includes(v)}
        onChange={() => { const next = toggle(f[key], v); if (next.length) set(key, next) }} />
      {label} <span className="n" title="Подходят под остальные фильтры">{num(n, 0)}</span>
    </label>
  )

  return (
    <div className="filters">
      <div className="reset-row" style={{ margin: '0 0 4px' }}>
        <button type="button" className="linklike" onClick={() => onChange(DEFAULT_FILTERS)}>Сбросить фильтры учреждений</button>
      </div>
      <h3>Медицинские учреждения</h3>
      <CareLevelField value={f.lpu_care} onChange={(value) => set('lpu_care', value)} hint />
      <div className="chk-row">
        {own('lpu_ownership', 'state', 'Государственные', counts.lpu.state)}
        {own('lpu_ownership', 'private', 'Частные', counts.lpu.private)}
      </div>
      <div className="chips" role="group" aria-label="Типы учреждений">
        {ALL_CATEGORIES.map((c) => (
          <button type="button" key={c.key} aria-pressed={f.categories.includes(c.key)}
            onClick={() => { const next = toggle(f.categories, c.key); if (next.length) set('categories', next) }}>
            {c.label}
          </button>
        ))}
      </div>
      {f.lpu_ownership.includes('private') && (
        <NetworkPicker title="Сети частных клиник" items={city?.networks.lpu ?? []}
          value={f.lpu_networks} onChange={(v) => set('lpu_networks', v)} />
      )}

      <h3>Аптеки</h3>
      <div className="chk-row">
        {own('pharmacy_ownership', 'state', 'Государственные', counts.pharmacy.state)}
        {own('pharmacy_ownership', 'private', 'Частные', counts.pharmacy.private)}
      </div>
      <NetworkPicker title="Аптечные сети" items={city?.networks.pharmacy ?? []}
        value={f.pharmacy_networks} onChange={(v) => set('pharmacy_networks', v)} />
      <div className="field inline-field">
        <label htmlFor="fturn">Оборот аптеки в месяц</label>
        <select id="fturn" value={f.pharmacy_min_turnover ?? ''}
          onChange={(e) => set('pharmacy_min_turnover', e.target.value === '' ? null : Number(e.target.value))}>
          {TURNOVER_STEPS.map((t) => <option key={t.label} value={t.v ?? ''}>{t.label}</option>)}
        </select>
      </div>

      <p className="result">
        Под фильтры подходят {num(selectedLpu, 0)} ЛПУ (около {num(counts.docs, 0)}{' '}
        {plural(counts.docs, 'врача', 'врачей', 'врачей')}, {specialty.toLowerCase()}) и {num(selectedPharmacies, 0)}{' '}
        {plural(selectedPharmacies, 'аптека', 'аптеки', 'аптек')}.
      </p>
    </div>
  )
}

function NetworkPicker({ title, items, value, onChange }: {
  title: string; items: NetItem[]; value: string[] | null; onChange: (v: string[] | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const chains = items.filter((i) => i.key && i.count > 1)
  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase()
    const list = ql ? items.filter((i) => i.name.toLowerCase().includes(ql)) : chains.concat(items.filter((i) => !i.key))
    return list.slice(0, 150)
  }, [items, chains, q])
  const selected = value ?? []
  const summary = value === null ? 'все' : value.length === 0 ? 'ни одной' :
    value.length <= 2 ? value.map((k) => items.find((i) => i.key === k)?.name ?? k).join(', ') : `выбрано ${value.length}`

  return (
    <div className="netpick">
      <button type="button" className="netpick-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span>{title}</span><b>{summary}</b>
      </button>
      {open && (
        <div className="netpick-body">
          <div className="netpick-mode">
            <label className="chk"><input type="radio" checked={value === null} onChange={() => onChange(null)} />Все</label>
            <label className="chk"><input type="radio" checked={value !== null} onChange={() => onChange(value ?? [])} />Только выбранные</label>
          </div>
          {value !== null && (
            <>
              <input type="text" placeholder="Найти сеть" value={q} onChange={(e) => setQ(e.target.value)} />
              <ul>
                {shown.map((i) => (
                  <li key={i.key || '_none'}>
                    <label className="chk">
                      <input type="checkbox" checked={selected.includes(i.key)}
                        onChange={() => onChange(toggle(selected, i.key))} />
                      <span className="nm">{i.key ? i.name : 'Без названия сети'}</span>
                      <span className="n">{i.count}</span>
                    </label>
                  </li>
                ))}
              </ul>
              <div className="netpick-foot">
                <button type="button" className="linklike" onClick={() => onChange(chains.map((c) => c.key))}>Все сети от 2 точек</button>
                <button type="button" className="linklike" onClick={() => onChange([])}>Снять все</button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
