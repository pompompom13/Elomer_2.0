import { NumInput } from './NumInput'
import type { ReactNode } from 'react'
import type { CityItem, CityNorms, GoalSpec, Meta, PlanParams } from '../types'
import { CityPicker } from './CityPicker'
import { FrequencyPolicy, ProjectSpecialties } from './ProjectSpecialties'
import { CITY_NORMS, CitySettings } from './CitySettings'

export type EntryMode = 'reps' | 'goal'

interface Props {
  meta: Meta
  cities: CityItem[]
  params: PlanParams
  onChange: (p: PlanParams) => void
  onSubmit: () => void
  onAddCity: () => void
  busy: boolean
  cityStatus: string | null
  cityReady: boolean
  filtersSlot?: ReactNode
  onGoal: (target: number | null) => void
  goalTarget: number | null
  onGoalTarget: (v: number | null) => void
  goalSpec: GoalSpec
  onGoalSpec: (v: GoalSpec) => void
  mode: EntryMode
  onMode: (m: EntryMode) => void
  projectSlot?: ReactNode
  clientBases?: { id: string; name: string }[]
  linked?: boolean
  onReset?: () => void
  normDefaults?: CityNorms
}

/** Левая панель плана города: два входа (есть медпреды / есть цель), основное сверху, остальное в «Дополнительно». */
export function ParamsForm({ meta, cities, params: p, onChange, onSubmit, onAddCity, busy, cityStatus, cityReady, filtersSlot,
  onGoal, goalTarget, onGoalTarget, goalSpec: gs, onGoalSpec, mode, onMode, projectSlot, clientBases = [], linked, onReset, normDefaults }: Props) {
  const set = <K extends keyof PlanParams>(k: K, v: PlanParams[K]) => onChange({ ...p, [k]: v })
  const settings = <CitySettings value={p} defaults={normDefaults ?? CITY_NORMS} meta={meta} linked={linked}
    specialties={[p.specialty, ...(p.extra_specialties ?? []).map((row) => row.specialty)]}
    onChange={(norms) => onChange({ ...p, ...norms })} />
  if (linked) return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit() }}>
      {projectSlot}
      <p className="hint">Срок, специальности и частота визитов задаются в проекте.</p>
      <div className="field">
        <label htmlFor="linked-reps">Медпредов в городе</label>
        <NumInput id="linked-reps" min={1} max={80} value={p.reps} onChange={(v) => set('reps', v ?? 1)} />
      </div>
      <button className="btn primary" type="submit" disabled={busy || !cityReady}>{busy ? 'Считаю…' : 'Пересчитать город'}</button>
      <details className="norms more">
        <summary>Учреждения города</summary>
        {clientBases.length > 0 && <label className="field"><span>Источник ЛПУ</span>
          <select value={p.client_base ?? ''} onChange={(e) => set('client_base', e.target.value || null)}>
            <option value="">Наша база</option>
            {clientBases.map((c) => <option key={c.id} value={c.id}>База заказчика: {c.name}</option>)}
          </select>
        </label>}
        {filtersSlot}
      </details>
      {settings}
    </form>
  )

  const missingGoalLoad = mode === 'goal' && (
    (!gs.doctors && !gs.pharmacies) ||
    (gs.doctors && !p.doctor_visits_per_day && !p.extra_specialties?.some((row) => row.doctor_visits_per_day)) ||
    (gs.pharmacies && !p.pharmacy_visits_per_day)
  )

  return (
    <form onSubmit={(e) => { e.preventDefault(); if (mode === 'goal') onGoal(goalTarget); else onSubmit() }}>
      {projectSlot}
      {onReset && (
        <div className="reset-row">
          <button type="button" className="linklike" onClick={onReset}>Сбросить параметры</button>
        </div>
      )}
      <div className="seg seg-wide" role="tablist" aria-label="Что известно">
        <button type="button" role="tab" aria-pressed={mode === 'reps'} onClick={() => onMode('reps')}>Есть медпреды</button>
        <button type="button" role="tab" aria-pressed={mode === 'goal'} onClick={() => onMode('goal')}>Есть цель охвата</button>
      </div>
      <p className="seg-hint">{mode === 'reps'
        ? 'Сколько визитов и врачей успеет команда за срок проекта.'
        : 'Подбор команды по цели охвата и заданным визитам в день.'}</p>

      <div className="field">
        <label htmlFor="city">Город</label>
        <CityPicker cities={cities} value={p.city} onChange={(slug) => set('city', slug)} />
        {cityStatus ? <span className="hint">{cityStatus}</span> : (
          <span className="hint">
            {cities.length} городов России от 50 тыс. жителей. <button type="button" className="linklike" onClick={onAddCity}>База города</button>
          </span>
        )}
      </div>

      {mode === 'reps' ? (
        <div className="field">
          <label htmlFor="reps">Медпредов</label>
          <NumInput id="reps" min={1} max={80} value={p.reps} onChange={(v) => set('reps', v ?? 1)} />
        </div>
      ) : (
        <div className="field goal-spec">
          <label>Кого охватить</label>
          <div className="goal-row">
            <label className="chk"><input type="checkbox" checked={gs.doctors}
              onChange={(e) => onGoalSpec({ ...gs, doctors: e.target.checked })} />
              <span className="swatch" style={{ background: 'var(--doc)' }} />Врачей</label>
            <input id="goal" type="number" min={1} placeholder="все в базе" value={goalTarget ?? ''} disabled={!gs.doctors}
              aria-label="Охватить врачей"
              onChange={(e) => onGoalTarget(e.target.value === '' ? null : Math.max(1, parseInt(e.target.value, 10) || 1))} />
          </div>
          <div className="goal-row">
            <label className="chk"><input type="checkbox" checked={gs.pharmacies}
              onChange={(e) => onGoalSpec({ ...gs, pharmacies: e.target.checked })} />
              <span className="swatch" style={{ background: 'var(--ph)' }} />Аптек</label>
            <input type="number" min={1} placeholder="все в базе" value={gs.ph ?? ''} disabled={!gs.pharmacies}
              aria-label="Охватить аптек"
              onChange={(e) => onGoalSpec({ ...gs, ph: e.target.value === '' ? null : Math.max(1, parseInt(e.target.value, 10) || 1) })} />
          </div>
          <span className="hint">
            Пустое поле означает всех доступных врачей или аптеки. Цель по врачам общая для выбранных специальностей.
          </span>
        </div>
      )}

      <div className="pair">
        <div className="field">
          <label htmlFor="sd">Начало</label>
          <input id="sd" type="date" value={p.start_date} onChange={(e) => set('start_date', e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mo">Месяцев</label>
          <NumInput id="mo" min={1} max={12} value={p.months} onChange={(v) => set('months', v ?? 1)} />
        </div>
      </div>

      <FrequencyPolicy value={p.frequency_unit} onChange={(v) => set('frequency_unit', v)} />
      {(mode === 'reps' || gs.doctors) && <ProjectSpecialties options={meta.specialties} settings={p} frequencyUnit={p.frequency_unit}
        onChange={(settings) => onChange({ ...p, ...settings })} />}
      {(mode === 'reps' || gs.pharmacies) && (
      <div className="visit-row">
        <span className="swatch" style={{ background: 'var(--ph)' }} />
        <span>Аптеки</span>
        <NumInput aria-label="Визитов в аптеки в день" min={0} max={20} value={p.pharmacy_visits_per_day}
          onChange={(v) => set('pharmacy_visits_per_day', v ?? 0)} />
        <NumInput aria-label="Кратность визитов в аптеки" min={1} max={12} value={p.pharmacy_frequency}
          onChange={(v) => set('pharmacy_frequency', v ?? 1)} />
      </div>
      )}

      {missingGoalLoad && <p className="hint">Выберите цель и задайте дневную нагрузку для каждой включённой группы.</p>}
      <button className="btn primary" type="submit" disabled={busy || !p.city || !cityReady || missingGoalLoad}>
        {busy ? 'Считаю…' : mode === 'goal' ? 'Подобрать команду' : 'Рассчитать план'}
      </button>

      <details className="norms more">
        <summary>Учреждения города</summary>
        <div className="field">
          <label htmlFor="cov">Каких врачей брать в зоне</label>
          <select id="cov" value={p.coverage} onChange={(e) => set('coverage', e.target.value as PlanParams['coverage'])}>
            <option value="spread">По всей зоне равномерно</option>
            <option value="compact">Ближайших к точке старта</option>
          </select>
          <span className="hint">
            {p.coverage === 'spread'
              ? 'Визиты разносятся по всей зоне: каждый день — компактная группа врачей в своей части зоны.'
              : 'Меньше дорог, но дальняя часть зоны остаётся без визитов.'}
          </span>
        </div>
        {p.pharmacy_visits_per_day > 0 && (
          <div className="field">
            <label htmlFor="php">Какие аптеки брать</label>
            <select id="php" value={p.pharmacy_priority ?? 'near'}
              onChange={(e) => set('pharmacy_priority', e.target.value as PlanParams['pharmacy_priority'])}>
              <option value="near">Ближайшие к врачам дня</option>
              <option value="turnover">С большим оборотом (по пути)</option>
            </select>
            <span className="hint">
              {p.pharmacy_priority === 'turnover'
                ? 'Среди аптек до 5 км от врачей дня сначала берутся с наибольшим оборотом (по реестру аптек).'
                : 'Сначала аптеки рядом с врачами дня, затем всё дальше — в пределах своей зоны.'}
            </span>
          </div>
        )}

        {clientBases.length > 0 && (
          <div className="field">
            <label htmlFor="csrc">Источник ЛПУ</label>
            <select id="csrc" value={p.client_base ?? ''} onChange={(e) => set('client_base', e.target.value || null)}>
              <option value="">Наша база</option>
              {clientBases.map((c) => <option key={c.id} value={c.id}>База заказчика: {c.name}</option>)}
            </select>
            <span className="hint">ЛПУ и число врачей из базы заказчика вместо наших; аптеки — наши.</span>
          </div>
        )}
        <h2>Какие учреждения включать</h2>
        {filtersSlot}
      </details>
      {settings}
    </form>
  )
}
