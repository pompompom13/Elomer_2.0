import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { CareFilter, CityData, ClientBaseItem, Facility, Meta } from '../types'
import { PlanMap, type MapPoint } from './PlanMap'
import { CityStatsView } from './CityStatsView'
import { FacilityCard } from './FacilityCard'
import { ALL_CATEGORIES, TURNOVER_STEPS } from './FiltersPanel'
import { CARE_LABELS, CareLevelField, countForCare, matchesCare, usesPartialCareCount } from './CareLevelField'
import { loadEdits, patchEdits, saveEdits, type Edits } from '../store'
import { num } from '../format'

interface Props {
  meta: Meta
  slug: string
  specialty: string
  initialClient: string | null
  initialCare?: CareFilter
  onBack: () => void
  onPlan: (slug: string, spec: string, care: CareFilter) => void
}

const ROW_LIMIT = 400
// цвета категорий: фиксированный порядок, «прочее» — серый
const CAT_COLOR: Record<string, string> = {
  polyclinic_adult: '#2A52AD', ambulatory: '#2A52AD', polyclinic_child: '#0E8AA3', hospital: '#C2562E',
  private_center: '#B8862B', cardio_center: '#6A4FA3', womens: '#8E3B5E', dental: '#3E8E6A',
}
const NET_COLORS = ['#2A52AD', '#C2562E', '#0E8AA3', '#6A4FA3', '#B8862B', '#3D84D6']
const GREY = '#9AA6B8'
const CLIENT = '#C2562E' // ЛПУ из базы заказчика
type Paint = 'kind' | 'category' | 'network' | 'turnover'

// Оценку числа врачей считает сервер (тот же алгоритм, что и в планировщике)
export const estimateDoctors = (_meta: Meta, f: Facility, _spec: string): number => f.est ?? 0

