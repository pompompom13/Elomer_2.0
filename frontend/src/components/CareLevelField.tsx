import { useId } from 'react'
import type { CareFilter, CareLevel } from '../types'

export const CARE_LABELS: Record<CareLevel, string> = {
  ambulatory: 'Амбулаторное', hospital: 'Госпитальное', mixed: 'Смешанное', unknown: 'Не определено',
}

export function matchesCare(actual: CareLevel = 'unknown', selected: CareFilter) {
  return selected === 'all' || actual === selected || usesPartialCareCount(actual, selected)
}

export function usesPartialCareCount(actual: CareLevel = 'unknown', selected: CareFilter) {
  return (selected === 'ambulatory' && (actual === 'mixed' || actual === 'unknown')) ||
    (selected === 'hospital' && actual === 'mixed')
}

export function countForCare(total: number, actual: CareLevel | undefined, selected: CareFilter) {
  if (!matchesCare(actual, selected)) return 0
  const ambulatory = Math.floor((3 * total + 2) / 5)
  if (!usesPartialCareCount(actual, selected)) return total
  return selected === 'ambulatory' ? ambulatory : total - ambulatory
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
    {hint && <span id={`${id}-hint`} className="hint">Амбулаторный отбор включает 60% врачей каждой специальности в смешанных ЛПУ и ЛПУ с неопределённым звеном. В госпитальный отбор входят оставшиеся врачи смешанных ЛПУ.</span>}
  </div>
}
