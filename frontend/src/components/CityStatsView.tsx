import { useEffect, useState, type ReactNode } from 'react'
import { api } from '../api'
import type { CareFilter, CityStats, Meta } from '../types'
import { num, people } from '../format'

interface Props {
  slug: string
  meta: Meta
  specialty: string // выбранная специальность — выделяется в диаграмме врачей
  care: CareFilter
  client?: string | null
  onBack: () => void
  actions?: ReactNode
}

const n = (v: number | null | undefined) => (v == null ? '—' : num(v, 0))
const CAT_ORDER = ['polyclinic_adult', 'polyclinic_child', 'hospital', 'private_center', 'cardio_center', 'womens', 'dental']
const SHORT: Record<string, string> = {
  polyclinic_adult: 'Поликлиники', polyclinic_child: 'Детские', hospital: 'Больницы', private_center: 'Медцентры',
  cardio_center: 'Профильные центры', womens: 'Женские консультации', dental: 'Стоматологии',
}

/** Статистика одного города: ряд ключевых чисел и три карточки одинаковой ширины. */
export function CityStatsView({ slug, meta, specialty, care, client, onBack, actions }: Props) {
  const [s, setS] = useState<CityStats | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    setS(null)
    setErr(null)
    api.cityStats(slug, care, client).then((value) => { if (active) setS(value) }).catch((e) => { if (active) setErr(e.message) })
    return () => { active = false }
  }, [slug, care, client])

  if (err) return <div className="error">{err}</div>
  if (!s) return <div className="loading">Считаю статистику…</div>

  const L = s.lpu, P = s.pharmacies
  const docTotal = s.doctors.reduce((a, d) => a + d.n, 0)
  const confirmed = s.doctors.reduce((a, d) => a + d.confirmed, 0)
  const sel = s.doctors.find((d) => d.spec === specialty)

  return (
    <div className="cstats">
      <div className="cstats-head">
        <button className="linklike crumb" onClick={onBack}>Все города</button><span className="crumb-sep">/</span>
        <h2>{s.name}</h2>
        <span className="muted">{[s.region !== s.name ? s.region : '', s.population ? people(s.population) : ''].filter(Boolean).join(' · ')}</span>
        {actions && <div className="cstats-act">{actions}</div>}
      </div>

      <div className={`kpis${specialty ? "" : " kpis5"}`}>
        <Kpi v={n(L.total)} l="ЛПУ" sub={s.lpu_per_100k ? `${num(s.lpu_per_100k)} на 100 тыс. жит.` : ''} />
        <Kpi v={n(P.total)} l="аптек" sub={P.per_10k ? `${num(P.per_10k)} на 10 тыс. жит.` : ''} />
        {specialty && <Kpi v={sel ? n(sel.n) : '—'} l={`${specialty.toLowerCase()}ов`} sub={sel?.confirmed ? `${n(sel.confirmed)} подтверждено визитами` : 'в городе'} />}
        <Kpi v={n(docTotal)} l="врачей всего" sub={`${meta.specialties.length} специальностей`} />
        <Kpi v={n(P.network_count)} l="аптечных сетей" sub={P.networks[0] ? `крупнейшая — ${P.networks[0].name}` : ''} />
        <Kpi v={P.turnover_median ? `${num(P.turnover_median / 1e6, 1)} млн ₽` : '—'} l="оборот аптеки в месяц" sub="медиана по юрлицам" />
      </div>

      <div className="cards3">
        <section className="card">
          <h3>ЛПУ по типам</h3>
          <Bars color="var(--doc)" rows={CAT_ORDER.filter((k) => L.by_category[k]).map((k) => ({ label: SHORT[k], n: L.by_category[k] }))} />
          <Split a={{ label: 'гос.', n: L.state }} b={{ label: 'частные', n: L.private }} color="var(--doc)" />
          <p className="card-note">
            {L.sources.field ? `${n(L.sources.field)} добавлено по данным визитов. ` : ''}
            {L.confirmed ? `В ${n(L.confirmed)} ЛПУ врачи подтверждены визитами.` : 'Данных визитов по городу нет.'}
          </p>
        </section>

        <section className="card">
          <h3>Аптечные сети</h3>
          <Bars color="var(--ph)" rows={[
            ...P.networks.slice(0, 8).map((x) => ({ label: x.name, n: x.n })),
            ...(P.other_networks + P.networks.slice(8).reduce((a, x) => a + x.n, 0)
              ? [{ label: `Другие (${P.network_count - Math.min(8, P.networks.length)})`, n: P.other_networks + P.networks.slice(8).reduce((a, x) => a + x.n, 0), muted: true }] : []),
            ...(P.singles ? [{ label: 'Без сети', n: P.singles, muted: true }] : []),
          ]} />
          <Split a={{ label: 'гос.', n: P.state }} b={{ label: 'частные', n: P.private }} color="var(--ph)" />
          <p className="card-note">
            Из реестра добавлено {n(P.sources.registry_only)}, совпало с картой {n(P.sources.map_and_registry)}.
            {P.sources.not_in_registry ? ` ${n(P.sources.not_in_registry)} аптек карты нет в реестре — не учитываются.` : ''}
          </p>
        </section>

        <section className="card">
          <h3>Врачи по специальностям</h3>
          <Bars color="var(--doc)" highlight={specialty}
            rows={s.doctors.map((d) => ({ label: d.spec, n: d.n, extra: d.confirmed }))} />
          <p className="card-note">
            {care === 'all' ? 'Оценка числа врачей в городе.' : 'Оценка числа врачей в выбранном звене.'}{confirmed ? ` Тёмная часть — подтверждено визитами (${n(confirmed)}).` : ''}
          </p>
        </section>
      </div>
    </div>
  )
}

