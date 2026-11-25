import { useId } from 'react'
import type { CareFilter, CareLevel } from '../types'

export const CARE_LABELS: Record<CareLevel, string> = {
  ambulatory: 'Амбулаторное', hospital: 'Госпитальное', mixed: 'Смешанное', unknown: 'Не определено',
}

export function matchesCare(actual: CareLevel = 'unknown', selected: CareFilter) {
  return selected === 'all' || actual === selected ||
    (actual === 'mixed' && (selected === 'ambulatory' || selected === 'hospital'))
}

export function countForCare(total: number, actual: CareLevel | undefined, selected: CareFilter) {
  if (!matchesCare(actual, selected)) return 0
  const ambulatory = Math.floor((3 * total + 2) / 5)
  if (actual === 'mixed' && selected === 'ambulatory') return ambulatory
  if (actual === 'mixed' && selected === 'hospital') return total - ambulatory
  return total
}

export function CareLevelField({ value = 'all', onChange, hint = false }: {
  value?: CareFilter; onChange: (value: CareFilter) => void; hint?: boolean
}) {
  const id = useId()
  return <div className="field care-field">
    <label htmlFor={id}>Звено ЛПУ</label>
    <select id={id} aria-describedby={hint ? `${id}-hint` : undefined}
      value={value} onChange={(event) => onChange(event.target.value as CareFilter)}>
      <option value="all">Все звенья</option>
      <option value="ambulatory">Амбулаторное</option>
      <option value="hospital">Госпитальное</option>
      <option value="mixed">Только смешанные ЛПУ</option>
      <option value="unknown">Звено не определено</option>
    </select>
    {hint && <span id={`${id}-hint`} className="hint">В смешанных ЛПУ на амбулаторное звено приходится 60% врачей каждой специальности, на госпитальное — 40%.</span>}
  </div>
}
