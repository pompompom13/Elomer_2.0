import type { CityNorms, Meta } from '../types'
import { num } from '../format'
import { NumInput } from './NumInput'

export const CITY_NORMS: CityNorms = {
  transport: 'car', day_start: '09:00', workday_limit_h: 8,
  travel_time_factor: 1, travel_detour: null, travel_overhead_min: null,
  wait_min: null, doctor_visit_min: null, pharmacy_visit_min: null, pharmacy_wait_min: null,
  lpu_entry_min: null, lunch_min: null, crm_min: null,
}

export function cityNormsFrom(conditions: Partial<CityNorms>): CityNorms {
  return Object.fromEntries(Object.entries(CITY_NORMS).map(([key, fallback]) => {
    const value = conditions[key as keyof CityNorms]
    return [key, value === undefined ? fallback : value]
  })) as unknown as CityNorms
}

type MinuteKey = 'wait_min' | 'doctor_visit_min' | 'pharmacy_visit_min' |
  'pharmacy_wait_min' | 'lpu_entry_min' | 'lunch_min' | 'crm_min'
const MINUTES: { key: MinuteKey; label: string; min?: number }[] = [
  { key: 'doctor_visit_min', label: 'Визит к врачу, мин', min: 1 },
  { key: 'wait_min', label: 'Ожидание врача, мин' },
  { key: 'pharmacy_visit_min', label: 'Визит в аптеку, мин', min: 1 },
  { key: 'pharmacy_wait_min', label: 'Ожидание в аптеке, мин' },
  { key: 'lpu_entry_min', label: 'Регистратура и вход в ЛПУ, мин' },
  { key: 'lunch_min', label: 'Обед, мин' },
  { key: 'crm_min', label: 'Отчёт в CRM, мин' },
]

export function CitySettings({ value, defaults, meta, specialties, linked, onChange }: {
  value: CityNorms
  defaults: CityNorms
  meta: Meta
  specialties: string[]
  linked?: boolean
  onChange: (norms: CityNorms) => void
}) {
  const set = <K extends keyof CityNorms>(key: K, next: CityNorms[K]) => onChange({ ...cityNormsFrom(value), [key]: next })
  const transport = meta.transport[value.transport]
  const changed = Object.keys(defaults).some((key) => value[key as keyof CityNorms] !== defaults[key as keyof CityNorms])
  const wait = specialties.map((s) => `${s.toLowerCase()} — ${meta.wait_median[s] ?? 20} мин`).join(', ')

  return (
    <details className="norms more city-settings">
      <summary>Дополнительные настройки{changed && <span> · изменены</span>}</summary>
      <p className="hint">{linked
        ? 'Настройки действуют для этого города в текущем проекте. Пересчитайте город, затем нажмите «Сохранить и вернуться к проекту».'
        : 'Настройки действуют в текущем расчёте города. Новый план начинается с базовых нормативов.'}</p>

      <fieldset>
        <legend>Рабочий день</legend>
        <div className="city-settings-grid">
          <label className="field"><span>Начало дня</span>
            <input type="time" required value={value.day_start} onChange={(e) => set('day_start', e.target.value)} />
          </label>
          <label className="field"><span>Норма дня, ч</span>
            <NumInput decimal min={4} max={12} step="any" value={value.workday_limit_h}
              onChange={(n) => set('workday_limit_h', n ?? defaults.workday_limit_h)} />
          </label>
        </div>
      </fieldset>
      <fieldset>
        <legend>Транспорт и дорога</legend>
        <div className="city-settings-grid">
          <label className="field"><span>Транспорт</span>
            <select value={value.transport} onChange={(e) => set('transport', e.target.value as CityNorms['transport'])}>
              {Object.entries(meta.transport).map(([key, info]) => <option key={key} value={key}>{info.label}</option>)}
            </select>
          </label>
          <label className="field"><span>Время движения, % от базового</span>
            <NumInput min={50} max={300} value={Math.round(value.travel_time_factor * 100)}
              onChange={(n) => set('travel_time_factor', (n ?? 100) / 100)} />
          </label>
          <label className="field"><span>Коэффициент длины дороги</span>
            <NumInput allowEmpty decimal min={1} max={3} step="any" placeholder={String(transport.detour)}
              value={value.travel_detour} onChange={(n) => set('travel_detour', n)} />
          </label>
          <label className="field"><span>Парковка или пересадка, мин</span>
            <NumInput allowEmpty min={0} max={60} placeholder={String(transport.overhead_min)}
              value={value.travel_overhead_min} onChange={(n) => set('travel_overhead_min', n)} />
          </label>
        </div>
        <p className="hint">Базовая скорость — {num(transport.speed_kmh)} км/ч, расчётная — {num(transport.speed_kmh / value.travel_time_factor)} км/ч. При 120% движение на транспорте занимает на 20% больше времени. Для коротких переходов используется обычная скорость пешком. Текущие пробки онлайн не учитываются.</p>
      </fieldset>
      <fieldset>
        <legend>Визиты и паузы</legend>
        <div className="city-settings-grid">
          {MINUTES.map(({ key, label, min }) => (
            <label key={key} className="field"><span>{label}</span>
              <NumInput allowEmpty decimal min={min ?? 0} max={180} step="any" value={value[key]}
                placeholder={key === 'wait_min' ? (specialties.length > 1 ? 'По специальности' : String(meta.wait_median[specialties[0]] ?? 20)) : String(meta[key])}
                onChange={(n) => set(key, n)} />
            </label>
          ))}
        </div>
        <p className="hint">Пустые поля используют базовые нормативы. Базовое ожидание врача: {wait}.</p>
      </fieldset>
      <button type="button" className="linklike" disabled={!changed} onClick={() => onChange(defaults)}>
        {linked ? 'Вернуть нормы проекта' : 'Вернуть базовые нормы'}
      </button>
    </details>
  )
}