function Kpi({ v, l, sub }: { v: string; l: string; sub?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-v">{v}</div>
      <div className="kpi-l">{l}</div>
      {sub && <div className="kpi-s">{sub}</div>}
    </div>
  )
}

type Row = { label: string; n: number; muted?: boolean; extra?: number; note?: string }

/** Горизонтальные полосы: одна шкала на список, значение справа. */
function Bars({ rows, color, highlight }: { rows: Row[]; color: string; highlight?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.n))
  return (
    <ul className="hbars">
      {rows.map((r) => (
        <li key={r.label} className={r.label === highlight ? 'hl' : ''}
          title={`${r.label}: ${num(r.n, 0)}${r.extra ? `, подтверждено визитами: ${num(r.extra, 0)}` : ''}${r.note ? ` (${r.note})` : ''}`}>
          <span className="hb-l">{r.label}</span>
          <span className="hb-t">
            <i style={{ width: `${(r.n / max) * 100}%`, background: r.muted ? 'var(--travel)' : color, opacity: r.extra ? 0.45 : 1 }} />
            {!!r.extra && <i style={{ width: `${(Math.min(r.extra, r.n) / max) * 100}%`, background: color }} />}
          </span>
          <span className="hb-v">{num(r.n, 0)}</span>
        </li>
      ))}
    </ul>
  )
}

/** Доля двух частей одной полосой. */
function Split({ a, b, color }: { a: { label: string; n: number }; b: { label: string; n: number }; color: string }) {
  const t = a.n + b.n || 1
  return (
    <div className="split">
      <div className="split-bar">
        <i style={{ width: `${(a.n / t) * 100}%`, background: color }} />
        <i style={{ width: `${(b.n / t) * 100}%`, background: color, opacity: 0.35 }} />
      </div>
      <div className="split-l"><span>{a.label} {num(a.n, 0)} · {Math.round((a.n / t) * 100)}%</span><span>{b.label} {num(b.n, 0)}</span></div>
    </div>
  )
}
