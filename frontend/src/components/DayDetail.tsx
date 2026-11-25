import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import type { Day, PlanFacility, RepInfo } from '../types'
import { hours, longDate, num, plural } from '../format'
import { DayBreakdown } from './DayBreakdown'
import { PlanMap, type MapPoint } from './PlanMap'

interface Props {
  day: Day
  rep: RepInfo
  multiRep: boolean
  facilities: PlanFacility[]
  center: [number, number]
  limitH: number
  calendar: ReactNode
  detailsRef: RefObject<HTMLDivElement>
  busy: boolean
  picking: boolean
  onTogglePick: () => void
  onPick: (p: [number, number]) => void
}

export function DayDetail({ day, rep, multiRep, facilities, center, limitH, calendar, detailsRef, busy, picking, onTogglePick, onPick }: Props) {
  const [hl, setHl] = useState<string | null>(null)
  const [mode, setMode] = useState<'day' | 'all'>('day')
  const listRef = useRef<HTMLOListElement>(null)
  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = 0
    setHl(null)
  }, [day.date, day.rep])
  const t = day.totals
  const risk = Math.round(t.overtime_risk * 100)
  const hasVisits = day.stops.length > 0

  const dayPoints: MapPoint[] = day.stops.map((s) => ({
    id: s.facility_id, lat: s.lat, lon: s.lon, kind: s.type, label: String(s.seq),
    title: `${s.seq}. ${s.name}${s.address ? ', ' + s.address : ''}`,
  }))
  const repFacilities = facilities.filter((f) => f.visits.some((v) => v.rep === rep.id))
  const allPoints: MapPoint[] = repFacilities.map((f) => ({
    id: f.id, lat: f.lat, lon: f.lon, kind: f.kind,
    title: `${f.name} — ${f.visits.length} ${plural(f.visits.length, 'визит', 'визита', 'визитов')}`,
  }))
  const route: [number, number][] = [day.home, ...day.stops.map((s) => [s.lat, s.lon] as [number, number])]

  return (
    <div className="plan-calendar-layout">
      {calendar}
      <section className="day-details" ref={detailsRef} aria-label="Расписание дня" aria-busy={busy}>
      <div className="day-head">
        <h2>{longDate(day.date)}{multiRep && `, МП ${day.rep}`}</h2>
        {hasVisits ? <><span className="meta">
          {t.start}–{t.end}, {hours(t.hours)}, {num(t.km)} км, ожидание {t.wait_min} мин, в пути {t.travel_min} мин
        </span>
        <span className="meta" style={{ color: risk >= 20 ? 'var(--risk)' : undefined }}>
          с вероятностью 80% день займёт до {num(t.p80_hours)} ч{risk > 0 && `, риск переработки ${risk}%`}
        </span></> : <span className="meta">На этот рабочий день визиты не запланированы.</span>}
      </div>
      {hasVisits && <>
      <div className="day-strip"><DayBreakdown day={day} limitH={limitH} /></div>

      <h3 className="day-stops-title">Остановки маршрута <span>{day.stops.length}</span></h3>
      <ol className="stops" ref={listRef} tabIndex={0} aria-label="Остановки маршрута, с прокруткой">
        <li>
          <span className="n" style={{ background: 'var(--ink)', borderRadius: 3 }} />
          <div className="home">Выезд из точки старта</div>
          <div className="time">{t.start}</div>
        </li>
        {day.stops.map((s) => (
          <li key={s.facility_id} className={hl === s.facility_id ? 'hl' : ''}
            onMouseEnter={() => setHl(s.facility_id)} onMouseLeave={() => setHl(null)}>
            <span className={`n ${s.type}`}>{s.seq}</span>
            <div>
              <div className="name">{s.name}</div>
              <div className="addr">{s.address || 'адрес уточняется'}</div>
              {s.type === 'lpu' ? (
                <div className="docs">
                  {s.doctors.map((d) => (
                    <span key={d.name}>{d.name}, {d.start}, визит {d.visit_no}</span>
                  ))}
                </div>
              ) : (
                <div className="docs"><span>Аптека, визит {s.visit_no ?? 1}</span></div>
              )}
            </div>
            <div className="time">
              {s.arrive}–{s.leave}
              <small>{s.travel_min} мин в пути, {num(s.km)} км</small>
            </div>
          </li>
        ))}
        <li>
          <span />
          <div className="home">Обед, отчёт в CRM, окончание дня</div>
          <div className="time">{t.end}</div>
        </li>
      </ol>
      </>}

      </section>
      <section className="day-map" aria-label="Карта маршрута">
        <div className="map-tools">
          <div className="rep-tabs">
            <button aria-pressed={mode === 'day'} onClick={() => setMode('day')}>Маршрут дня</button>
            <button aria-pressed={mode === 'all'} onClick={() => setMode('all')}>Все учреждения МП</button>
          </div>
          <button className={`btn quiet${picking ? ' active' : ''}`} disabled={busy} aria-pressed={picking} onClick={onTogglePick}>
            {picking ? 'Отменить выбор точки' : 'Изменить точку старта'}
          </button>
        </div>
        {picking && <p className="map-pick-status" role="status">Выберите новую точку старта на карте.</p>}
        {busy && <p className="map-pick-status" role="status">Пересчитываю маршрут…</p>}
        <div className="mapbox">
          <PlanMap
            center={center}
            points={mode === 'day' ? dayPoints : allPoints}
            route={mode === 'day' ? route : undefined}
            home={rep.home}
            routeColor={multiRep ? rep.color : undefined}
            highlight={hl}
            onHover={setHl}
            picking={picking}
            onPick={onPick}
            wheelZoom={false}
            fitKey={`${mode}-${day.date}-${day.rep}-${day.home.join(",")}`}
          />
        </div>
      </section>
    </div>
  )
}
