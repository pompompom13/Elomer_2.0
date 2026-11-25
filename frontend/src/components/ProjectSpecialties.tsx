import type { ExtraSpec, PlanParams } from '../types'
import { NumInput } from './NumInput'

type VisitSettings = ExtraSpec & { extra_specialties?: ExtraSpec[] }

export function specialtyRows(settings: VisitSettings): ExtraSpec[] {
  const { specialty, doctor_visits_per_day, doctor_frequency, extra_specialties } = settings
  return [{ specialty, doctor_visits_per_day, doctor_frequency }, ...(extra_specialties ?? []).filter((x) => x.specialty !== specialty)]
}

interface Props {
  options: string[]
  settings: VisitSettings
  onChange: (settings: VisitSettings) => void
  frequencyUnit?: PlanParams['frequency_unit']
}

export function ProjectSpecialties({ options, settings, onChange, frequencyUnit = 'project_month' }: Props) {
  const rows = specialtyRows(settings)
  const available = options.filter((name) => !rows.some((row) => row.specialty === name))
  const update = ([first, ...extra]: ExtraSpec[]) => onChange({ ...first, extra_specialties: extra })
  const edit = (i: number, patch: Partial<ExtraSpec>) => update(rows.map((row, j) => j === i ? { ...row, ...patch } : row))
  return (
    <>
      <div className="visit-row head"><span /><span>Специальность</span><span>в день</span><span>раз {frequencyUnit === 'project' ? 'за проект' : 'в месяц'}</span></div>
      {rows.map((row, i) => (
        <div className="visit-row extra" key={i}>
          <span className="swatch" style={{ background: 'var(--doc)' }} />
          <span className="extra-name">
            <select aria-label={`Специальность ${i + 1}`} value={row.specialty}
              onChange={(e) => edit(i, { specialty: e.target.value })}>
              {[row.specialty, ...available].map((name) => <option key={name}>{name}</option>)}
            </select>
            {rows.length > 1 && <button type="button" className="extra-del" aria-label={`Убрать ${row.specialty}`}
              onClick={() => update(rows.filter((_, j) => j !== i))}>×</button>}
          </span>
          <NumInput aria-label={`Визитов в день: ${row.specialty}`} min={0} max={20} value={row.doctor_visits_per_day}
            onChange={(v) => edit(i, { doctor_visits_per_day: v ?? 0 })} />
          <NumInput aria-label={`Кратность: ${row.specialty}`} min={1} max={12} value={row.doctor_frequency}
            onChange={(v) => edit(i, { doctor_frequency: v ?? 1 })} />
        </div>
      ))}
      {available.length > 0 && <button type="button" className="linklike add-spec"
        onClick={() => update([...rows, { specialty: available[0], doctor_visits_per_day: 2, doctor_frequency: 1 }])}>
        + Ещё специальность
      </button>}
    </>
  )
}

export function FrequencyPolicy({ value, onChange }: {
  value: PlanParams['frequency_unit']
  onChange: (value: NonNullable<PlanParams['frequency_unit']>) => void
}) {
  return <label className="field">
    <span>Как считать кратность</span>
    <select value={value === 'project' ? 'project' : 'project_month'} onChange={(e) => onChange(e.target.value as NonNullable<PlanParams['frequency_unit']>)}>
      <option value="project_month">За каждый месяц от даты старта</option>
      <option value="project">Всего за проект</option>
    </select>
    {value !== 'project' && <span className="hint">Например, при старте 24 ноября первый месяц длится до 23 декабря включительно.</span>}
  </label>
}