/** «База» → город: статистика, карта со слоями и список учреждений с правками для плана. */
export function BaseView({ meta, slug, specialty: spec0, initialClient, initialCare = 'all', onBack, onPlan }: Props) {
  // специальность выбирается здесь же; пусто — «не выбрана»: без подсветки и без колонки врачей
  const [specialty, setSpecialty] = useState(spec0)
  useEffect(() => setSpecialty(spec0), [spec0])
  const apiSpec = specialty || 'Кардиолог'
  const [city, setCity] = useState<CityData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loadingCity, setLoadingCity] = useState(false)
  const [edits, setEdits] = useState<Edits>(() => loadEdits(slug, specialty))
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<'all' | 'lpu' | 'pharmacy' | 'target'>('target')
  const [own, setOwn] = useState<'all' | 'state' | 'private'>('all')
  const [cat, setCat] = useState('')
  const [care, setCare] = useState(initialCare)
  const [net, setNet] = useState('')
  const [turn, setTurn] = useState<number | null>(null)
  const [paint, setPaint] = useState<Paint>('kind')
  const [card, setCard] = useState<string | null>(null)
  const loadCard = useCallback(() => api.facility(slug, card!), [slug, card])
  const [clients, setClients] = useState<ClientBaseItem[]>([])
  const [layers, setLayers] = useState<{ ours: boolean; client: string | null }>({ ours: !initialClient, client: initialClient })
  useEffect(() => { api.clientBases().then((l) => setClients(l.filter((c) => !c.job?.running && c.rows))).catch(() => undefined) }, [])

  const load = useCallback(() => {
    setErr(null)
    const get = async () => {
      const ours = layers.ours || !layers.client ? await api.city(slug, apiSpec) : null
      const cl = layers.client ? await api.city(slug, apiSpec, layers.client) : null
      const base = (ours ?? cl)!
      const facilities = ours && cl
        ? [...base.facilities, ...cl.facilities.filter((f) => f.source === 'client')]
        : base.facilities
      return { ...base, facilities: facilities.filter((f) => f.kind !== 'pharmacy' || f.in_registry !== false) }
    }
    get().then(setCity).catch(async () => {
      // города ещё нет на диске — скачиваем базу
      setLoadingCity(true)
      try { await api.loadCity(slug); setCity(await get()) } catch (e) { setErr((e as Error).message) } finally { setLoadingCity(false) }
    })
  }, [slug, apiSpec, layers])
  useEffect(() => { setCity(null); load() }, [load])
  useEffect(() => { setEdits(loadEdits(slug, specialty)) }, [slug, specialty])
  useEffect(() => {
    if (!city?.geocoding.running) return
    const t = setTimeout(load, 8000)
    return () => clearTimeout(t)
  }, [city, load])

  const change = (id: string, ch: { override?: number | null; off?: boolean }) => {
    const next = patchEdits(edits, id, ch)
    setEdits(next)
    saveEdits(slug, specialty, next)
  }

  const nets = useMemo(() => {
    const m = new Map<string, number>()
    for (const f of city?.facilities ?? []) if (f.kind === 'pharmacy' && f.network_name) m.set(f.network_name, (m.get(f.network_name) ?? 0) + 1)
    return [...m.entries()].sort((a, b) => b[1] - a[1])
  }, [city])
  const netColor = useMemo(() => new Map(nets.slice(0, NET_COLORS.length).map(([n], i) => [n, NET_COLORS[i]])), [nets])

  const rows = useMemo(() => {
    if (!city) return []
    const ql = q.trim().toLowerCase()
    return city.facilities
      .map((f) => {
        const total = specialty ? edits.overrides[f.id] ?? estimateDoctors(meta, f, specialty) : 0
        return { f, est: countForCare(total, f.care_level, care) }
      })
      .filter(({ f, est }) =>
        (kind === 'all' || (kind === 'target' ? f.kind === 'pharmacy' || !specialty || est > 0 : f.kind === kind)) &&
        (own === 'all' || f.ownership === own) &&
        (!cat || f.kind !== 'lpu' || f.category === cat || (cat === 'polyclinic_adult' && f.category === 'ambulatory')) &&
        (f.kind !== 'lpu' || matchesCare(f.care_level, care)) &&
        (!net || f.kind !== 'pharmacy' || (f.network_name || 'Без сети') === net) &&
        (!turn || f.kind !== 'pharmacy' || (f.turnover ?? 0) >= turn) &&
        (!ql || f.name.toLowerCase().includes(ql) || f.address.toLowerCase().includes(ql) || (f.network_name ?? '').toLowerCase().includes(ql)))
      .sort((a, b) => (a.f.kind === b.f.kind ? b.est - a.est || a.f.name.localeCompare(b.f.name) : a.f.kind === 'lpu' ? -1 : 1))
  }, [city, q, kind, own, cat, care, net, turn, edits.overrides, meta, specialty])

  const totals = useMemo(() => {
    let docs = 0, lpu = 0, ph = 0
    for (const { f, est } of rows) {
      if (edits.excluded.includes(f.id)) continue
      if (f.kind === 'pharmacy') ph++
      else if (est > 0 || !specialty) { lpu++; docs += est }
    }
    return { docs, lpu, ph }
  }, [rows, edits.excluded])

  const turnMax = useMemo(() => Math.max(1, ...rows.map(({ f }) => f.turnover ?? 0)), [rows])
  const points: MapPoint[] = rows.map(({ f, est }) => {
    let color: string | undefined
    let r: number | undefined
    if (paint === 'category' && f.kind === 'lpu') color = CAT_COLOR[f.category] ?? GREY
    if (paint === 'network' && f.kind === 'pharmacy') color = netColor.get(f.network_name ?? '') ?? GREY
    if (paint === 'turnover' && f.kind === 'pharmacy') {
      r = f.turnover ? 3 + 7 * Math.sqrt(f.turnover / turnMax) : 2.5
      color = f.turnover ? '#4596E0' : GREY
    }
    if ((paint === 'network' || paint === 'turnover') && f.kind === 'lpu') color = '#C9D3E1'
    if ((paint === 'category') && f.kind === 'pharmacy') color = '#C9D3E1'
    if (f.source === 'client' && (paint === 'kind' || layers.ours)) color = CLIENT
    if (f.source === 'client' && layers.ours) r = 6.5  // поверх наших точек и чуть крупнее
    return {
      id: f.id, lat: f.lat, lon: f.lon, kind: f.kind, color, r,
      title: `${f.name}${f.kind === 'lpu' ? (specialty ? `, врачей: ${est}` : '') : f.turnover ? `, оборот ${num(f.turnover / 1e6, 1)} млн ₽` : ''}`,
      dim: edits.excluded.includes(f.id),
    }
  })

  points.sort((a, b) => (a.color === CLIENT ? 1 : 0) - (b.color === CLIENT ? 1 : 0))
  const legend: { c: string; l: string }[] = paint === 'category'
    ? ALL_CATEGORIES.map((c) => ({ c: CAT_COLOR[c.key], l: c.label }))
    : paint === 'network'
      ? [...nets.slice(0, NET_COLORS.length).map(([n], i) => ({ c: NET_COLORS[i], l: n })), { c: GREY, l: 'другие и без сети' }]
      : paint === 'turnover'
        ? [{ c: '#4596E0', l: 'аптека: чем больше круг, тем выше оборот' }, { c: GREY, l: 'оборот неизвестен' }]
        : [{ c: '#2A52AD', l: layers.client && layers.ours ? 'наши ЛПУ' : 'ЛПУ' }, { c: '#4596E0', l: 'аптеки' }]
  if (layers.client && (paint === 'kind' || layers.ours)) legend.splice(paint === 'kind' ? 1 : 0, 0, { c: CLIENT, l: 'ЛПУ заказчика' })
  const resetFilters = () => { setSpecialty(''); setQ(''); setKind('target'); setOwn('all'); setCat(''); setNet(''); setTurn(null); setPaint('kind') }
  const hasEdits = Object.keys(edits.overrides).length + edits.excluded.length > 0
  const isClient = (id: string | null) => !!id && id.startsWith('cb_')

  const cardF = card ? city?.facilities.find((f) => f.id === card) : null

  return (
    <div className="cbase">
      <CityStatsView slug={slug} meta={meta} specialty={specialty} care={care} client={layers.ours ? null : layers.client} onBack={onBack}
        actions={<button className="btn" onClick={() => onPlan(slug, specialty, care)}>Спланировать работу в городе</button>} />

      {err && <div className="error">{err}</div>}
      {loadingCity && <div className="loading">Загружаю базу города из OpenStreetMap — крупный город до 3 минут…</div>}
      {city && (
        <>
          <div className="section-head cbase-head">
            <h2>Учреждения</h2>
            <span className="muted">
              Показано {num(totals.lpu, 0)} ЛПУ{specialty ? ` (около ${num(totals.docs, 0)} врачей, ${specialty.toLowerCase()})` : ''} и {num(totals.ph, 0)} аптек.
              Правки здесь сразу учитываются в плане города.
              {city.geocoding.running && ` Уточняем адреса: ${city.geocoding.done} из ${city.geocoding.total}.`}
            </span>
          </div>
          <div className="layer-bar">
            <label className="chk layer-spec">
              <span className="muted">Специальность:</span>
              <select value={specialty} aria-label="Специальность врачей" onChange={(e) => setSpecialty(e.target.value)}>
                <option value="">не выбрана</option>
                {meta.specialties.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
            <span className="muted">Слои на карте и в списке:</span>
            <label className="chk"><input type="checkbox" checked={layers.ours}
              onChange={(e) => setLayers((l) => ({ ...l, ours: e.target.checked || !l.client }))} />наша база</label>
            <label className="chk layer-client">
              <i style={{ background: CLIENT }} />
              <select value={layers.client ?? ''} aria-label="База заказчика"
                onChange={(e) => setLayers((l) => ({ ours: e.target.value ? l.ours : true, client: e.target.value || null }))}>
                <option value="">база заказчика — нет</option>
                {clients.map((c) => <option key={c.id} value={c.id}>база заказчика: {c.name}</option>)}
              </select>
            </label>
            <span className="spacer" />
            <button className="linklike" onClick={resetFilters}>Сбросить фильтры</button>
            {hasEdits && <button className="linklike muted-l" onClick={() => {
              if (window.confirm('Сбросить все правки базы этого города (исключённые учреждения и своё число врачей)?')) {
                const empty = { overrides: {}, excluded: [] }
                setEdits(empty); saveEdits(slug, specialty, empty)
              }
            }}>Сбросить правки базы</button>}
          </div>
          <div className="base-tools">
            <input type="text" placeholder="Название, адрес или сеть" value={q} onChange={(e) => setQ(e.target.value)} />
            <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} aria-label="Какие записи">
              <option value="target">Подходящие для плана</option>
              <option value="lpu">Все ЛПУ</option>
              <option value="pharmacy">Аптеки</option>
              <option value="all">Все записи</option>
            </select>
            <select value={own} onChange={(e) => setOwn(e.target.value as typeof own)} aria-label="Собственность">
              <option value="all">Любая собственность</option>
              <option value="state">Государственные</option>
              <option value="private">Частные</option>
            </select>
            {kind !== 'pharmacy' && (
              <>
                <select value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Тип ЛПУ">
                  <option value="">Все типы ЛПУ</option>
                  {ALL_CATEGORIES.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                </select>
                <CareLevelField value={care} onChange={setCare} />
              </>
            )}
            {kind !== 'lpu' && (
              <>
                <select value={net} onChange={(e) => setNet(e.target.value)} aria-label="Аптечная сеть">
                  <option value="">Все аптечные сети</option>
                  {nets.slice(0, 40).map(([n, c]) => <option key={n} value={n}>{n} ({c})</option>)}
                  <option value="Без сети">Без сети</option>
                </select>
                <select value={turn ?? ''} onChange={(e) => setTurn(e.target.value === '' ? null : Number(e.target.value))} aria-label="Оборот аптеки">
                  {TURNOVER_STEPS.map((t) => <option key={t.label} value={t.v ?? ''}>Оборот: {t.label}</option>)}
                </select>
              </>
            )}
            <span className="spacer" />
            <label className="paint-l">Карта:
              <select value={paint} onChange={(e) => setPaint(e.target.value as Paint)} aria-label="Раскраска карты">
                <option value="kind">ЛПУ и аптеки</option>
                <option value="category">ЛПУ по типам</option>
                <option value="network">Аптеки по сетям</option>
                <option value="turnover">Аптеки по обороту</option>
              </select>
            </label>
          </div>
          <div className="base">
            <div className="table-wrap" style={{ height: 'var(--pane-h)', maxHeight: 'none' }}>
              <table className="grid">
                <thead>
                  <tr><th>В плане</th><th>Учреждение</th><th>Тип</th><th>Сеть</th><th>Адрес</th><th>{specialty ? `${specialty}ов` : 'Оборот'}</th></tr>
                </thead>
                <tbody>
                  {rows.slice(0, ROW_LIMIT).map(({ f, est }) => {
                    const off = edits.excluded.includes(f.id)
                    return (
                      <tr key={f.id} className={`${off ? 'off' : ''}${card === f.id ? ' sel' : ''}`}>
                        <td><input type="checkbox" checked={!off} aria-label={`Учитывать ${f.name}`}
                          onChange={(e) => change(f.id, { off: !e.target.checked })} /></td>
                        <td>
                          <button className="rowlink" onClick={() => !isClient(f.id) && setCard(f.id)}>
                            <span className={`dot ${f.kind}`} style={f.source === 'client' ? { background: CLIENT } : undefined} />{f.name}</button>
                          {f.source === 'client' && <span className="client-tag">заказчик</span>}
                          {f.kind === 'lpu' && !!f.known_doctors?.[specialty] && <span className="known-tag" title="Столько разных врачей этой специальности посещали медпреды в реальных проектах.">подтверждено: {f.known_doctors[specialty]}</span>}
                        </td>
                        <td className="muted">{meta.categories[f.category] ?? f.category}{f.ownership === 'state' ? ', гос.' : ', частн.'}
                          {f.care_level && <small className="care-caption">{CARE_LABELS[f.care_level]}</small>}</td>
                        <td>{f.network_name || <span className="muted">—</span>}</td>
                        <td>{f.address || <span className="muted">уточняется</span>}</td>
                        <td>
                          {f.kind === 'lpu' ? (!specialty ? <span className="muted">—</span> :
                            usesPartialCareCount(f.care_level, care)
                              ? <span title="Для правки общего числа врачей выберите «Все звенья»">{num(est, 0)}</span> :
                            <input type="number" min={0} max={99} value={est} aria-label={`Число врачей в ${f.name}`}
                              onChange={(e) => change(f.id, { override: e.target.value === '' ? null : Number(e.target.value) })} />
                          ) : f.turnover ? <span className="muted">{num(f.turnover / 1e6, 1)} млн ₽</span> : '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              {rows.length > ROW_LIMIT && <p className="muted" style={{ padding: '6px 10px' }}>Показаны первые {ROW_LIMIT} из {num(rows.length, 0)} — уточните поиск. На карте видны все.</p>}
            </div>
            <div>
              <div className="mapbox" style={{ height: 'var(--pane-h)' }}>
                <PlanMap center={city.center} points={points} fitKey={`${city.slug}-${kind}-${cat}-${care}-${net}-${layers.ours}-${layers.client}`} onSelect={(id) => !isClient(id) && setCard(id)} />
              </div>
              <div className="legend base-legend">
                {legend.map((x) => <span key={x.l}><i className="mk" style={{ background: x.c }} />{x.l}</span>)}
              </div>
            </div>
          </div>
        </>
      )}

      {card && cardF !== undefined && (
        <FacilityCard load={loadCard} specialty={specialty} editing={{
          override: edits.overrides[card], excluded: edits.excluded.includes(card),
          onOverride: (n) => change(card, { override: n }), onExclude: (off) => change(card, { off }),
        }}
          onClose={() => setCard(null)} />
      )}
    </div>
  )
}
