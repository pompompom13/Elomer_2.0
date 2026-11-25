import { NumInput } from './NumInput'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '../api'
import { CareLevelField } from './CareLevelField'
import type { CityItem, CityResult, JobStatus, Meta, Ownership, Project, ProjectItem, ProjectResult, ProjectStatus, Transport } from '../types'
import { ALL_CATEGORIES, TURNOVER_STEPS } from './FiltersPanel'
import { PROJECT_STATUS } from './Home'
import { CityPicker } from './CityPicker'
import { FrequencyPolicy, ProjectSpecialties, specialtyRows } from './ProjectSpecialties'
import { num, plural } from '../format'

interface Props {
  meta: Meta
  cities: CityItem[]
  onCitiesChanged: () => void
  onOpenPlan: (pr: Project, slug: string, reps: number) => void
  initialProjectId: string | null
  onSelectProject: (id: string | null) => void
  backSlot?: ReactNode
}

const STATUS = { ok: 'Условия выполнены', warn: 'Есть замечания', bad: 'Есть ограничения' }
const mp = (n: number) => `${n} ${plural(n, 'медпред', 'медпреда', 'медпредов')}`
const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
const intOrNull = (v: string) => (v === '' ? null : Math.max(1, parseInt(v, 10) || 1))

const DEFAULT_NAME = 'Новый проект'
/** Название по заказчику: «Гриндекс — кардиолог, терапевт». Без заказчика — «Новый проект». */
function autoName(client: string, specs: string[]): string {
  const c = client.trim()
  return c ? `${c} — ${specs.filter(Boolean).map((s) => s.toLowerCase()).join(', ')}` : DEFAULT_NAME
}
const projectSpecs = (p: Project) => [p.conditions.specialty, ...(p.conditions.extra_specialties ?? []).map((x) => x.specialty)]

