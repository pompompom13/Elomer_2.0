import { NumInput } from './NumInput'
import type { ExtraSpec } from '../types'

interface Props {
  main: string                // основная специальность — её нельзя выбрать повторно
  specialties: string[]
  value: ExtraSpec[]
  onChange: (v: ExtraSpec[]) => void
}

/** «Кардиолог» → «кардиологи», «Терапевт» → «терапевты». */
const plural = (s: string) => s.split('-').map((w) => w.toLowerCase() + (/[гкхжшчщ]$/i.test(w) ? 'и' : 'ы')).join('-')

/** Дополнительные специальности: те же медпреды в те же дни посещают и других врачей. */
export function ExtraSpecs({ main, specialties, value, onChange }: Props) {
  const used = new Set([main, ...value.map((x) => x.specialty)])
  const free = specialties.filter((s) => !used.has(s))
  const set = (i: number, patch: Partial<ExtraSpec>) => onChange(value.map((x, j) => (j === i ? { ...x, ...patch } : x)))

  return (
    <>
      {value.map((x, i) => (
        <div className="visit-row extra" key={i}>
          <span className="swatch" style={{ background: 'var(--doc)', opacity: 0.55 }} />
          <span className="extra-name">
            <select aria-label="Дополнительная специальность" value={x.specialty}
              onChange={(e) => set(i, { specialty: e.target.value })}>
              {[x.specialty, ...free].map((s) => <option key={s}>{s}</option>)}
            </select>
            <button type="button" className="extra-del" aria-label={`Убрать ${x.specialty}`}
              onClick={() => onChange(value.filter((_, j) => j !== i))}>×</button>
          </span>
          <NumInput aria-label={`Визитов в день: ${x.specialty}`} min={1} max={20} value={x.doctor_visits_per_day}
            onChange={(v) => set(i, { doctor_visits_per_day: v ?? 1 })} />
          <NumInput aria-label={`Кратность: ${x.specialty}`} min={1} max={12} value={x.doctor_frequency}
            onChange={(v) => set(i, { doctor_frequency: v ?? 1 })} />
        </div>
      ))}
      {free.length > 0 && (
        <button type="button" className="linklike add-spec"
          onClick={() => onChange([...value, { specialty: free[0], doctor_visits_per_day: 2, doctor_frequency: 2 }])}>
          + Ещё специальность
        </button>
      )}
      {value.length > 0 && (
        <p className="hint extra-hint">
          Врачи доп. специальностей ставятся в те же дни и по возможности в те же ЛПУ, что и {plural(main)}.
        </p>
      )}
    </>
  )
}
