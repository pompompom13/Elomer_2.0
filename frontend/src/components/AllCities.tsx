import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import { api } from '../api'
import type { BaseExportQuery, BaseQuery, BaseRow, BaseSummary, CareFilter, ClientBaseItem, CityItem, Meta, Ownership } from '../types'
import { num, plural } from '../format'
import { ALL_CATEGORIES, TURNOVER_STEPS } from './FiltersPanel'
import { DotsMap, type Dot } from './DotsMap'
import { CARE_LABELS, CareLevelField } from './CareLevelField'
import { BaseExportDialog } from './BaseExportDialog'
import { FacilityCard } from './FacilityCard'

// цвета точек: учреждения города и вне городов (пригороды, область) — светлее
const DOT = { lpu: '#2A52AD', lpuOut: '#7FA0E0', ph: '#2E86D6', phOut: '#8CC3F0' }
const dotLpu = (d: Dot) => (d[3] ? DOT.lpuOut : DOT.lpu)
const dotPh = (d: Dot) => (d[3] ? DOT.phOut : DOT.ph)
// слой «врачи»: точка — ЛПУ, площадь растёт с числом врачей в нём
const DOC = { in: '#6B4FBB', out: '#A996E0' }
const dotDoc = (d: Dot) => (d[3] ? DOC.out : DOC.in)
import { Drop } from './Drop'

interface Props {
  meta: Meta
  cities: CityItem[]
  specialty: string
  filters: BaseFilters
  onFiltersChange: Dispatch<SetStateAction<BaseFilters>>
  onOpenCity: (slug: string, spec: string) => void
  onCreateProject: (slugs: string[], spec: string) => void
}

interface BaseFilters {
  sel: string[] | null
  kinds: ('lpu' | 'pharmacy')[]
  lpuOwn: Ownership[]
  lpuCare: CareFilter
  phOwn: Ownership[]
  cats: string[]
  spec: string
  nets: string[] | null
  turn: number | null
  scope: 'city' | 'suburbs' | 'region'
  client: string | null
}

const SCOPES: { k: BaseFilters['scope']; l: string; hint: string }[] = [
  { k: 'city', l: 'Только города', hint: 'Учреждения в границах городов от 50 тыс. жителей.' },
  { k: 'suburbs', l: 'Города с пригородами', hint: 'Город плюс посёлки и небольшие города вокруг (до 30 км за границей). Аптеки — из реестра, ЛПУ — после загрузки пригородов в «Данных».' },
  { k: 'region', l: 'Области целиком', hint: 'Строка на область: её города, пригороды и остальная территория. Москва и Московская область — разные строки, их можно выбрать вместе.' },
]

const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
const n = (v?: number | null) => (v == null ? '—' : num(v, 0))
const mln = (v?: number | null) => (v ? `${num(v / 1e6, 1)} млн` : '—')
const ownLabel = (o: Ownership[]) => (o.length === 2 ? 'все' : o[0] === 'state' ? 'гос.' : 'частные')
type SortKey = 'city' | 'population' | 'lpu' | 'doctors' | 'doctors_per_10k' | 'pharmacies' | 'ph_per_10k' | 'turnover_median'
type MapMetric = 'doctors' | 'lpu' | 'pharmacies'

/** «База» → Россия: фильтры в одну строку, итоги, карта, таблица городов, выгрузка и проект из выбранных. */
/** Фильтры «Базы» по умолчанию: все ЛПУ и все аптеки, области целиком. */
export const baseDefaults = (): BaseFilters => ({
  sel: null, kinds: ['lpu', 'pharmacy'], lpuOwn: ['state', 'private'], lpuCare: 'all', phOwn: ['state', 'private'],
  cats: ALL_CATEGORIES.map((c) => c.key), spec: '', nets: null, turn: null, scope: 'region', client: null,
})
export const baseQueryOf = (f: BaseFilters): BaseQuery => ({
  cities: f.sel, kinds: f.kinds, lpu_ownership: f.lpuOwn, lpu_care: f.lpuCare, pharmacy_ownership: f.phOwn, categories: f.cats,
  specialty: f.spec, pharmacy_networks: f.nets, pharmacy_min_turnover: f.turn, scope: f.client ? 'city' : f.scope, client_base: f.client,
})

