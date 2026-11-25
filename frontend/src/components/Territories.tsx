import { useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { CircleMarker, MapContainer, Marker, Pane, Polygon, Polyline, Popup, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import { api } from '../api'
import type { ManualTerritories, Plan, PlanRequest, Zone } from '../types'
import { num } from '../format'

interface Props {
  plan: Plan
  request: PlanRequest // параметры последнего расчёта — для пересчёта зон при правке
  center: [number, number]
  rep: number
  onRep: (id: number) => void
  onApply: (t: ManualTerritories | null) => void
  busy: boolean
}

type LL = [number, number]
type Mode = 'move' | 'draw' | 'areas' | 'edit' | 'assign'

const MODES: { key: Mode; label: string; hint: string }[] = [
  { key: 'move', label: 'Двигать зоны', hint: 'Перетащите номер зоны, чтобы сдвинуть её; кнопками «−» и «+» в таблице уменьшите или увеличьте зону.' },
  { key: 'draw', label: 'Нарисовать область', hint: 'Кликайте по карте, ставя углы области; двойной клик или «Готово» замыкает её. Область добавляется к зоне выбранного медпреда — все остальные границы остаются как есть. За городом в область попадают и учреждения соседних городов.' },
  { key: 'areas', label: 'Нарисованные области', hint: 'Выберите область на карте или в списке. Её контур выделится. После удаления сохраните и пересчитайте план.' },
  { key: 'edit', label: 'Править границы', hint: 'Тяните квадратики — вершины границ; общая граница двигается сразу у обеих зон. Клик по кружку на границе добавляет вершину, правый клик по вершине удаляет её.' },
  { key: 'assign', label: 'Переназначить учреждение', hint: 'Нажмите на точку учреждения и выберите, какому медпреду его отдать.' },
]

function FitAll({ pts, k }: { pts: LL[]; k: string }) {
  const map = useMap()
  useEffect(() => {
    const fit = () => {
      map.invalidateSize()
      if (pts.length > 1) map.fitBounds(L.latLngBounds(pts), { padding: [16, 16] })
    }
    fit()
    const t = setTimeout(fit, 150)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [k])
  useEffect(() => {
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [map])
  return null
}

function FocusArea({ polygon }: { polygon: LL[] | undefined }) {
  const map = useMap()
  useEffect(() => {
    if (polygon?.length) map.fitBounds(polygon, { padding: [32, 32], maxZoom: 15 })
  }, [map, polygon])
  return null
}

/** Клики по карте в режиме рисования. */
function DrawEvents({ active, onAdd, onFinish }: { active: boolean; onAdd: (p: LL) => void; onFinish: () => void }) {
  const map = useMapEvents({
    click: (e) => { if (active) onAdd([e.latlng.lat, e.latlng.lng]) },
    dblclick: (e) => { if (active) { L.DomEvent.stop(e); onFinish() } },
  })
  useEffect(() => {
    if (active) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable()
    map.getContainer().style.cursor = active ? 'crosshair' : ''
  }, [active, map])
  return null
}

const zonesFromPlan = (plan: Plan): Zone[] => plan.reps.map((r) => ({
  id: r.id, color: r.color, polygon: r.polygon, center: r.center, weight: r.weight,
  doctors: r.territory_doctors, lpu: 0, pharmacies: r.territory_pharmacies,
  extension: r.extension ?? 0, suburb: r.suburb ?? { doctors: 0, lpu: 0, pharmacies: 0 },
}))

const vkey = (p: LL) => `${p[0].toFixed(5)},${p[1].toFixed(5)}`

/** Склеить почти совпадающие вершины соседних зон (до ~40 м), чтобы общая граница двигалась целиком. */
function snap(polys: LL[][]): LL[][] {
  const reps: LL[] = []
  const pick = (p: LL): LL => {
    const r = reps.find((q) => Math.abs(q[0] - p[0]) < 0.0004 && Math.abs(q[1] - p[1]) < 0.0006)
    if (r) return r
    reps.push(p)
    return p
  }
  return polys.map((poly) => {
    const out: LL[] = []
    for (const p of poly) {
      const q = pick(p)
      if (!out.length || vkey(out[out.length - 1]) !== vkey(q)) out.push(q)
    }
    if (out.length > 1 && vkey(out[0]) === vkey(out[out.length - 1])) out.pop()
    return out
  })
}

/** Зоны медпредов и ручная правка границ. */
export function Territories({ plan, request, center, rep, onRep, onApply, busy }: Props) {
  const saved = request.territories ?? null
  const [zones, setZones] = useState<Zone[]>(() => zonesFromPlan(plan))
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [mode, setMode] = useState<Mode>('move')
  const [drawn, setDrawn] = useState<NonNullable<ManualTerritories['drawn']>>(saved?.drawn ?? [])
  const [selectedArea, setSelectedArea] = useState<number | null>(null)
  const [editPolys, setEditPolys] = useState<LL[][] | null>(saved?.polygons ?? null)
  const [moves, setMoves] = useState<Record<string, number>>(saved?.assign ?? {})
  const [draft, setDraft] = useState<LL[]>([])
  const [drawRep, setDrawRep] = useState(0)
  const [liveAssign, setLiveAssign] = useState<Record<string, number> | null>(null)
  // все учреждения зон — серым показываются те, что в зоне, но не в плане
  const [allPoints, setAllPoints] = useState<[string, number, number, number][]>([])
  const [showAll, setShowAll] = useState(true)
  useEffect(() => {
    let alive = true
    api.territories(request).then((r) => { if (alive) setAllPoints(r.points ?? []) }).catch(() => undefined)
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan])
  const inPlan = useMemo(() => new Set(plan.facilities.map((f) => f.id)), [plan])
  const outsidePlan = useMemo(() => allPoints.filter(([id]) => !inPlan.has(id)), [allPoints, inPlan])
  const seq = useRef(0)
  useEffect(() => {
    setZones(zonesFromPlan(plan))
    setLiveAssign(null)
    setDirty(false)
    setDrawn(saved?.drawn ?? [])
    setSelectedArea(null)
    if (!saved?.drawn?.length) setMode((current) => current === 'areas' ? 'draw' : current)
    setEditPolys(saved?.polygons ?? null)
    setMoves(saved?.assign ?? {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan])

  const stats = useMemo(() => new Map(plan.reps.map((r) => {
    const dd = plan.days.filter((d) => d.rep === r.id && d.stops.length)
    const avg = (f: (d: (typeof dd)[number]) => number) => (dd.length ? dd.reduce((a, d) => a + f(d), 0) / dd.length : 0)
    return [r.id, { hours: avg((d) => d.totals.hours), km: avg((d) => d.totals.km),
      visits: dd.reduce((a, d) => a + d.totals.doctor_visits, 0) }]
  })), [plan])

  const manual = (over: Partial<ManualTerritories> = {}): ManualTerritories => ({
    centers: zones.map((z) => z.center), weights: zones.map((z) => z.weight), extensions: zones.map((z) => z.extension),
    drawn: drawn.length ? drawn : undefined, polygons: editPolys, assign: Object.keys(moves).length ? moves : undefined,
    ...over,
  })

  async function preview(next: ManualTerritories) {
    const my = ++seq.current
    setLoading(true)
    setErr(null)
    try {
      const r = await api.territories({ ...request, territories: next })
      if (my === seq.current) {
        setZones(r.zones)
        setLiveAssign(r.assignment ?? null)
        if (r.points) setAllPoints(r.points)
        setDirty(true)
        if (next.polygons) setEditPolys(next.polygons)
        return true
      }
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      if (my === seq.current) setLoading(false)
    }
    return false
  }
  const [suburbNote, setSuburbNote] = useState(false)

  // ── режим «двигать зоны» ──
  function move(i: number, ll: LL) {
    const m = manual({ polygons: null })
    m.centers[i] = ll
    setEditPolys(null)
    preview(m)
  }
  function extend(i: number, km: number) {
    const m = manual()
    m.extensions![i] = km
    if (km > 0 && !zones.some((z) => z.extension > 0)) setSuburbNote(true)
    preview(m).finally(() => setSuburbNote(false))
  }
  function resize(i: number, dir: 1 | -1) {
    const m = manual({ polygons: null })
    m.weights[i] += dir * plan.territories.weight_step
    setEditPolys(null)
    preview(m)
  }

  // ── режим «нарисовать область» ──
  function finishDraft() {
    if (draft.length < 3) return
    const next = [...drawn, { rep: drawRep, polygon: draft }]
    setDrawn(next)
    setDraft([])
    preview(manual({ drawn: next }))
  }
  async function removeSelectedArea() {
    if (selectedArea === null || loading || busy) return
    const next = drawn.filter((_, i) => i !== selectedArea)
    if (await preview(manual({ drawn: next.length ? next : undefined }))) {
      setDrawn(next)
      setSelectedArea(null)
      if (!next.length) setMode('draw')
    }
  }

  // ── режим «править границы» ──
  function startEdit() {
    setMode('edit')
    setDraft([])
    if (!editPolys) setEditPolys(snap(zones.map((z) => z.polygon as LL[])))
  }
  const vertices = useMemo(() => {
    const m = new Map<string, LL>()
    for (const poly of editPolys ?? []) for (const p of poly) m.set(vkey(p), p)
    return [...m.entries()]
  }, [editPolys])
  const midpoints = useMemo(() => {
    const out: { a: string; b: string; at: LL }[] = []
    const seen = new Set<string>()
    for (const poly of editPolys ?? []) {
      poly.forEach((p, i) => {
        const q = poly[(i + 1) % poly.length]
        const k = [vkey(p), vkey(q)].sort().join('|')
        if (seen.has(k)) return
        seen.add(k)
        out.push({ a: vkey(p), b: vkey(q), at: [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2] })
      })
    }
    return out
  }, [editPolys])
  function moveVertex(key: string, ll: LL) {
    const next = (editPolys ?? []).map((poly) => poly.map((p) => (vkey(p) === key ? ll : p)))
    setEditPolys(next)
    preview(manual({ polygons: next }))
  }
  function addVertex(a: string, b: string, at: LL) {
    // вершина добавляется во все зоны, у которых есть это ребро, — граница остаётся общей
    const next = (editPolys ?? []).map((poly) => {
      const out: LL[] = []
      poly.forEach((p, i) => {
        out.push(p)
        const q = poly[(i + 1) % poly.length]
        const k1 = vkey(p), k2 = vkey(q)
        if ((k1 === a && k2 === b) || (k1 === b && k2 === a)) out.push(at)
      })
      return out
    })
    setEditPolys(next)
  }
  function removeVertex(key: string) {
    const next = (editPolys ?? []).map((poly) => (poly.length > 3 ? poly.filter((p) => vkey(p) !== key) : poly))
    setEditPolys(next)
    preview(manual({ polygons: next }))
  }

  // ── режим «переназначить учреждение» ──
  function reassign(id: string, r: number | null) {
    const next = { ...moves }
    if (r === null) delete next[id]; else next[id] = r
    setMoves(next)
    preview(manual({ assign: Object.keys(next).length ? next : undefined }))
  }

  function resetAll() {
    setDrawn([]); setEditPolys(null); setMoves({}); setDraft([]); setMode('move')
    onApply(null)
  }

  const avgDocs = zones.reduce((a, z) => a + z.doctors, 0) / Math.max(1, zones.length)
  const handle = (z: Zone) => L.divIcon({
    className: '', html: `<div class="zone-handle" style="background:${z.color}">${z.id}</div>`,
    iconSize: [26, 26], iconAnchor: [13, 13],
  })
  const vIcon = L.divIcon({ className: '', html: '<div class="v-handle"></div>', iconSize: [12, 12], iconAnchor: [6, 6] })
  const colorOf = (i: number) => zones[i]?.color ?? '#555'
  const selectedPolygon = selectedArea === null ? undefined : drawn[selectedArea]?.polygon
  const selectingAreas = editing && mode === 'areas'
  const shownPolys: LL[][] = editing && mode === 'edit' && editPolys ? editPolys : zones.map((z) => z.polygon as LL[])
  const hint = MODES.find((m) => m.key === mode)!.hint

  return (
    <section className="terr">
      <div className="section-head">
        <h2>Зоны медпредов</h2>
        <span className="muted">Город целиком поделён на {zones.length} {zones.length < 5 ? 'зоны' : 'зон'}. </span>
        <button type="button" className="btn quiet" aria-expanded={editing} onClick={() => {
          setEditing(!editing); setMode('move'); setDraft([])
        }}>{editing ? 'Закрыть редактор' : 'Редактировать зоны'}</button>
      </div>
      {(dirty || plan.territories.manual) && (
        <div className={`terr-bar${dirty ? ' dirty' : ''}`}>
          <span>{dirty ? 'Границы изменены — план ещё посчитан по старым.' : 'План посчитан по границам, заданным вручную.'}</span>
          {dirty && <button className="btn" disabled={busy || loading} onClick={() => onApply(manual())}>Пересчитать план по новым границам</button>}
          {editing && <button className="btn quiet" disabled={busy || loading} onClick={resetAll}>Вернуть автоматическое деление</button>}
        </div>
      )}
      {err && <div className="error">{err}</div>}
      <div className="terr-grid">
        <table className="grid terr-table">
          <thead>
            <tr><th>МП</th><th title="Процент показывает отклонение от среднего количества врачей по зонам">Врачей в зоне</th><th>Аптек в зоне</th>{editing && <><th>Размер</th><th>За город</th></>}
              {!dirty && <><th>Визитов к врачам</th><th>Средний день</th><th>Пробег</th></>}</tr>
          </thead>
          <tbody>
            {zones.map((z, i) => {
              const st = stats.get(z.id)
              const off = avgDocs ? Math.round((z.doctors / avgDocs - 1) * 100) : 0
              return (
                <tr key={z.id} className={rep === z.id ? 'sel' : ''} onClick={() => onRep(z.id)}>
                  <td><span className="sw" style={{ background: z.color }} />МП {z.id}</td>
                  <td>{z.doctors}
                    {z.doctors === 0 ? <span className="dev empty"> зона пустая</span>
                      : zones.length > 1 && Math.abs(off) >= 5 && <span className={`dev ${Math.abs(off) >= 25 ? 'up' : 'down'}`}> {off > 0 ? '+' : ''}{off}%</span>}</td>
                  <td>{z.pharmacies}</td>
                  {editing && <><td className="sz" onClick={(e) => e.stopPropagation()}>
                    <button aria-label={`Уменьшить зону ${z.id}`} disabled={loading} onClick={() => resize(i, -1)}>−</button>
                    <button aria-label={`Увеличить зону ${z.id}`} disabled={loading} onClick={() => resize(i, 1)}>+</button>
                  </td>
                  <td className="ext" onClick={(e) => e.stopPropagation()}>
                    <select aria-label={`Расширить зону ${z.id} за город`} value={z.extension} disabled={loading}
                      onChange={(e) => extend(i, Number(e.target.value))}>
                      {[0, 5, 10, 15, 20, 30].map((km) => <option key={km} value={km}>{km ? `+${km} км` : 'нет'}</option>)}
                    </select>
                    {z.extension > 0 && <small>{z.suburb.doctors} врачей, {z.suburb.pharmacies} аптек</small>}
                  </td></>}
                  {!dirty && st && <><td>{st.visits}</td><td>{num(st.hours)} ч</td><td>{num(st.km)} км</td></>}
                </tr>
              )
            })}
          </tbody>
        </table>

        {editing && <div className="map-modes">
          <div className="seg" role="tablist" aria-label="Режим правки зон">
            {MODES.filter((m) => m.key !== 'areas' || drawn.length > 0).map((m) => (
              <button key={m.key} role="tab" aria-selected={mode === m.key}
                onClick={() => (m.key === 'edit' ? startEdit() : (setMode(m.key), setDraft([])))}>{m.label}</button>
            ))}
          </div>
          <p className="mode-hint">{hint}</p>
          {editing && mode === 'draw' && (
            <div className="mode-tools">
              <label>Кому отдать область
                <select value={drawRep} onChange={(e) => setDrawRep(Number(e.target.value))}>
                  {zones.map((z, i) => <option key={z.id} value={i}>МП {z.id}</option>)}
                </select>
              </label>
              <span className="muted">{draft.length ? `углов: ${draft.length}` : 'кликните по карте'}</span>
              <button className="btn small" disabled={draft.length < 3 || loading} onClick={finishDraft}>Готово</button>
              {draft.length > 0 && <button className="btn quiet small" onClick={() => setDraft([])}>Отменить</button>}
            </div>
          )}
          {selectingAreas && (
            <div className="mode-tools">
              <div className="drawn-list" role="group" aria-label="Нарисованные области">
                {drawn.map((d, i) => (
                  <button key={i} type="button" className="drawn-chip" style={{ borderColor: colorOf(d.rep) }}
                    aria-pressed={i === selectedArea} disabled={busy || loading} onClick={() => setSelectedArea(i)}>
                    Область {i + 1} · МП {zones[d.rep]?.id ?? d.rep + 1}
                  </button>
                ))}
              </div>
              <button type="button" className="btn small" disabled={selectedArea === null || busy || loading} onClick={removeSelectedArea}>
                {selectedArea === null ? 'Удалить область' : `Удалить область ${selectedArea + 1}`}
              </button>
            </div>
          )}
          {editing && mode === 'assign' && Object.keys(moves).length > 0 && (
            <div className="mode-tools">
              <span className="muted">Переназначено учреждений: {Object.keys(moves).length}</span>
              <button className="btn quiet small" onClick={() => { setMoves({}); preview(manual({ assign: undefined })) }}>Сбросить</button>
            </div>
          )}
        </div>}

        <div className="mapbox terr-map">
          <MapContainer center={center} zoom={10} style={{ height: '100%', width: '100%' }} preferCanvas scrollWheelZoom>
            <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; участники <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' />
            <FitAll pts={zones.flatMap((z) => z.polygon as LL[])} k={plan.summary.city + zones.length} />
            <FocusArea polygon={selectingAreas ? selectedPolygon : undefined} />
            <DrawEvents active={editing && mode === 'draw'} onAdd={(p) => setDraft((d) => [...d, p])} onFinish={finishDraft} />

            {shownPolys.map((poly, i) => poly.length > 2 && (
              <Polygon key={`${i}-${poly.length}-${poly[0]?.[0]}-${poly[0]?.[1]}`} positions={poly}
                interactive={mode === 'move'}
                pathOptions={{ color: colorOf(i), weight: rep === i + 1 ? 3 : 1.5, fillOpacity: rep === i + 1 ? 0.18 : 0.08 }}
                eventHandlers={{ click: () => onRep(i + 1) }} />
            ))}
            {drawn.map((d, i) => {
              const selected = selectingAreas && i === selectedArea
              return (
                <Polygon key={`d${i}-${selectingAreas}`} positions={d.polygon} interactive={selectingAreas}
                  pathOptions={{ color: colorOf(d.rep), weight: selected ? 5 : 2.5,
                    dashArray: selected ? undefined : '6 4', fillOpacity: selected ? 0.28 : 0.12 }}
                  eventHandlers={{ click: () => { if (selectingAreas && !busy && !loading) setSelectedArea(i) } }}>
                  {selectingAreas && <Tooltip permanent direction="center">Область {i + 1}</Tooltip>}
                </Polygon>
              )
            })}
            {draft.length > 0 && <Polyline positions={draft} pathOptions={{ color: colorOf(drawRep), weight: 2, dashArray: '4 4' }} />}
            {draft.map((p, i) => <CircleMarker key={`dp${i}`} center={p} radius={4} pathOptions={{ color: colorOf(drawRep), fillOpacity: 1 }} />)}

            {/* Серые точки приходят позже. Их слой всегда ниже цветных маршрутов и границ. */}
            <Pane name="unplanned-points" style={{ zIndex: 399, pointerEvents: 'none' }}>
              {showAll && outsidePlan.map(([id, lat, lon, isLpu]) => (
                <CircleMarker key={id} center={[lat, lon]} radius={isLpu ? 3 : 1.5} interactive={false}
                  pathOptions={{ color: '#8691A3', weight: isLpu ? 1 : 0, fillColor: '#8691A3', fillOpacity: isLpu ? 0.08 : 0.35 }} />
              ))}
            </Pane>
            {plan.facilities.map((f) => {
              const orig = (f.visits[0]?.rep ?? 1) - 1
              const cur = moves[f.id] ?? liveAssign?.[f.id] ?? orig
              const color = colorOf(cur)
              return (
                <CircleMarker key={f.id} center={[f.lat, f.lon]} radius={f.kind === 'lpu' ? (mode === 'assign' ? 5 : 3) : 2}
                  interactive={mode === 'assign'}
                  pathOptions={{ color: moves[f.id] !== undefined ? '#16233A' : color, fillColor: color, fillOpacity: 0.85, weight: moves[f.id] !== undefined ? 2 : 0 }}>
                  {editing && mode === 'assign' && (
                    <Popup>
                      <div className="assign-pop">
                        <b>{f.name}</b>
                        <div className="muted">{f.address}{f.kind === 'lpu' ? ` · врачей в плане: ${f.doctors}` : ''}</div>
                        <div>Сейчас: МП {cur + 1}. Отдать:</div>
                        <div className="assign-btns">
                          {zones.map((z, i) => i !== cur && (
                            <button key={z.id} style={{ borderColor: z.color }} onClick={() => reassign(f.id, i)}>МП {z.id}</button>
                          ))}
                          {moves[f.id] !== undefined && <button onClick={() => reassign(f.id, null)}>как было</button>}
                        </div>
                      </div>
                    </Popup>
                  )}
                </CircleMarker>
              )
            })}

            {editing && mode === 'move' && zones.map((z, i) => (
              <Marker key={`h${z.id}`} position={z.center} icon={handle(z)} draggable
                eventHandlers={{
                  dragend: (e) => { const ll = (e.target as L.Marker).getLatLng(); move(i, [ll.lat, ll.lng]) },
                  click: () => onRep(z.id),
                }}>
                <Tooltip direction="top" offset={[0, -12]}>Зона МП {z.id}: перетащите, чтобы сдвинуть</Tooltip>
              </Marker>
            ))}

            {mode === 'edit' && midpoints.map((m) => (
              <CircleMarker key={`m${m.a}|${m.b}`} center={m.at} radius={4}
                pathOptions={{ color: '#16233A', weight: 1, fillColor: '#fff', fillOpacity: 1 }}
                eventHandlers={{ click: () => addVertex(m.a, m.b, m.at) }} />
            ))}
            {mode === 'edit' && vertices.map(([k, p]) => (
              <Marker key={`v${k}`} position={p} icon={vIcon} draggable
                eventHandlers={{
                  dragend: (e) => { const ll = (e.target as L.Marker).getLatLng(); moveVertex(k, [ll.lat, ll.lng]) },
                  contextmenu: () => removeVertex(k),
                }} />
            ))}
          </MapContainer>
          <div className="map-legend">
            <span><i className="ml-in" />в плане (цвет — медпред)</span>
            <label><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
              <i className="ml-out" />в зоне, но не в плане: {outsidePlan.length}</label>
          </div>
          {loading && <div className="map-note">{suburbNote ? 'Загружаю учреждения пригородов — первый раз до 3 минут…' : 'Пересчитываю границы…'}</div>}
        </div>
      </div>
    </section>
  )
}
