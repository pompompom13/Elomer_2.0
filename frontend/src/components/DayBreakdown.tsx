import type { Day, SegType } from '../types'

const PARTS: { key: string; label: string; types: SegType[]; cls: string }[] = [
  { key: 'visit', label: 'Разговор с врачами', types: ['visit'], cls: 'b-visit' },
  { key: 'pharmacy', label: 'Визиты в аптеки', types: ['pharmacy'], cls: 'b-pharmacy' },
  { key: 'wait', label: 'Ожидание врача и в аптеке', types: ['wait'], cls: 'b-wait' },
  { key: 'entry', label: 'Регистратура, пропуск', types: ['entry'], cls: 'b-entry' },
  { key: 'travel', label: 'Дорога', types: ['travel'], cls: 'b-travel' },
  { key: 'other', label: 'Обед и отчёт в CRM', types: ['lunch', 'crm'], cls: 'b-other' },
]

const fmt = (m: number) => {
  const r = Math.round(m)
  return r >= 60 ? `${Math.floor(r / 60)} ч ${String(r % 60).padStart(2, '0')} мин` : `${r} мин`
}

/** Из чего складывается рабочий день: доли по видам деятельности. */
export function DayBreakdown({ day, limitH }: { day: Day; limitH: number }) {
  const sums = PARTS.map((p) => ({
    ...p, min: day.segments.filter(([t]) => p.types.includes(t)).reduce((a, [, , d]) => a + d, 0),
  })).filter((p) => p.min > 0)
  const total = sums.reduce((a, p) => a + p.min, 0)
  const scale = Math.max(total, limitH * 60)
  return (
    <div className="breakdown">
      <div className="bd-bar" aria-hidden="true">
        {sums.map((p) => <span key={p.key} className={p.cls} style={{ width: `${(p.min / scale) * 100}%` }} />)}
        <i className="bd-limit" style={{ left: `${((limitH * 60) / scale) * 100}%` }}>
          <em>норма {limitH} ч</em>
        </i>
      </div>
      <ul className="bd-list">
        {sums.map((p) => (
          <li key={p.key}>
            <i className={p.cls} />
            <span>{p.label}</span>
            <b>{fmt(p.min)}</b>
            <span className="pct">{Math.round((p.min / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
