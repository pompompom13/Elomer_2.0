import { useState } from 'react'
import type { Advice, Scenario } from '../types'
import { num, plural } from '../format'

interface ChartProps {
  advice: Advice
  current: number
  specialty: string
}

interface Props extends ChartProps {
  hasExtraSpecialties: boolean
  onApply?: (change: Scenario['change']) => void
  busy: boolean
}

const STATUS = { ok: 'в норме', warn: 'можно лучше', bad: 'не оптимально' }

/** Оценка параметров проекта: что не так, почему и какие значения лучше. */
export function AdvicePanel({ advice, current, specialty, hasExtraSpecialties, onApply, busy }: Props) {
  const bad = advice.params.filter((p) => p.status !== 'ok').length
  const scopeNote = hasExtraSpecialties && (
    <p className="muted adv-scope">
      Советы по числу визитов и кратности относятся к основной специальности — {specialty}.
      {' '}Оценка длины дня учитывает также дополнительные специальности и аптеки.
    </p>
  )
  const charts = (
    <details className="adv-more">
      <summary>Графики по визитам · {specialty}</summary>
      {advice.ok && scopeNote}
      <div className="adv-charts">
        <DayChart advice={advice} current={current} specialty={specialty} />
        <PlacedChart advice={advice} current={current} specialty={specialty} />
      </div>
    </details>
  )
  if (advice.ok) return <section className="advice ok-only">{charts}</section>
  return (
    <section className="advice">
      <div className="section-head">
        <h2>Что улучшить</h2>
        <span className={`adv-verdict ${advice.ok ? 'ok' : 'bad'}`}>
          {advice.ok ? 'Параметры сбалансированы' : `Не оптимально: ${bad} из ${advice.params.length}`}
        </span>
      </div>
      {scopeNote}

      <table className="grid adv-table">
        <thead><tr><th>Параметр</th><th>Сейчас</th><th>Лучше</th><th>Почему</th></tr></thead>
        <tbody>
          {advice.params.map((p) => (
            <tr key={p.key} className={p.status}>
              <td>
                <i className={`st ${p.status}`} />
                {p.key === 'doctor_visits_per_day' ? `${specialty} · Визитов в день на медпреда`
                  : p.key === 'doctor_frequency' ? `${specialty} · ${p.label}` : p.label}
                <div className="muted">{STATUS[p.status]}</div>
              </td>
              <td className="num">{p.current}</td>
              <td className="num">{p.recommended !== p.current ? <b>{p.recommended}</b> : '—'}</td>
              <td className="why">{p.why}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {advice.scenarios.length > 0 && (
        <>
          <h3 className="adv-sub">Варианты, которые решают проблему</h3>
          <ul className="scen">
            {advice.scenarios.map((s) => (
              <li key={s.title}>
                <div>
                  <b>{scenarioTitle(s, specialty)}</b>
                  <div className="muted">{s.note}</div>
                </div>
                <div className="scen-m">
                  <span className={s.coverage < 90 ? 'low' : ''}>{specialty} · {num(s.placed, 0)} из {num(s.planned, 0)} визитов ({s.coverage}%)</span>
                  <span className={s.p80 > advice.model.limit_h ? 'low' : ''}>день {num(s.hours)} ч, до {num(s.p80)} ч (80%)</span>
                </div>
                <button className="btn" disabled={busy || !onApply} onClick={() => onApply?.(s.change)}>Применить</button>
              </li>
            ))}
          </ul>
          {!onApply && <p className="muted adv-foot">Пересчитайте план для выбранной специальности, чтобы применить совет.</p>}
          <p className="muted adv-foot">
            Оценка по модели: визит к врачу с ожиданием, регистратурой и дорогой ≈ {advice.model.per_doctor_min} мин,
            {' '}в аптеку ≈ {advice.model.per_pharmacy_min} мин, обед и отчёт {advice.model.fixed_min} мин.
            После «Применить» план пересчитывается полностью.
          </p>
        </>
      )}

      {charts}
    </section>
  )
}

function scenarioTitle(scenario: Scenario, specialty: string) {
  const visits = scenario.change.doctor_visits_per_day
  if (visits !== undefined && Object.keys(scenario.change).length === 1) {
    return `${specialty} · ${visits} ${plural(visits, 'визит', 'визита', 'визитов')} в день на медпреда`
  }
  if (scenario.change.doctor_frequency !== undefined) return `${specialty} · ${scenario.title}`
  return scenario.title
}

// ─── Графики ──────────────────────────────────────────────────────────────

const W = 460, H = 220, M = { l: 40, r: 12, t: 14, b: 34 }

function useHover() {
  const [i, setI] = useState<number | null>(null)
  return { i, set: setI }
}

function Axis({ ticks, y, fmt }: { ticks: number[]; y: (v: number) => number; fmt: (v: number) => string }) {
  return (
    <g className="ax">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} className="grid-l" />
          <text x={M.l - 6} y={y(t) + 4} textAnchor="end">{fmt(t)}</text>
        </g>
      ))}
    </g>
  )
}

