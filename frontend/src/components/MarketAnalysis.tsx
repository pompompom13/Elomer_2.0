import { useMemo, useState, type ReactNode } from 'react'
import { api } from '../api'
import { num } from '../format'
import { PlanMap, type MapPoint } from './PlanMap'
import { NDMap, type NDCityMarker } from './NDMap'

type Coordinates = [number, number]
type Issue = { row: number; text: string }
type Status = 'matched' | 'only_base' | 'only_client' | 'review'
type Kind = 'lpu' | 'pharmacy'
type Facility = {
  id: string; name: string; address: string; kind: Kind; code: string; coordinates: Coordinates
}
type Match = {
  status: Status; name: string; address: string; kind: Kind | null; code: string
  coordinates: Coordinates | null; method?: string; base: Facility | null
}
type Comparison = {
  cities: { city: string; slug: string; points: Match[]; base_count: number; matched: number; client_count: number; review: number }[]
  issues: Issue[]
}
type DistributionRow = {
  date: string; city: string; brand: string; sku: string; coordinates: Coordinates | null
  scope: string; dsm: number; stock: number | null; delivery: number | null
  nd: number | null; preorder: number | null; note: string
}
type Distribution = { rows: DistributionRow[]; issues: Issue[] }
type Mode = 'comparison' | 'distribution'

const STATUS: Record<Status, { label: string; color: string }> = {
  matched: { label: 'Совпало', color: '#21835F' },
  only_base: { label: 'Только в ЭЛОМЕРЕ', color: '#2A52AD' },
  only_client: { label: 'Только у заказчика', color: '#9155AA' },
  review: { label: 'Требует проверки', color: '#BC6E00' },
}
const PALETTE = ['#2A52AD', '#0E8AA3', '#8A4695', '#B4542F', '#397648', '#85591F', '#A74264']
const KIND = { pharmacy: 'Аптека', lpu: 'ЛПУ' }
const count = (n: number | null) => n === null ? '—' : num(n, 0)
const percent = (n: number | null) => n === null ? '—' : `${num(n * 100, 2)}%`
const unique = (items: string[]) => [...new Set(items)].sort((a, b) => a.localeCompare(b))

