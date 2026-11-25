import { useMemo, type CSSProperties } from 'react'
import type { Day } from '../types'
import { iso, num, parseDate, plural, shortMonth } from '../format'

interface Props {
  days: Day[] // дни выбранного МП
  start: string
  end: string
  selected: string | null
  onSelect: (date: string) => void
  limitH: number // норма рабочего дня, ч
}

const DOW = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const FULL_H = 7.5
const OVER_H = 8.5

function loadLevel(hours: number): 'ok' | 'full' | 'over' {
  if (hours > OVER_H) return 'over'
  if (hours > FULL_H) return 'full'
  return 'ok'
}

export function Calendar({ days, start, end, selected, onSelect, limitH }: Props) {
  const byDate = useMemo(() => new Map(days.map((d) => [d.date, d])), [days])
  const columnCount = days.some((d) => {
    const weekday = parseDate(d.date).getDay()
    return d.date >= start && d.date <= end && (weekday === 6 || weekday === 0)
  }) ? 7 : 5
  const weeks = useMemo(() => {
    const s = parseDate(start), e = parseDate(end)
    const monday = new Date(s)
    monday.setDate(s.getDate() - ((s.getDay() + 6) % 7))
    const out: Date[][] = []
    for (let w = new Date(monday); w <= e; w.setDate(w.getDate() + 7)) {
      out.push(Array.from({ length: columnCount }, (_, i) => {
        const d = new Date(w)
        d.setDate(w.getDate() + i)
        return d
      }))
    }
    return out
  }, [start, end, columnCount])

  const s = parseDate(start), e = parseDate(end)

  return (
    <div className="cal" style={{ "--calendar-days": columnCount } as CSSProperties} role="grid" aria-label="Календарь проекта">
      <div />
      {DOW.slice(0, columnCount).map((d) => <div key={d} className="dow">{d}</div>)}
      {weeks.map((week, wi) => {
        const first = week[0], last = week[week.length - 1]
        const label = first.getMonth() === last.getMonth()
          ? `${first.getDate()}–${last.getDate()} ${shortMonth(first)}`
          : `${first.getDate()} ${shortMonth(first)} – ${last.getDate()} ${shortMonth(last)}`
        return [
          <div key={`w${wi}`} className="wk">{label}</div>,
          ...week.map((d) => {
            const key = iso(d)
            const day = byDate.get(key)
            if (d < s || d > e) return <div key={key} className="cell outside" />
            if (!day) {
              return (
                <div key={key} className="cell off" aria-disabled="true">
                  <div className="d"><b>{d.getDate()}</b></div>
                  <div className="tag">выходной</div>
                </div>
              )
            }
            return <DayCell key={key} day={day} date={d} limitH={limitH}
              selected={selected === key} onSelect={() => onSelect(key)} />
          }),
        ]
      })}
    </div>
  )
}

function DayCell({ day, date, limitH, selected, onSelect }: {
  day: Day; date: Date; limitH: number; selected: boolean; onSelect: () => void
}) {
  const t = day.totals
  const level = loadLevel(t.hours)
  const overLimit = Math.max(t.hours, t.p80_hours) > limitH
  const hoursLabel = num(t.hours, 2)
  const fill = Math.min(100, (t.hours / limitH) * 100)
  const note = day.kind === 'reserve' ? 'резервный день' : day.kind !== 'work' ? 'нет врачей' : null
  return (
    <button className={`cell lvl-${level}`} aria-pressed={selected} onClick={onSelect}
      aria-label={`${date.getDate()} число: ${t.doctor_visits} врачей, ${t.pharmacy_visits} аптек, ${hoursLabel} ч`}>
      <div className="d"><b>{date.getDate()}</b>{overLimit && <span className="calendar-risk" title="Длительность или P80 выше нормы дня" aria-label="Сверх нормы">!</span>}{note && <span className="tag">{note}</span>}</div>
      <div className="counts">
        {t.doctor_visits > 0 && <span><i className="mk doc" />{t.doctor_visits} {plural(t.doctor_visits, 'врач', 'врача', 'врачей')}</span>}
        {t.pharmacy_visits > 0 && <span><i className="mk ph" />{t.pharmacy_visits} {plural(t.pharmacy_visits, 'аптека', 'аптеки', 'аптек')}</span>}
      </div>
      <div className="load" title={`Рабочий день ${hoursLabel} ч при норме ${num(limitH)} ч`}>
        <span className="bar"><i style={{ width: `${fill}%` }} /></span>
        <span className="h">{hoursLabel} ч</span>
      </div>
      <div className="when">{t.start}–{t.end}</div>
    </button>
  )
}

export function CalendarLegend() {
  return (
    <div className="legend">
      <span><i className="lg ok" />{num(FULL_H)} ч и меньше</span>
      <span><i className="lg full" />свыше {num(FULL_H)} до {num(OVER_H)} ч</span>
      <span><i className="lg over" />свыше {num(OVER_H)} ч</span>
      <span><b className="calendar-risk" aria-hidden="true">!</b> длительность или P80 выше нормы</span>
    </div>
  )
}