function niceTicks(max: number, n = 4) {
  const raw = max / n
  const p = 10 ** Math.floor(Math.log10(raw || 1))
  const step = [1, 2, 2.5, 5, 10].map((k) => k * p).find((s) => s >= raw) ?? raw
  const out = []
  for (let v = 0; v <= max + 1e-9; v += step) out.push(+v.toFixed(6))
  return out
}

function DayChart({ advice, current, specialty }: ChartProps) {
  const c = advice.curve
  const lim = advice.model.limit_h
  const maxY = Math.max(lim * 1.25, ...c.map((x) => x.p80))
  const bw = (W - M.l - M.r) / c.length
  const x = (i: number) => M.l + i * bw
  const y = (v: number) => H - M.b - (v / maxY) * (H - M.t - M.b)
  const h = useHover()
  const best = advice.model.max_doctor_visits
  return (
    <figure className="chart">
      <figcaption>Длина рабочего дня · {specialty}</figcaption>
      <div className="chart-box">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Длина рабочего дня при разном числе визитов · ${specialty}`}>
          <Axis ticks={niceTicks(maxY)} y={y} fmt={(v) => `${v} ч`} />
          {c.map((p, i) => {
            const over = p.p80 > lim
            const cls = p.d === current ? 'cur' : p.d === best ? 'best' : over ? 'over' : 'okb'
            return (
              <g key={p.d} onMouseEnter={() => h.set(i)} onMouseLeave={() => h.set(null)}>
                <rect x={x(i)} y={M.t} width={bw} height={H - M.t - M.b} className="hit" />
                <path className={`bar ${cls}`}
                  d={roundTop(x(i) + bw * 0.2, y(p.hours), bw * 0.6, H - M.b - y(p.hours))} />
                <line x1={x(i) + bw * 0.2} x2={x(i) + bw * 0.8} y1={y(p.p80)} y2={y(p.p80)} className="p80" />
                <text x={x(i) + bw / 2} y={H - M.b + 14} textAnchor="middle"
                  className={p.d === current ? 'xl cur' : p.d === best ? 'xl best' : 'xl'}>{p.d}</text>
              </g>
            )
          })}
          <line x1={M.l} x2={W - M.r} y1={y(lim)} y2={y(lim)} className="limit" />
          <text x={M.l + 4} y={y(lim) - 5} textAnchor="start" className="lim-t">норма {lim} ч</text>
          <text x={(W + M.l) / 2} y={H - 4} textAnchor="middle" className="ax-t">визитов в день на медпреда</text>
        </svg>
        {h.i !== null && (
          <div className="tip" style={{ left: `${((x(h.i) + bw / 2) / W) * 100}%` }}>
            <b>{c[h.i].d} визитов</b><br />день {num(c[h.i].hours)} ч<br />до {num(c[h.i].p80)} ч с вер. 80%
          </div>
        )}
      </div>
      <div className="legend">
        <span><i className="lg-bar" />средний день</span>
        <span><i className="lg-p80" />день с вероятностью 80%</span>
        <span><i className="lg-cur" />сейчас</span>
        <span><i className="lg-best" />лучше, {best} в день</span>
      </div>
    </figure>
  )
}

function PlacedChart({ advice, current, specialty }: ChartProps) {
  const c = advice.curve
  const maxY = Math.max(...c.map((x) => x.planned)) * 1.05 || 1
  const step = (W - M.l - M.r) / (c.length - 1 || 1)
  const x = (i: number) => M.l + i * step
  const y = (v: number) => H - M.b - (v / maxY) * (H - M.t - M.b)
  const line = (k: 'placed' | 'planned' | 'placed_alt') => c.map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(p[k] ?? 0)}`).join('')
  const h = useHover()
  const ci = c.findIndex((p) => p.d === current)
  return (
    <figure className="chart">
      <figcaption>Визиты в маршрутах и по плану · {specialty}</figcaption>
      <div className="chart-box">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Визиты в маршрутах и по плану · ${specialty}`}>
          <Axis ticks={niceTicks(maxY)} y={y} fmt={(v) => num(v, 0)} />
          <path d={line('planned')} className="ln planned" />
          {advice.alt_frequency && <path d={line('placed_alt')} className="ln alt" />}
          <path d={line('placed')} className="ln placed" />
          {ci >= 0 && <line x1={x(ci)} x2={x(ci)} y1={M.t} y2={H - M.b} className="cur-l" />}
          {c.map((p, i) => (
            <g key={p.d}>
              <text x={x(i)} y={H - M.b + 14} textAnchor="middle" className={p.d === current ? 'xl cur' : 'xl'}>{p.d}</text>
              <rect x={x(i) - step / 2} y={M.t} width={step} height={H - M.t - M.b} className="hit"
                onMouseEnter={() => h.set(i)} onMouseLeave={() => h.set(null)} />
            </g>
          ))}
          {h.i !== null && (
            <>
              <line x1={x(h.i)} x2={x(h.i)} y1={M.t} y2={H - M.b} className="cross" />
              <circle cx={x(h.i)} cy={y(c[h.i].placed)} r={4.5} className="dot placed" />
              <circle cx={x(h.i)} cy={y(c[h.i].planned)} r={4.5} className="dot planned" />
            </>
          )}
          <text x={(W + M.l) / 2} y={H - 4} textAnchor="middle" className="ax-t">визитов в день на медпреда</text>
        </svg>
        {h.i !== null && (
          <div className="tip" style={{ left: `${(x(h.i) / W) * 100}%` }}>
            <b>{c[h.i].d} в день</b><br />в маршрутах {num(c[h.i].placed, 0)}
            {advice.alt_frequency && <><br />при кратности {advice.alt_frequency_month ? `${advice.alt_frequency_month} в месяц` : advice.alt_frequency}: {num(c[h.i].placed_alt ?? 0, 0)}</>}
            <br />по плану {num(c[h.i].planned, 0)}
          </div>
        )}
      </div>
      <div className="legend">
        <span><i className="lg-placed" />встанет в маршруты</span>
        {advice.alt_frequency && <span><i className="lg-alt" />при кратности {advice.alt_frequency_month ? `${advice.alt_frequency_month} в месяц` : advice.alt_frequency}</span>}
        <span><i className="lg-planned" />по плану</span>
      </div>
      <p className="muted chart-note">Где синяя линия ниже пунктира, врачей в зонах не хватает при текущей кратности.</p>
    </figure>
  )
}

function roundTop(x: number, y: number, w: number, h: number) {
  const r = Math.min(4, w / 2, Math.max(0, h))
  if (h <= 0) return ''
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
}