function AnalysisFile<T>({ mode, filename, note, render }: {
  mode: Mode; filename: string; note: string; render: (report: T) => ReactNode
}) {
  const [report, setReport] = useState<T | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function upload(next: File) {
    setReport(null)
    setFile(null)
    setError('')
    setBusy(true)
    try {
      setReport(await api.marketAnalysis<T>(mode, next))
      setFile(next)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function download() {
    if (!file) return
    setBusy(true)
    setError('')
    try { await api.downloadMarketAnalysis(mode, file, filename) }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <>
    <p className="card-note">{note}</p>
    <div className="market-controls">
      <label className="field"><span>Файл Excel</span>
        <input type="file" accept=".xlsx" disabled={busy} onChange={e => {
          const selected = e.target.files?.[0]
          if (selected) void upload(selected)
        }} />
      </label>
      {file && <button className="btn" disabled={busy} onClick={() => void download()}>Выгрузить отчёт в Excel</button>}
    </div>
    {busy && <p role="status">Обрабатываю файл…</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {report && render(report)}
  </>
}

function Issues({ issues }: { issues: Issue[] }) {
  if (!issues.length) return null
  return <details className="secondary-panel"><summary>Проверка загрузки · {count(issues.length)}</summary>
    <ul>{issues.slice(0, 20).map((i, n) => <li key={n}>Строка {i.row}. {i.text}</li>)}</ul>
    {issues.length > 20 && <p className="hint">Полный список доступен в Excel.</p>}
  </details>
}

function SelectControl({ label, value, values, onChange, all }: {
  label: string; value: string; values: [string, string][]; onChange: (value: string) => void; all?: string
}) {
  return <label className="field"><span>{label}</span>
    <select value={value} onChange={e => onChange(e.target.value)}>
      {all && <option value="">{all}</option>}
      {values.map(([key, name]) => <option key={key} value={key}>{name}</option>)}
    </select>
  </label>
}

function ComparisonResult({ report }: { report: Comparison }) {
  const [city, setCity] = useState(report.cities[0]?.slug ?? '')
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(0)
  const selected = report.cities.find(c => c.slug === city)
  const points = (selected?.points ?? []).filter(p => !kind || p.kind === kind)
  const totals = Object.fromEntries(Object.keys(STATUS).map(key => [key, points.filter(p => p.status === key).length]))
  const base = totals.matched + totals.only_base
  const client = totals.matched + totals.only_client + totals.review
  const shown = points.filter(p => !status || p.status === status)
  const map = shown.flatMap((p, i): MapPoint[] => {
    const coords = p.base?.coordinates ?? p.coordinates
    return coords ? [{ id: `${i}`, lat: coords[0], lon: coords[1], kind: p.kind ?? p.base?.kind ?? 'lpu',
      title: `${STATUS[p.status].label} · ${p.name} · ${p.address || p.base?.address || 'адрес не указан'}`, color: STATUS[p.status].color }] : []
  })
  return <>
    {report.cities.length > 1 && <div className="table-wrap" tabIndex={0} aria-label="Сверка по городам">
      <table className="grid"><thead><tr><th>Город</th><th>Точек в базе</th><th>Совпало</th><th>Охват базы</th><th>Требует проверки</th></tr></thead>
        <tbody>{report.cities.map(c => <tr key={c.slug}>
          <td><button className="linklike" onClick={() => { setCity(c.slug); setPage(0) }}>{c.city}</button></td>
          <td>{count(c.base_count)}</td><td>{count(c.matched)}</td>
          <td>{percent(c.base_count ? c.matched / c.base_count : null)}</td><td>{count(c.review)}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    {selected && <>
      <div className="market-controls">
        <SelectControl label="Город сверки" value={city} values={report.cities.map(c => [c.slug, c.city])} onChange={v => { setCity(v); setPage(0) }} />
        <SelectControl label="Тип учреждения" value={kind} all="Все учреждения" values={[['pharmacy', 'Аптеки'], ['lpu', 'ЛПУ']]} onChange={v => { setKind(v); setPage(0) }} />
        <SelectControl label="Статус" value={status} all="Все статусы" values={Object.entries(STATUS).map(([key, info]) => [key, info.label])} onChange={v => { setStatus(v); setPage(0) }} />
      </div>
      <p className="hint">
        Охват базы {percent(base ? totals.matched / base : null)}. Распознано в списке заказчика {percent(client ? totals.matched / client : null)}.
      </p>
      <div className="market-legend">{Object.entries(STATUS).map(([key, val]) => <span key={key}>
        <i style={{ background: val.color }} />{val.label} · {count(totals[key])}
      </span>)}</div>
      {map.length > 0 && <div className="mapbox market-map"><PlanMap center={[map[0].lat, map[0].lon]} points={map}
        fitKey={`${city}-${kind}-${status}`} wheelZoom={false} /></div>}
      <div className="table-wrap comparison-table" tabIndex={0} aria-label="Адреса сверки"><table className="grid">
        <thead><tr><th>Статус</th><th>Точка и адрес</th><th>Тип</th><th>Код ДСМ</th><th>Основание</th><th>Адрес ЭЛОМЕРА</th></tr></thead>
        <tbody>{shown.slice(page * 30, (page + 1) * 30).map((p, i) => <tr key={i}>
          <td>{STATUS[p.status].label}</td><td><b>{p.name}</b><br />{p.address}</td>
          <td>{p.kind ? KIND[p.kind] : '—'}</td><td>{p.code || p.base?.code || '—'}</td>
          <td>{p.method || '—'}</td><td>{p.base?.address || '—'}</td>
        </tr>)}</tbody>
      </table></div>
      {shown.length === 0 && <p className="hint">По выбранным фильтрам точек нет.</p>}
      {shown.length > 30 && <div className="market-controls">
        <button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>Назад</button>
        <span>{count(page * 30 + 1)}–{count(Math.min((page + 1) * 30, shown.length))} из {count(shown.length)}</span>
        <button className="btn" disabled={(page + 1) * 30 >= shown.length} onClick={() => setPage(page + 1)}>Дальше</button>
      </div>}
    </>}
    <Issues issues={report.issues} />
  </>
}

const ND_SCALE = [
  ['0–<25%', '#A6C5EF'], ['25–<50%', '#658FCF'], ['50–<75%', '#3866AC'], ['75–100%', '#183A78'],
]
const NO_DATA_COLOR = '#8794A8'
const positionKey = (r: DistributionRow) => JSON.stringify([r.brand, r.sku])
const ndValue = (value: number | null) => value === null ? 'Нет данных' : percent(value)
const ndCount = (row: DistributionRow) => row.nd === null ? '' : `${count(row.stock)} из ${count(row.dsm)} аптек`
const compareNd = (a: DistributionRow, b: DistributionRow) =>
  (b.nd ?? -1) - (a.nd ?? -1) || a.city.localeCompare(b.city) || a.sku.localeCompare(b.sku)

function rankingColor(nd: number | null, min: number | null, max: number | null) {
  if (nd === null || min === null || max === null || min === max) return undefined
  const relative = (nd - min) / (max - min)
  if (relative < 1 / 3) return '#A9332A'
  if (relative < 2 / 3) return '#925900'
  return '#217252'
}

function DistributionResult({ report }: { report: Distribution }) {
  const dates = unique(report.rows.map(r => r.date)).reverse()
  const [date, setDate] = useState(dates[0])
  const [city, setCity] = useState('')
  const [brand, setBrand] = useState('')
  const [sku, setSku] = useState('')
  const [mode, setMode] = useState<'position' | 'leaders'>('leaders')
  const [selectedCity, setSelectedCity] = useState('')
  const isPosition = mode === 'position'
  const current = useMemo(() => report.rows.filter(r => r.date === date), [report, date])
  const cities = unique(current.map(r => r.city))
  const brands = unique(current.map(r => r.brand))
  const positions = [...new Map(current.filter(r => !brand || r.brand === brand)
    .map(r => [positionKey(r), r.sku] as const)).entries()].sort((a, b) => a[1].localeCompare(b[1]))
  const activeSku = isPosition ? (positions.some(([key]) => key === sku) ? sku : positions[0]?.[0]) : ''

  const { ranked, leaders, groups, legend, map } = useMemo(() => {
    const colors = new Map(unique(current.map(r => r.brand)).map((b, i) => [b, PALETTE[i % PALETTE.length]]))
    const territories = new Map<string, DistributionRow>()
    const groups = new Map<string, DistributionRow[]>()
    const ranked = current.filter(r => (!city || r.city === city) && (!brand || r.brand === brand)
      && (!activeSku || positionKey(r) === activeSku)).sort(compareNd)
    for (const row of current) if (!city || row.city === city) territories.set(row.city, row)
    for (const row of ranked) {
      const group = groups.get(row.city) ?? []
      group.push(row)
      groups.set(row.city, group)
    }
    const leaders = [...territories.values()].map(row => groups.get(row.city)?.[0] ??
      { ...row, brand: '', sku: '', nd: null, stock: null }).sort(compareNd)
    const map = leaders.flatMap((row): NDCityMarker[] => row.coordinates ? [{
      city: row.city, coordinates: row.coordinates, nd: row.nd, value: ndValue(row.nd),
      color: row.nd === null ? NO_DATA_COLOR : isPosition
        ? ND_SCALE[Math.min(3, Math.floor(row.nd * 4))][1] : colors.get(row.brand) ?? PALETTE[0],
    }] : [])
    const mappedCities = new Set(map.map(p => p.city))
    const legend = isPosition ? ND_SCALE : unique(leaders.filter(r => r.nd !== null && mappedCities.has(r.city))
      .map(r => r.brand)).map(b => [b, colors.get(b)!])
    return { ranked, leaders, groups, legend, map }
  }, [current, city, brand, activeSku, isPosition])

  const selectedRows = groups.get(selectedCity)?.filter(r => r.nd !== null).slice(0, isPosition ? 1 : 3) ?? []
  const visible = selectedCity ? groups.get(selectedCity) ?? [] : ranked
  const measured = leaders.filter(row => row.nd !== null)
  const highestNd = measured[0]?.nd ?? null
  const lowestNd = measured[measured.length - 1]?.nd ?? null
  return <>
    <div className="market-controls nd-modes" role="group" aria-label="Режим карты">
      <button type="button" className="btn" aria-pressed={isPosition} onClick={() => { setMode('position'); setSelectedCity('') }}>Сравнить одну позицию</button>
      <button type="button" className="btn" aria-pressed={!isPosition} onClick={() => { setMode('leaders'); setSelectedCity('') }}>Лидеры по городам</button>
    </div>
    <div className="market-controls">
      <SelectControl label="Дата среза" value={date} values={dates.map(d => [d, d.split('-').reverse().join('.')])}
        onChange={v => { setDate(v); setCity(''); setBrand(''); setSku(''); setSelectedCity('') }} />
      <SelectControl label="География" value={city} all="Все территории" values={cities.map(c => [c, c])}
        onChange={v => { setCity(v); setSelectedCity('') }} />
      <SelectControl label="Бренд" value={brand} all="Все бренды" values={brands.map(b => [b, b])}
        onChange={v => { setBrand(v); setSku(''); setSelectedCity('') }} />
      {isPosition && <SelectControl label="Позиция" value={activeSku ?? ''} values={positions}
        onChange={v => { setSku(v); setSelectedCity('') }} />}
    </div>
    <p className="hint">{isPosition
      ? 'Цвет на карте показывает долю аптек с наличием по выбранной позиции.'
      : 'Цвет на карте показывает бренд позиции с наибольшей ND. Выберите город, чтобы увидеть три позиции с наибольшим наличием в процентах.'}</p>
    {map.length > 0 && <div className="market-legend" aria-label="Легенда карты">
      {legend.map(([name, color]) => <span key={name}><i style={{ background: color }} />{name}</span>)}
      <span><i style={{ background: NO_DATA_COLOR }} />Нет данных</span>
    </div>}
    <div className="nd-overview">
      {map.length > 0 && <NDMap points={map} selected={selectedCity} onSelect={setSelectedCity} />}
      <aside className="nd-ranking" aria-label="Рейтинг территорий">
        <h3>Рейтинг территорий</h3>
        {highestNd !== lowestNd && <p className="hint nd-rank-note">Цвет отражает уровень ND относительно других территорий в выборке.</p>}
        <ul>{leaders.map(row => {
          const color = rankingColor(row.nd, lowestNd, highestNd)
          return <li key={row.city}>
            <button type="button" aria-pressed={selectedCity === row.city} onClick={() => setSelectedCity(row.city)}>
              <span className="nd-rank-title"><b>{row.city}</b><strong style={{ color }}>{ndValue(row.nd)}</strong></span>
              {row.nd !== null && <span className="nd-rank-track" aria-hidden="true"><span style={{
                width: `${row.nd * 100}%`, backgroundColor: color,
              }} /></span>}
              {!isPosition && row.nd !== null && <span>{row.sku}</span>}
              {row.nd !== null && <span className="hint">{ndCount(row)}</span>}
            </button>
          </li>
        })}</ul>
      </aside>
    </div>
    {leaders.some(r => !r.coordinates) && <p className="hint">Регионы, группы городов и территории без координат показаны в рейтинге и таблице.</p>}
    {selectedCity && <section className="nd-selection" aria-label={`Показатели ND — ${selectedCity}`}>
      <div className="nd-selection-head"><h3>{selectedCity}</h3>
        <button type="button" className="linklike" onClick={() => setSelectedCity('')}>Сбросить выбор территории</button></div>
      {selectedRows.length > 0 ? <ol>{selectedRows.map(row => <li key={positionKey(row)}>
        <strong>{percent(row.nd)}</strong> · {row.sku} <span className="hint">({ndCount(row)})</span>
      </li>)}</ol> : <p className="hint">Нет данных для расчёта ND.</p>}
    </section>}
    <div className="table-wrap market-table" tabIndex={0} aria-label="Нумерическая дистрибуция по территориям">
      <table className="grid"><thead><tr><th>Территория</th><th>Позиция</th><th>Аптек ДСМ</th><th>В наличии</th><th>ND</th><th>На доставку</th><th>Предзаказ</th><th>Замечание</th></tr></thead>
        <tbody>{visible.map(r => <tr key={JSON.stringify([r.date, r.city, r.brand, r.sku])}>
          <td>{r.city}</td><td><b>{r.brand}</b><br />{r.sku}</td><td>{count(r.dsm || null)}</td><td>{count(r.stock)}</td>
          <td><b>{ndValue(r.nd)}</b></td><td>{count(r.delivery)}</td><td>{count(r.preorder)}</td><td>{r.note || '—'}</td>
        </tr>)}</tbody>
      </table>
    </div>
    {visible.length === 0 && <p className="hint">По выбранным фильтрам позиций нет.</p>}
    <Issues issues={report.issues} />
  </>
}
export function MarketAnalysis() {
  return <div className="market-analysis">
    <details className="card"><summary>Сверка адресов заказчика</summary>
      <AnalysisFile<Comparison> mode="comparison" filename="Сверка_адресов.xlsx"
        note="Загрузите список аптек и ЛПУ. Для сопоставления подходят код ДСМ, координаты или адрес CRM. Список проверяется в пределах указанного города."
        render={result => <ComparisonResult report={result} />} />
    </details>
    <details className="card"><summary>Нумерическая дистрибуция</summary>
      <AnalysisFile<Distribution> mode="distribution" filename="Нумерическая_дистрибуция.xlsx"
        note="Загрузите стандартную таблицу Ютеки. ND — число аптек с фактическим наличием по Ютеке, делённое на число аптек ДСМ в той же географии."
        render={result => <DistributionResult report={result} />} />
    </details>
  </div>
}