/** Проекты: заказчик, несколько городов, общие условия; расчёт, Excel и PDF для заказчика. */
export function ProjectsView({ meta, cities, onCitiesChanged, onOpenPlan, initialProjectId, onSelectProject, backSlot }: Props) {
  const [list, setList] = useState<ProjectItem[]>([])
  const [pr, setPr] = useState<Project | null>(null)
  const [section, setSection] = useState<'conditions' | 'result'>('conditions')
  const [job, setJob] = useState<JobStatus | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [dl, setDl] = useState<'xlsx' | 'pdf' | null>(null)

  const refreshList = useCallback(() => api.projects().then(setList).catch((e) => setErr(e.message)), [])
  useEffect(() => {
    refreshList().then(() => undefined)
  }, [refreshList])
  useEffect(() => {
    if (initialProjectId) open(initialProjectId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function open(id: string) {
    if (!(await flush())) return
    setErr(null)
    api.project(id).then(async (p) => {
      // старые проекты «Новый проект» с заказчиком получают название по заказчику
      if (p.name === DEFAULT_NAME && p.client.trim()) { p = { ...p, name: autoName(p.client, projectSpecs(p)) }; await api.saveProject(p.id, { name: p.name }).catch(() => undefined); refreshList() }
      setPr(p); setSection(p.result ? 'result' : 'conditions'); setJob(p.job ?? null); onSelectProject(id) })
      .catch(() => { onSelectProject(null); refreshList() })
  }

  async function create() {
    if (!(await flush())) return
    setSection('conditions')
    setJob(null)
    const p = await api.createProject({ name: DEFAULT_NAME })
    await refreshList()
    setPr(p)
    onSelectProject(p.id)
  }

  async function remove(id = pr?.id, name = pr?.name) {
    if (!id || !window.confirm(`Удалить проект «${name}»? Расчёт и планы по дням будут удалены.`)) return
    await api.deleteProject(id)
    if (pr?.id === id) {
      onSelectProject(null)
      setPr(null)
    }
    refreshList()
  }

  // автосохранение правок через полсекунды после последнего изменения
  const timer = useRef<number | undefined>(undefined)
  const pending = useRef<Project | null>(null)
  const saveTask = useRef<Promise<boolean>>(Promise.resolve(true))
  const flush = useCallback(() => {
    window.clearTimeout(timer.current)
    const p = pending.current
    if (!p) return saveTask.current
    pending.current = null
    const save = async () => {
      setSaving(true)
      try {
        const saved = await api.saveProject(p.id, { name: p.name, client: p.client, conditions: p.conditions, goal: p.goal, cities: p.cities, status: p.status })
        setPr((cur) => cur?.id === saved.id ? {
          ...cur, result: cur === p ? saved.result : cur.result, updated: saved.updated,
        } : cur)
        refreshList()
        return true
      } catch (e) {
        pending.current ??= p
        setErr((e as Error).message)
        return false
      } finally { setSaving(false) }
    }
    saveTask.current = saveTask.current.then(save)
    return saveTask.current
  }, [refreshList])
  function edit(next: Project) {
    // название ведётся по заказчику, пока его не переименовали вручную
    if (pr && next.name === pr.name && (pr.name === DEFAULT_NAME || pr.name === autoName(pr.client, projectSpecs(pr)))) {
      next = { ...next, name: autoName(next.client, projectSpecs(next)) }
    }
    if (pr?.result && next.result &&
      (next.conditions !== pr.conditions || next.cities !== pr.cities || next.goal !== pr.goal)) {
      next = { ...next, result: { ...next.result, stale: true } }
    }
    setPr(next)
    pending.current = next
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(flush, 500)
  }

  // расчёт в фоне: опрашиваем ход, по готовности перечитываем проект
  async function compute() {
    if (!pr) return
    setErr(null)
    if (!(await flush())) return
    setSection('result')
    try {
      setJob(await api.computeProject(pr.id))
    } catch (e) { setErr((e as Error).message) }
  }
  useEffect(() => {
    if (!pr || !job?.running) return
    const t = window.setTimeout(async () => {
      const j = await api.projectJob(pr.id)
      setJob(j)
      if (!j.running) {
        if (j.error) setErr(j.error)
        const p = await api.project(pr.id)
        setPr(p)
        refreshList()
        onCitiesChanged()
      }
    }, 900)
    return () => window.clearTimeout(t)
  }, [job, pr, refreshList, onCitiesChanged])

  async function download(kind: 'xlsx' | 'pdf') {
    if (!pr?.result?.id) return
    setDl(kind)
    try { await api.downloadProject(pr.id, kind, pr.result.id) } catch (e) { setErr((e as Error).message) } finally { setDl(null) }
  }

  const c = pr?.conditions
  const setCond = <K extends keyof NonNullable<typeof c>>(k: K, v: NonNullable<typeof c>[K]) =>
    pr && edit({ ...pr, conditions: { ...pr.conditions, [k]: v } })
  const res = pr?.result ?? null
  const specCount = c ? specialtyRows(c).filter((x) => x.doctor_visits_per_day > 0).length : 0
  const useMixed = specCount > 1
  const running = !!job?.running

  return (
    <div className="workspace">
      <aside className="side projects-side">
        <button className="btn primary" style={{ marginTop: 0 }} onClick={create}>Новый проект</button>
        <ul className="proj-list">
          {list.map((p) => (
            <li key={p.id}>
              <button aria-current={pr?.id === p.id ? 'true' : undefined} onClick={() => open(p.id)}>
                <b>{p.name === DEFAULT_NAME && p.client ? autoName(p.client, [p.specialty]) : p.name}</b>
                <small>{[p.client, `${p.cities} ${plural(p.cities, 'город', 'города', 'городов')}`,
                  (p.specialties ?? [p.specialty]).join(', ').toLowerCase()].filter(Boolean).join(' · ')}</small>
                <small>{p.computed ? `${p.reps ?? '—'} МП · ` : 'не рассчитан · '}{PROJECT_STATUS[p.status ?? 'draft']}</small>
              </button>
              <button className="proj-del" title="Удалить проект" aria-label={`Удалить проект ${p.name}`}
                onClick={() => remove(p.id, p.name)}>
                <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5M7 7v4M9 7v4"
                  fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </button>
            </li>
          ))}
          {list.length === 0 && <li className="muted">Проектов пока нет.</li>}
        </ul>
      </aside>

      <main className="main">
        {backSlot}
        {!pr || !c ? (
          <div className="empty">
            <h1>Проекты для заказчиков</h1>
            <p>Проект собирает в одном месте заказчика, города и общие условия: специальность, визиты в день,
              кратность и срок. ЭЛОМЕР подберёт число медпредов под цель по врачам в каждом городе, построит
              планы по дням и покажет, что при заданных условиях не получается.</p>
            <p>Результат выгружается в Excel для команды (расписание, базы, статистика по месяцам) и в PDF для заказчика.</p>
          </div>
        ) : (
          <div className="project">
            <div className="proj-head">
              <input className="proj-name" aria-label="Название проекта" value={pr.name}
                onChange={(e) => edit({ ...pr, name: e.target.value })} />
              <select className="proj-status" aria-label="Статус проекта" value={pr.status ?? 'draft'}
                onChange={(e) => edit({ ...pr, status: e.target.value as ProjectStatus })}>
                {Object.entries(PROJECT_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <span className="muted">{saving ? 'Сохраняю…' : `Сохранено ${pr.updated}`}</span>
              <div className="spacer" />
              <button className="btn primary project-compute" disabled={running || pr.cities.length === 0}
                onClick={compute}>{running ? 'Считаю…' : res ? 'Пересчитать проект' : 'Рассчитать проект'}</button>
            </div>
            <div className="seg plan-views" role="group" aria-label="Раздел проекта">
              <button type="button" aria-pressed={section === 'conditions'} onClick={() => setSection('conditions')}>Условия</button>
              <button type="button" aria-pressed={section === 'result'} onClick={() => setSection('result')}>Результат</button>
            </div>
            {err && <div className="error" role="alert">{err}</div>}
            {running && job && <div className="proj-progress" role="status">
              <div className="progress"><i style={{ width: ((job.done / Math.max(1, job.total)) * 100) + '%' }} /></div>
              <span className="muted">{job.current || 'Готовлю расчёт'} · {Math.floor(job.done)} из {job.total}</span>
            </div>}
            {section === 'conditions' && <>
            <div className="step"><span className="step-n">1</span><h2>Заказчик и специальности</h2>
              <span className="spacer" />
              <button className="linklike" onClick={() => {
                if (!window.confirm('Вернуть условия проекта по умолчанию (специальность, визиты, кратность, срок, учреждения, цель)? Заказчик, название и города останутся.')) return
                edit({ ...pr, goal: { mode: 'none', total_doctors: null }, conditions: {
                  ...pr.conditions, specialty: 'Кардиолог', doctor_visits_per_day: 5, pharmacy_visits_per_day: 3, doctor_frequency: 1,
                  pharmacy_frequency: 1, frequency_unit: 'project_month', months: 12, transport: 'car', workday_limit_h: 8, coverage: 'spread',
                  lpu_ownership: ['state', 'private'], pharmacy_ownership: ['state', 'private'], categories: null, lpu_care: 'all',
                  extra_specialties: [], pharmacy_min_turnover: null } })
              }}>Сбросить условия</button>
            </div>
            <div className="proj-form">
              <section>
                <h3>Заказчик</h3>
                <input type="text" placeholder="Название компании" value={pr.client}
                  onChange={(e) => edit({ ...pr, client: e.target.value })} />
              </section>

              <section>
                <h3>Визиты одного медпреда</h3>
                <FrequencyPolicy value={c.frequency_unit} onChange={(v) => setCond('frequency_unit', v)} />
                <ProjectSpecialties options={meta.specialties} settings={c} frequencyUnit={c.frequency_unit}
                  onChange={(settings) => edit({ ...pr, conditions: { ...c, ...settings } })} />
                <div className="visit-row">
                  <span className="swatch" style={{ background: 'var(--ph)' }} /><span>Аптеки</span>
                  <NumInput aria-label="Визитов в аптеки в день" min={0} max={20} value={c.pharmacy_visits_per_day}
                    onChange={(v) => setCond('pharmacy_visits_per_day', v ?? 0)} />
                  <NumInput aria-label="Кратность визитов в аптеки" min={1} max={12} value={c.pharmacy_frequency}
                    onChange={(v) => setCond('pharmacy_frequency', v ?? 1)} />
                </div>
                <div className="pair" style={{ marginTop: 8 }}>
                  <div className="field"><label htmlFor="pj-tr">Транспорт</label>
                    <select id="pj-tr" value={c.transport} onChange={(e) => setCond('transport', e.target.value as Transport)}>
                      {Object.entries(meta.transport).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                    </select></div>
                  <div className="field"><label htmlFor="pj-lim">Норма дня, ч</label>
                    <NumInput id="pj-lim" decimal step={0.5} min={4} max={12} value={c.workday_limit_h}
                      onChange={(v) => setCond('workday_limit_h', v ?? 8)} /></div>
                </div>
              </section>

            </div>

            <div className="step"><span className="step-n">2</span><h2>Города</h2>
              <span className="muted">{pr.goal.mode === 'none' ? 'Медпредов: пустое поле — 1 медпред.' : 'Медпредов: пустое поле — подобрать под цель.'}</span></div>
            <div className="proj-cities">
              <div className="proj-table"><table className="grid">
                <thead>
                  <tr>
                    <th>Город</th><th>Медпредов</th>{pr.goal.mode === 'per_city' && <th>Цель, врачей всего</th>}
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {pr.cities.map((pc, i) => {
                    const ci = cities.find((x) => x.slug === pc.slug)
                    const setCity = (patch: Partial<typeof pc>) =>
                      edit({ ...pr, cities: pr.cities.map((x, j) => (j === i ? { ...x, ...patch } : x)) })
                    return (
                      <tr key={pc.slug}>
                        <td><b>{ci?.name ?? pc.slug}</b>{ci && !ci.loaded && <div className="muted">База загрузится при расчёте</div>}</td>
                        <td><input aria-label={'Медпредов: ' + (ci?.name ?? pc.slug)} type="number" min={1} max={80}
                          placeholder={pr.goal.mode === 'none' ? '1' : 'авто'}
                          value={pc.reps ?? ''} onChange={(e) => setCity({ reps: e.target.value === '' ? null : Math.min(80, intOrNull(e.target.value) ?? 1) })} /></td>
                        {pr.goal.mode === 'per_city' && <td><input aria-label={'Цель: ' + (ci?.name ?? pc.slug)} type="number"
                          min={1} placeholder="все" value={pc.target ?? ''} onChange={(e) => setCity({ target: intOrNull(e.target.value) })} /></td>}
                        <td className="proj-acts"><button className="linklike muted-l"
                          onClick={() => edit({ ...pr, cities: pr.cities.filter((_, j) => j !== i) })}>Убрать</button></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table></div>
              <div className="proj-add">
                <CityPicker cities={cities.filter((x) => !pr.cities.some((pc) => pc.slug === x.slug))} value=""
                  onChange={(slug) => slug && edit({ ...pr, cities: [...pr.cities, { slug, reps: null }] })} />
              </div>
            </div>

            <div className="step"><span className="step-n">3</span><h2>Цель и срок</h2></div>
            <div className="proj-form">
              <section>
                <h3>Срок</h3>
                <div className="pair">
                  <div className="field"><label htmlFor="pj-sd">Начало</label>
                    <input id="pj-sd" type="date" value={c.start_date} onChange={(e) => setCond('start_date', e.target.value)} /></div>
                  <div className="field"><label htmlFor="pj-mo">Месяцев</label>
                    <NumInput id="pj-mo" min={1} max={12} value={c.months}
                      onChange={(v) => setCond('months', v ?? 1)} /></div>
                </div>
                <h3>Цель по охвату врачей</h3>
                {useMixed && <p className="muted proj-goal-hint">Цель общая для выбранных специальностей. Она делится по дневной нагрузке,
                  кратности и доступной базе. Разбивка появится после расчёта.</p>}
                {([['none', 'Нет цели по охвату — считать по медпредам и визитам в день'], ['all', 'Все врачи выбранных специальностей в городах'],
                  ['total_min', 'Общая цель по проекту'], ['per_city', 'Общая цель по каждому городу']] as const).map(([k, label]) => (
                  <label key={k} className="chk block">
                    <input type="radio" name="goal" checked={pr.goal.mode === k}
                      onChange={() => edit({ ...pr, goal: { ...pr.goal, mode: k } })} />{label}
                  </label>
                ))}
                {pr.goal.mode === 'total_min' && (
                  <div className="field" style={{ marginTop: 6 }}>
                    <label htmlFor="pj-total">Врачей охватить</label>
                    <input id="pj-total" type="number" min={1} value={pr.goal.total_doctors ?? ''}
                      onChange={(e) => edit({ ...pr, goal: { ...pr.goal, total_doctors: intOrNull(e.target.value) } })} />
                    <span className="hint">{useMixed
                      ? 'Цель распределяется между городами с учётом базы и расчётной ёмкости команды.'
                      : 'Цель распределяется между городами с учётом базы и нагрузки одного медпреда.'}</span>
                  </div>
                )}
                {pr.goal.mode === 'none' && (
                  <p className="hint" style={{ margin: '6px 0 0' }}>
                    Команда не подбирается: в каждом городе столько медпредов, сколько указано в таблице городов,
                    визитов в день — как в условиях выше. ЭЛОМЕР покажет, скольких врачей и аптек это охватит.
                  </p>
                )}
              </section>
              <section>
                <h3>Учреждения</h3>
                <OwnRow label="ЛПУ" value={c.lpu_ownership} onChange={(v) => setCond('lpu_ownership', v)} />
                <CareLevelField value={c.lpu_care} onChange={(value) => setCond('lpu_care', value)} hint />
                <div className="chips">
                  {ALL_CATEGORIES.map((x) => {
                    const cats = c.categories ?? ALL_CATEGORIES.map((y) => y.key)
                    return <button type="button" key={x.key} aria-pressed={cats.includes(x.key)}
                      onClick={() => { const v = toggle(cats, x.key); if (v.length) setCond('categories', v) }}>{x.label}</button>
                  })}
                </div>
                <OwnRow label="Аптеки" value={c.pharmacy_ownership} onChange={(v) => setCond('pharmacy_ownership', v)} />
                <div className="field"><label htmlFor="pj-turn">Оборот аптеки в месяц</label>
                  <select id="pj-turn" value={c.pharmacy_min_turnover ?? ''}
                    onChange={(e) => setCond('pharmacy_min_turnover', e.target.value === '' ? null : Number(e.target.value))}>
                    {TURNOVER_STEPS.map((t) => <option key={t.label} value={t.v ?? ''}>{t.label}</option>)}
                  </select></div>
              </section>
            </div>

            <button className="linklike danger" onClick={() => remove()}>Удалить проект</button>
            </>}
            {section === 'result' && <>
              {!res?.id ? <p className="seg-hint">{res ? 'Пересчитайте проект, чтобы получить связанную версию маршрутов и отчётов.' :
                'Добавьте города, задайте условия и рассчитайте проект.'}</p> : <>
                {res.stale && <div className="verdict warn" role="status">Показан расчёт от {res.computed}. {res.planner_updated ? 'Обновился расчёт визитов без цели по охвату.' : 'Условия изменились.'} Пересчитайте проект, чтобы обновить планы и отчёты.</div>}
                <div className="project-exports">
                  <button className="btn quiet" onClick={() => download('pdf')} disabled={running || !!dl}>
                    {dl === 'pdf' ? 'Готовлю PDF…' : res.stale ? 'PDF предыдущего расчёта' : 'Выгрузка PDF'}</button>
                  <button className="btn" onClick={() => download('xlsx')} disabled={running || !!dl}>
                    {dl === 'xlsx' ? 'Готовлю Excel…' : res.stale ? 'Excel предыдущего расчёта' : 'Выгрузка Excel'}</button>
                </div>
                <ProjectResults res={res} limit={res.inputs?.conditions.workday_limit_h ?? c.workday_limit_h}
                  openPlan={(row) => onOpenPlan(pr, row.slug, row.reps)} busy={running} />
              </>}
            </>}
          </div>
        )}
      </main>
    </div>
  )
}

function OwnRow({ label, value, onChange }: { label: string; value: Ownership[]; onChange: (v: Ownership[]) => void }) {
  return (
    <div className="chk-row">
      <span className="own-l">{label}</span>
      <label className="chk"><input type="checkbox" checked={value.includes('state')}
        onChange={() => onChange(toggle(value, 'state'))} />гос.</label>
      <label className="chk"><input type="checkbox" checked={value.includes('private')}
        onChange={() => onChange(toggle(value, 'private'))} />частные</label>
    </div>
  )
}


function ProjectResults({ res, limit, openPlan, busy }: {
  res: ProjectResult; limit: number; openPlan: (r: CityResult) => void; busy: boolean
}) {
  const t = res.totals
  const goal = t.has_goal !== false
  const complete = t.cities_bad === 0 && t.cities_warn === 0 && (!goal || t.covered_doctors >= t.target)
  const workload = t.over_limit_days ?? 0
  return (
    <section className="proj-res">
      <div className="headline"><h1>Итоги проекта</h1><span className="period">расчёт от {res.computed}</span></div>
      <div className={'verdict ' + (complete ? 'ok' : t.cities_bad ? 'bad' : 'warn')}>
        {complete ? 'Условия плана выполнены.' : 'В плане есть невыполненные условия.'}
        {' '}{mp(t.reps)} в маршрутах.
        {goal && ' Охват по всем специальностям — ' + num(t.covered_doctors, 0) + ' из ' + num(t.target, 0) + '.'}
        {workload > 0 && ' Сверх нормы ' + num(limit) + ' ч по длительности или P80 — ' + num(workload, 0) + ' человеко-дней.'}
      </div>
      {!!t.unallocated_target && <p className="stale">В доступной базе не хватило {num(t.unallocated_target, 0)} врачей для общей цели.</p>}
      <div className="figures">
        <div className="fig"><div className="v">{num(t.covered_doctors, 0)}{goal && <small> из {num(t.target, 0)}</small>}</div>
          <div className="l">врачей охвачено по всем специальностям</div></div>
        <div className="fig"><div className="v">{num(t.doctor_visits, 0)}</div><div className="l">визитов к врачам</div></div>
        <div className="fig"><div className="v">{num(t.pharmacy_visits, 0)}</div><div className="l">визитов в {num(t.unique_pharmacies, 0)} аптек</div></div>
        <div className={'fig' + (workload ? ' bad' : '')}><div className="v">{num(t.avg_hours)}<small> ч</small></div>
          <div className="l">средний день · среднее P80 {num(t.avg_p80_hours)} ч</div></div>
      </div>
      <h2 className="adv-sub">По городам</h2>
      <div className="project-cards">
        {res.cities.map((r) => (
          <article className={'project-city ' + r.status} key={r.slug}>
            <div className="project-city-row">
              <div><b>{r.city}</b><span className={'st-l ' + r.status}>{STATUS[r.status]}</span></div>
              <div>{mp(r.reps)}{r.reps_auto && <small>Подбор по цели</small>}</div>
              <div>{num(r.covered_doctors, 0)}{goal && ' из ' + num(r.target, 0)} врачей</div>
              <div>{num(r.avg_hours)} ч<small>P80 {num(r.avg_p80_hours)} ч{r.over_limit_days ? ' · сверх нормы: ' + r.over_limit_days : ''}</small></div>
              <button className="btn" disabled={busy || res.stale} onClick={() => openPlan(r)}>План по дням</button>
            </div>
            <details className="project-breakdown">
              <summary>{r.issues.length ? 'Причины и разбивка по специальностям' : 'Разбивка по специальностям'}</summary>
              {r.issues.length > 0 && <ul>{r.issues.map((i) => <li key={i.text}>{i.text}</li>)}</ul>}
              {r.fixes.length > 0 && <ul>{r.fixes.map((fix) => <li key={fix}>{fix}</li>)}</ul>}
              <div className="proj-table"><table className="grid" aria-label={'Охват по специальностям · ' + r.city}>
                <thead><tr><th>Специальность</th><th>В базе</th>{goal && <th>Цель</th>}<th>Охвачено</th><th>Визиты в маршрутах / план</th><th>Кратность по условиям за срок</th></tr></thead>
                <tbody>{r.specialties?.map((sp) => <tr key={sp.specialty}>
                  <td>{sp.specialty}</td><td>{num(sp.available, 0)}</td>{goal && <td>{num(sp.target ?? 0, 0)}</td>}<td>{num(sp.unique, 0)}</td>
                  <td>{num(sp.visits, 0)} / {num(sp.planned, 0)}</td><td>{sp.frequency}</td>
                </tr>)}</tbody>
              </table></div>
              <p className="hint">Всего в базе по выбранным специальностям — {num(r.available_doctors, 0)}. Визиты в аптеки — {num(r.pharmacy_visits, 0)} из {num(r.planned_pharmacy_visits, 0)}.</p>
            </details>
          </article>
        ))}
      </div>
    </section>
  )
}