export function AllCities({ meta, cities, specialty, filters: f, onFiltersChange: setF, onOpenCity, onCreateProject }: Props) {
  const loaded = useMemo(() => cities.filter((c) => c.loaded), [cities])
  const defaults = baseDefaults
  const isDefault = JSON.stringify(f) === JSON.stringify(defaults())
  const set = <K extends keyof BaseFilters>(k: K, v: BaseFilters[K]) => setF((x) => ({ ...x, [k]: v }))

  const [data, setData] = useState<BaseSummary | null>(null)
  const [clients, setClients] = useState<ClientBaseItem[]>([])
  useEffect(() => { api.clientBases().then((l) => setClients(l.filter((c) => !c.job?.running && !c.job?.error && c.rows))).catch(() => undefined) }, [])
  const [allNets, setAllNets] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [busyXlsx, setBusyXlsx] = useState<string | null>(null)
  const [exportSettings, setExportSettings] = useState<{ query: BaseExportQuery; tag: string } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [sort, setSort] = useState<{ k: SortKey; d: 1 | -1 }>({ k: 'population', d: -1 })
  const [metric, setMetric] = useState<MapMetric>('lpu')
  const [pts, setPts] = useState<{ key: string; query: BaseQuery; revision: string; lpu: number[][]; pharmacies: number[][] } | null>(null)
  const [ptsBusy, setPtsBusy] = useState(false)
  const [card, setCard] = useState<{ query: BaseQuery; revision: string; kind: 'lpu' | 'pharmacy'; index: number } | null>(null)
  const pointKind = metric === 'pharmacies' ? 'pharmacy' : 'lpu'
  const describePoint = useCallback(async (index: number) => {
    if (!pts) return null
    const point = await api.basePoint(pts.query, pointKind, index, pts.revision)
    const name = point.name.toLowerCase()
    const network = point.network.toLowerCase()
    const own = network && !name.includes(network) && !network.includes(name)
      ? [point.ownership, `сеть ${point.network}`].filter(Boolean).join(', ') : point.ownership
    const spec = point.specialty ? ` (${point.specialty.toLowerCase()})` : ' всех специальностей'
    const doctors = point.doctors === null ? '' :
      `≈ ${num(point.doctors, 0)} ${plural(point.doctors, 'врач', 'врача', 'врачей')}${spec}, оценка`
    const address = point.city && !point.address.toLowerCase().includes(point.city.toLowerCase())
      ? [point.city, point.address].filter(Boolean).join(', ') : point.address
    return { title: point.name, lines: [
      [point.type, point.care_level && CARE_LABELS[point.care_level], own].filter(Boolean).join(' · '),
      doctors,
      address,
    ] }
  }, [pts, pointKind])
  const openPointCard = useCallback((index: number) => {
    if (pts) setCard({ query: pts.query, revision: pts.revision, kind: pointKind, index })
  }, [pts, pointKind])
  const loadCard = useCallback(() => api.baseFacility(card!.query, card!.kind, card!.index, card!.revision), [card])
  // размер точки врачей — относительно типичного ЛПУ (90-й перцентиль): от 0,8 до 5 обычных точек
  const sizeDoc = useMemo(() => {
    const v = (pts?.lpu ?? []).map((d) => d[4] || 0).filter((x) => x > 0).sort((a, b) => a - b)
    const p90 = v.length ? v[Math.floor(v.length * 0.9)] : 1
    return (d: Dot) => (d[4] ? 0.8 + 2.4 * Math.min(1.75, Math.sqrt(d[4] / p90)) : 0)
  }, [pts])
  const [compare, setCompare] = useState(false)

  const query: BaseQuery = baseQueryOf(f)
  const isRegion = f.scope === 'region'
  const key = JSON.stringify(query)
  useEffect(() => {
    if (f.sel && f.sel.length === 0) { setData(null); setLoading(false); return }
    let alive = true
    setLoading(true)
    const t = setTimeout(() => {
      api.baseSummary(query).then((d) => {
        if (!alive) return
        setData(d)
        setErr(null)
        setAllNets((cur) => (d.all_networks?.length ? [...new Set([...cur, ...d.all_networks])] : cur))
      }).catch((e) => alive && setErr((e as Error).message)).finally(() => alive && setLoading(false))
    }, 300)
    return () => { alive = false; clearTimeout(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  // точки всех учреждений для карты — только когда карта показывает ЛПУ или аптеки
  useEffect(() => {
    if ((f.sel && f.sel.length === 0) || pts?.key === key) return
    let alive = true
    setPtsBusy(true)
    const t = setTimeout(() => {
      api.basePoints(query).then((p) => { if (alive) setPts({ key, query, ...p }) })
        .catch((e) => alive && setErr((e as Error).message)).finally(() => alive && setPtsBusy(false))
    }, 400)
    return () => { alive = false; clearTimeout(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, metric])
  useEffect(() => { if (!showL && metric !== 'pharmacies') setMetric('pharmacies'); else if (!showP && metric === 'pharmacies') setMetric('lpu') })

  const rows = useMemo(() => {
    const r = [...(data?.rows ?? [])]
    const val = (x: BaseRow) => (sort.k === 'city' ? x.city : (x[sort.k] ?? -1)) as number | string
    r.sort((a, b) => { const va = val(a), vb = val(b); return (va < vb ? -1 : va > vb ? 1 : 0) * sort.d })
    return r
  }, [data, sort])

  const t = data?.total
  const showL = f.kinds.includes('lpu'), showP = f.kinds.includes('pharmacy')
  const clientName = f.client ? clients.find((c) => c.id === f.client)?.name : null
  const scopeLabel = clientName ? `база «${clientName}»` : (f.sel === null ? (isRegion ? 'все области' : `все ${loaded.length} городов`) : `${f.sel.length} ${plural(f.sel.length, 'город', 'города', 'городов')}`) + (f.scope === 'suburbs' ? ' с пригородами' : isRegion && f.sel !== null ? ' (их области)' : '')

  function openExport(selected: string[] | null, onlyTarget = false, tag = 'all') {
    const areas = selected && (query.scope === 'region'
      ? { regions: selected.map((id) => id.slice('region:'.length)) }
      : { cities: selected })
    setExportSettings({ query: { ...query, ...areas, only_target: onlyTarget }, tag })
  }

  async function download(exportQuery: BaseExportQuery, tag: string) {
    setExportSettings(null)
    setBusyXlsx(tag)
    try { await api.downloadBase(exportQuery) }
    catch (e) { setErr((e as Error).message) } finally { setBusyXlsx(null) }
  }


  const regions = useMemo(() => [...new Set(loaded.map((c) => c.region))].sort(), [loaded])
  const [cq, setCq] = useState('')
  const clist = loaded.filter((c) => !cq.trim() || `${c.name} ${c.region}`.toLowerCase().includes(cq.trim().toLowerCase()))
  const chosen = f.sel ?? loaded.map((c) => c.slug)
  const th = (k: SortKey, label: string) => (
    <th aria-sort={sort.k === k ? (sort.d === 1 ? 'ascending' : 'descending') : undefined}>
      <button className="th-sort" onClick={() => setSort((s) => ({ k, d: s.k === k ? (-s.d as 1 | -1) : k === 'city' ? 1 : -1 }))}>
        {label}{sort.k === k ? (sort.d === 1 ? ' ↑' : ' ↓') : ''}
      </button>
    </th>
  )
  const cmp = rows.filter((r) => r.slug && picked.includes(r.slug))

  return (
    <div className="allc">
      <div className="headline" style={{ marginBottom: 10 }}>
        <h1>База</h1>
        <span className="period">ЛПУ, врачи и аптеки по городам России от 50 тыс. жителей. Загружено {loaded.length} из {cities.length} городов.</span>
      </div>

      <div className="reset-row">
        <button className="linklike" disabled={isDefault} onClick={() => { setF(defaults()); setPicked([]); setCompare(false); setCq('') }}>
          Сбросить все фильтры</button>
      </div>
      <div className="fbar">
        <Drop label="Города" value={f.sel === null ? 'все' : `${f.sel.length}`} wide active={f.sel !== null}>
          <input type="text" placeholder="Город или регион" value={cq} onChange={(e) => setCq(e.target.value)} />
          <div className="allc-quick">
            <button className="linklike" onClick={() => set('sel', null)}>Все</button>
            {cq && <button className="linklike" onClick={() => set('sel', [...new Set([...(f.sel ?? []), ...clist.map((c) => c.slug)])])}>Добавить найденные</button>}
            <button className="linklike" onClick={() => set('sel', [])}>Снять все</button>
          </div>
          <select value="" onChange={(e) => e.target.value && set('sel', [...new Set([...(f.sel ?? []), ...loaded.filter((c) => c.region === e.target.value).map((c) => c.slug)])])} aria-label="Регион">
            <option value="">Добавить область целиком…</option>
            {regions.map((r) => <option key={r}>{r}</option>)}
          </select>
          <ul className="allc-list">
            {clist.map((c) => (
              <li key={c.slug}><label className="chk">
                <input type="checkbox" checked={chosen.includes(c.slug)} onChange={() => set('sel', toggle(chosen, c.slug))} />
                <span className="nm">{c.name}</span><span className="n">{c.lpu} / {c.pharmacies}</span>
              </label></li>
            ))}
          </ul>
        </Drop>
        <Drop label="Специальность" value={f.spec || 'не выбрана'} active={!!f.spec}>
          <select value={f.spec} onChange={(e) => set('spec', e.target.value)} aria-label="Специальность врачей">
            <option value="">не показывать врачей</option>
            {meta.specialties.map((s) => <option key={s}>{s}</option>)}
          </select>
          <p className="muted drop-note">Оценка числа врачей этой специальности по городам и в выгрузке.</p>
        </Drop>
        <Drop label="Источник" value={f.client ? (clients.find((c) => c.id === f.client)?.name ?? 'база заказчика') : 'наша база'} wide active={!!f.client}>
          <label className="chk block scope-opt">
            <input type="radio" name="bsrc" checked={!f.client} onChange={() => { set('client', null); set('sel', null) }} />
            <span><b>Наша база</b><small>ЛПУ OpenStreetMap, реестр аптек и все дополнения.</small></span>
          </label>
          {clients.map((c) => (
            <label key={c.id} className="chk block scope-opt">
              <input type="radio" name="bsrc" checked={f.client === c.id} onChange={() => { set('client', c.id); set('sel', null); set('scope', 'city') }} />
              <span><b>{c.name}</b><small>База заказчика: его ЛПУ (и число врачей, если есть), аптеки — наши. {c.cities} {c.cities === 1 ? 'город' : 'городов'}.</small></span>
            </label>
          ))}
          {!clients.length && <p className="muted drop-note">Баз заказчиков пока нет — загрузите в разделе «Данные».</p>}
        </Drop>
        <Drop label="Территория" value={SCOPES.find((x) => x.k === f.scope)!.l} active={f.scope !== 'region'}>
          {SCOPES.map((x) => (
            <label key={x.k} className="chk block scope-opt">
              <input type="radio" name="bscope" checked={f.scope === x.k} onChange={() => { set('scope', x.k); setPicked([]) }} />
              <span><b>{x.l}</b><small>{x.hint}</small></span>
            </label>
          ))}
        </Drop>
        <button className="btn base-export" onClick={() => openExport(null)} disabled={!!busyXlsx || loading || !data}
          title={'Выгрузить в Excel: ' + scopeLabel}>
          {busyXlsx === 'all' ? 'Формирую…' : 'Выгрузить в Excel'}
        </button>
      </div>
        <details className="base-more">
          <summary>{!showL || !showP || f.lpuCare !== 'all' || f.cats.length < ALL_CATEGORIES.length || f.lpuOwn.length < 2 ||
            f.phOwn.length < 2 || f.nets !== null || f.turn ? 'Фильтры учреждений · применены' : 'Фильтры учреждений'}</summary>
          <div className="base-filter-grid">
        <Drop label="ЛПУ" value={!showL ? 'не брать' : f.lpuCare !== 'all' ? CARE_LABELS[f.lpuCare] : f.cats.length === ALL_CATEGORIES.length ? `все, ${ownLabel(f.lpuOwn)}` : `${f.cats.length} типа, ${ownLabel(f.lpuOwn)}`}
          active={!showL || f.lpuCare !== 'all' || f.cats.length < ALL_CATEGORIES.length || f.lpuOwn.length < 2}>
          <label className="chk block"><input type="checkbox" checked={showL} onChange={() => { const k = toggle(f.kinds, 'lpu'); if (k.length) set('kinds', k) }} />Включать ЛПУ</label>
          <CareLevelField value={f.lpuCare} onChange={(v) => set('lpuCare', v)} hint />
          <div className="chk-row">
            <label className="chk"><input type="checkbox" checked={f.lpuOwn.includes('state')} onChange={() => { const v = toggle(f.lpuOwn, 'state'); if (v.length) set('lpuOwn', v) }} />гос.</label>
            <label className="chk"><input type="checkbox" checked={f.lpuOwn.includes('private')} onChange={() => { const v = toggle(f.lpuOwn, 'private'); if (v.length) set('lpuOwn', v) }} />частные</label>
          </div>
          <div className="chips">
            {ALL_CATEGORIES.map((c) => (
              <button type="button" key={c.key} aria-pressed={f.cats.includes(c.key)}
                onClick={() => { const v = toggle(f.cats, c.key); if (v.length) set('cats', v) }}>{c.label}</button>
            ))}
          </div>
        </Drop>
        <Drop label="Аптеки" value={!showP ? 'не брать' : ownLabel(f.phOwn)} active={!showP || f.phOwn.length < 2}>
          <label className="chk block"><input type="checkbox" checked={showP} onChange={() => { const k = toggle(f.kinds, 'pharmacy'); if (k.length) set('kinds', k) }} />Включать аптеки</label>
          <div className="chk-row">
            <label className="chk"><input type="checkbox" checked={f.phOwn.includes('state')} onChange={() => { const v = toggle(f.phOwn, 'state'); if (v.length) set('phOwn', v) }} />гос.</label>
            <label className="chk"><input type="checkbox" checked={f.phOwn.includes('private')} onChange={() => { const v = toggle(f.phOwn, 'private'); if (v.length) set('phOwn', v) }} />частные</label>
          </div>
        </Drop>
        <Drop label="Аптечные сети" value={f.nets === null ? 'все' : `${f.nets.length}`} wide active={f.nets !== null}>
          <div className="allc-quick">
            <button className="linklike" onClick={() => set('nets', null)}>Все</button>
            <button className="linklike" onClick={() => set('nets', [])}>Снять все</button>
          </div>
          <ul className="allc-list">
            {[...allNets, 'Без сети'].map((nm) => (
              <li key={nm}><label className="chk">
                <input type="checkbox" checked={f.nets === null || f.nets.includes(nm)}
                  onChange={() => set('nets', toggle(f.nets ?? [...allNets, 'Без сети'], nm))} />
                <span className="nm">{nm}</span>
              </label></li>
            ))}
          </ul>
        </Drop>
        <Drop label="Оборот аптеки" value={TURNOVER_STEPS.find((x) => x.v === f.turn)?.label ?? 'любой'} active={!!f.turn}>
          {TURNOVER_STEPS.map((x) => (
            <label key={x.label} className="chk block">
              <input type="radio" name="bturn" checked={f.turn === x.v} onChange={() => set('turn', x.v)} />{x.label}
            </label>
          ))}
          <p className="muted drop-note">Выручка юрлица в месяц в расчёте на одну его аптеку (реестр аптек).</p>
        </Drop>
          </div>
        </details>


      {err && <div className="error">{err}</div>}
      {t && (
        <div className="figures big">
          <div className="fig"><div className="v">{data!.rows.length}</div><div className="l">{(isRegion ? plural(data!.rows.length, 'область', 'области', 'областей') : plural(data!.rows.length, 'город', 'города', 'городов'))}
            {f.scope !== 'city' && !f.client && <>: {isRegion ? `${n(loaded.length)} ${plural(loaded.length, 'город', 'города', 'городов')} от 50 тыс. жителей и ` : ''}{n(t.settlements ?? 0)} {plural(t.settlements ?? 0, 'населённый пункт', 'населённых пункта', 'населённых пунктов')} {isRegion ? 'вне их' : 'в пригородах'}</>}</div></div>
          {showL && <div className="fig"><div className="v">{n(t.lpu)}</div><div className="l">ЛПУ: {n(t.lpu_state)} гос., {n(t.lpu_private)} частных</div></div>}
          {showL && <div className="fig"><div className="v">{n(t.doctors)}</div><div className="l">{f.spec ? `врачей: ${f.spec.toLowerCase()}, оценка` : `врачей всех ${meta.specialties.length} специальностей, оценка`}</div></div>}
          {showP && <div className="fig"><div className="v">{n(t.pharmacies)}</div><div className="l">аптек: {n(t.ph_state)} гос., {n(t.ph_private)} частных</div></div>}
          {showP && data!.networks.length > 0 && (
            <div className="fig"><div className="v">{data!.networks[0][0]}</div><div className="l">крупнейшая сеть, {n(data!.networks[0][1])} аптек</div></div>
          )}
        </div>
      )}
      {loading && (
        <div className="loading-box">
          <div className="progress indet"><i /></div>
          <span>Считаю сводку по городам{data ? '' : ' — при первом открытии до 10 секунд'}…</span>
        </div>
      )}
      {f.sel !== null && f.sel.length === 0 && (
        <div className="empty-box">
          <h2>Города не выбраны</h2>
          <p>Выберите города в фильтре «Города» или покажите всю базу.</p>
          <button className="btn primary" style={{ width: 'auto' }} onClick={() => set('sel', null)}>Показать все города</button>
        </div>
      )}

      {data && (
        <div className="allc-body">
          <section className="allc-map">
            <div className="map-tools">
              <span>На карте</span>
              <div className="seg">
                {showL && <button aria-pressed={metric === 'lpu'} onClick={() => setMetric('lpu')}>все ЛПУ</button>}
                {showP && <button aria-pressed={metric === 'pharmacies'} onClick={() => setMetric('pharmacies')}>все аптеки</button>}
                {showL && <button aria-pressed={metric === 'doctors'} onClick={() => setMetric('doctors')}>
                  врачи</button>}
              </div>
            </div>
            <div className="mapbox base-overview-map">
              {pts ? (
                <DotsMap dots={metric === 'pharmacies' ? pts.pharmacies : pts.lpu}
                  color={metric === 'doctors' ? dotDoc : metric === 'lpu' ? dotLpu : dotPh}
                  size={metric === 'doctors' ? sizeDoc : undefined}
                  fitKey={`${f.scope}-${(f.sel ?? []).join(',')}`} describe={describePoint} onOpenCard={openPointCard} />
              ) : null}
              {ptsBusy && <div className="map-busy">Загружаю точки…</div>}
            </div>
            {metric === 'doctors' ? (
              <div className="legend base-legend">
                <span><i className="mk" style={{ background: DOC.in }} />
                  ЛПУ с врачами {f.spec ? `(${f.spec.toLowerCase()})` : 'всех специальностей'} · {n(pts?.lpu.filter((d) => d[4]).length ?? 0)},
                  врачей ≈ {n(pts?.lpu.reduce((a, d) => a + (d[4] || 0), 0) ?? 0)}</span>
                <span className="muted">Чем больше точка, тем больше врачей в учреждении (оценка). Специальность — в фильтре выше.
                  Нажмите на точку — название и число врачей.</span>
              </div>
            ) : (
              <div className="legend base-legend">
                <span><i className="mk" style={{ background: metric === 'lpu' ? DOT.lpu : DOT.ph }} />
                  {metric === 'lpu' ? 'ЛПУ' : 'аптеки'} в городах · {n((metric === 'lpu' ? pts?.lpu : pts?.pharmacies)?.filter((d) => !d[3]).length ?? 0)}</span>
                {f.scope !== 'city' && <span><i className="mk" style={{ background: metric === 'lpu' ? DOT.lpuOut : DOT.phOut }} />
                  {isRegion ? 'пригороды и остальная область' : 'пригороды'} · {n((metric === 'lpu' ? pts?.lpu : pts?.pharmacies)?.filter((d) => d[3]).length ?? 0)}</span>}
                <span className="muted">Масштаб — кнопками + и −. Город открывается из таблицы.</span>
              </div>
            )}
          </section>

          <section className="allc-tab">
            <div className="sel-bar">
              {picked.length === 0 ? (
                <span className="muted">Отметьте города галочками, чтобы создать проект, сравнить их или выгрузить только их.</span>
              ) : (
                <>
                  <b>Выбрано: {picked.length}</b>
                  <button className="btn primary" style={{ width: 'auto', marginTop: 0 }} onClick={() => onCreateProject(isRegion
                    ? loaded.filter((c) => picked.includes(`region:${c.region}`)).map((c) => c.slug) : picked, f.spec)}>Создать проект из выбранных</button>
                  <button className="btn quiet" disabled={picked.length < 2 || picked.length > 5} onClick={() => setCompare((v) => !v)}
                    title="От 2 до 5 городов">{compare ? 'Скрыть сравнение' : 'Сравнить'}</button>
                  <button className="btn quiet" disabled={!!busyXlsx || loading} onClick={() => openExport(picked, false, 'picked')}>
                    {busyXlsx === 'picked' ? 'Формирую…' : 'Выгрузить выбранные'}</button>
                  {f.spec && showL && <button className="btn quiet" disabled={!!busyXlsx || loading} onClick={() => openExport(picked, true, 'target')}
                    title={`Только ЛПУ, где есть ${f.spec.toLowerCase()}ы, и аптеки по фильтрам`}>
                    {busyXlsx === 'target' ? 'Формирую…' : 'Целевой список'}</button>}
                  <button className="linklike" onClick={() => { setPicked([]); setCompare(false) }}>снять выбор</button>
                </>
              )}
            </div>

            {compare && cmp.length >= 2 && <Compare rows={cmp} spec={f.spec} />}

            <div className="table-wrap base-table-scroll" tabIndex={0} aria-label="Таблица территорий, с прокруткой">
              <table className="grid allc-table">
                <thead>
                  <tr>
                    <th><input type="checkbox" aria-label="Выбрать все" checked={rows.length > 0 && picked.length === rows.length}
                      onChange={(e) => setPicked(e.target.checked ? rows.map((r) => r.slug!).filter(Boolean) : [])} /></th>
                    {th('city', isRegion ? 'Область' : 'Город')}{th('population', 'Жителей')}
                    {showL && <>{th('lpu', 'ЛПУ')}<th>Поликл.</th><th>Детские</th><th>Больницы</th><th>Медцентры</th>
                      {f.spec && <>{th('doctors', 'Врачей')}{th('doctors_per_10k', 'на 10 тыс.')}</>}</>}
                    {showP && <>{th('pharmacies', 'Аптеки')}{th('ph_per_10k', 'на 10 тыс.')}{th('turnover_median', 'Оборот, ₽/мес')}<th>Крупнейшие сети</th></>}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.city + r.region} className={r.slug && picked.includes(r.slug) ? 'sel' : ''}>
                      <td><input type="checkbox" aria-label={`Выбрать ${r.city}`} checked={!!r.slug && picked.includes(r.slug)}
                        onChange={() => r.slug && setPicked(toggle(picked, r.slug))} /></td>
                      <td>{isRegion ? <b>{r.city}</b> : <button className="rowlink" onClick={() => r.slug && onOpenCity(r.slug, f.spec)}><b>{r.city}</b></button>}
                        <div className="muted">{isRegion
                          ? `${r.cities_n ?? 0} ${plural(r.cities_n ?? 0, 'город', 'города', 'городов')} в базе${r.outside ? `, ${num(r.outside, 0)} вне городов` : ''}`
                          : [r.region !== r.city ? r.region : '', f.scope === 'suburbs' && r.outside ? `пригород: ${num(r.outside, 0)}` : ''].filter(Boolean).join(' · ')}</div></td>
                      <td>{r.population ? num(r.population / 1000, 0) + ' тыс.' : '—'}</td>
                      {showL && <><td>{n(r.lpu)}</td><td>{n(r.polyclinic_adult)}</td><td>{n(r.polyclinic_child)}</td><td>{n(r.hospital)}</td><td>{n(r.private_center)}</td>
                        {f.spec && <><td>{n(r.doctors)}</td><td className="muted">{r.doctors_per_10k ?? '—'}</td></>}</>}
                      {showP && <><td>{n(r.pharmacies)}</td><td className="muted">{r.ph_per_10k ?? '—'}</td><td>{mln(r.turnover_median)}</td>
                        <td className="muted">{r.top_networks.join(', ')}</td></>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
      {card && <FacilityCard load={loadCard} specialty={card.query.specialty} onClose={() => setCard(null)} />}
      {exportSettings && <BaseExportDialog query={exportSettings.query} specialties={meta.specialties}
        onClose={() => setExportSettings(null)} onDownload={(q) => download(q, exportSettings.tag)} />}
    </div>
  )
}

/** Сравнение 2–5 городов: показатели строками, лучший по строке выделен. */
function Compare({ rows, spec }: { rows: BaseRow[]; spec: string }) {
  const lines: { l: string; v: (r: BaseRow) => number | null | undefined; f?: (v: number) => string }[] = [
    { l: 'Жителей, тыс.', v: (r) => (r.population ? r.population / 1000 : null) },
    { l: 'ЛПУ', v: (r) => r.lpu },
    { l: 'из них частных', v: (r) => r.lpu_private },
    ...(spec ? [{ l: `Врачей (${spec.toLowerCase()})`, v: (r: BaseRow) => r.doctors }, { l: 'врачей на 10 тыс. жит.', v: (r: BaseRow) => r.doctors_per_10k, f: (v: number) => num(v, 1) }] : []),
    { l: 'Аптек', v: (r) => r.pharmacies },
    { l: 'аптек на 10 тыс. жит.', v: (r) => r.ph_per_10k, f: (v) => num(v, 1) },
    { l: 'Оборот аптеки, млн ₽/мес', v: (r) => (r.turnover_median ? r.turnover_median / 1e6 : null), f: (v) => num(v, 1) },
  ]
  return (
    <div className="compare">
      <table className="grid">
        <thead><tr><th />{rows.map((r) => <th key={r.city}>{r.city}</th>)}</tr></thead>
        <tbody>
          {lines.map((x) => {
            const vals = rows.map((r) => x.v(r) ?? null)
            const best = Math.max(...vals.map((v) => v ?? -1))
            return (
              <tr key={x.l}>
                <td className="muted">{x.l}</td>
                {vals.map((v, i) => <td key={i} className={v !== null && v === best ? 'best' : ''}>{v === null ? '—' : x.f ? x.f(v) : num(v, 0)}</td>)}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
