import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from './api'
import type { CareFilter, CityNorms, ClientBaseItem, CityData, CityItem, Filters, GoalSpec, ManualTerritories, Meta, Plan, PlanParams, PlanRequest, Project, ProjectConditions, ProjectItem } from './types'
import { ParamsForm, type EntryMode } from './components/ParamsForm'
import { CITY_NORMS, cityNormsFrom } from './components/CitySettings'
import { Calendar, CalendarLegend } from './components/Calendar'
import { DayDetail } from './components/DayDetail'
import { BaseView } from './components/BaseView'
import { DEFAULT_FILTERS, FiltersPanel } from './components/FiltersPanel'
import { Territories } from './components/Territories'
import { AllCities, baseDefaults } from './components/AllCities'
import { AdvicePanel } from './components/AdvicePanel'
import { ProjectsView } from './components/ProjectsView'
import { DataView } from './components/DataView'
import { Home, type Tab } from './components/Home'
import { EMPTY_EDITS, loadEdits, type Edits } from './store'
import { dateRange, iso, num, parseDate, plural } from './format'

function nextMonday() {
  const d = new Date()
  d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7))
  return iso(d)
}

const DEFAULT: PlanParams = {
  city: '', specialty: 'Кардиолог', reps: 1, doctor_visits_per_day: 5, pharmacy_visits_per_day: 3,
  doctor_frequency: 1, pharmacy_frequency: 1, start_date: nextMonday(), months: 3,
  ...CITY_NORMS, start_points: [], doctor_overrides: {}, excluded_ids: [], coverage: 'spread', pharmacy_priority: 'near',
  frequency_unit: 'project_month',
}
const NAV: [Tab, string][] = [['base', 'База'], ['projects', 'Проекты'], ['plan', 'План города'], ['data', 'Данные']]
const requestKey = (value: PlanRequest) => JSON.stringify(value, (_, part: unknown) => (
  part && typeof part === 'object' && !Array.isArray(part)
    ? Object.fromEntries(Object.entries(part).sort(([a], [b]) => a.localeCompare(b))) : part
))

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [cities, setCities] = useState<CityItem[]>([])
  const [city, setCity] = useState<CityData | null>(null)
  const [params, setParams] = useState<PlanParams>(() => ({ ...DEFAULT, start_date: nextMonday() }))
  const [plan, setPlan] = useState<Plan | null>(null)
  const [tab, setTab] = useState<Tab>('home')
  const [baseCity, setBaseCity] = useState<{ slug: string; spec: string; care?: CareFilter } | null>(null)
  const [baseFilters, setBaseFilters] = useState(baseDefaults)
  const [projectId, setProjectId] = useState<string | null>(null)
  // история переходов: каждый экран — запись в истории браузера, кнопка «Назад» и стрелка браузера ведут на предыдущий
  type View = { tab: Tab; baseCity: typeof baseCity }
  const [prevView, setPrevView] = useState<View | null>(null)
  const fromPop = useRef(false)
  const curView = useRef<View>({ tab, baseCity })
  const viewKey = `${tab}|${baseCity?.slug ?? ''}|${baseCity?.spec ?? ''}`
  const firstView = useRef(true)
  useEffect(() => {
    const v: View = { tab, baseCity }
    if (firstView.current) {
      firstView.current = false
      window.history.replaceState({ v, prev: null }, '')
    } else if (fromPop.current) {
      fromPop.current = false
    } else {
      const prev = curView.current
      window.history.pushState({ v, prev }, '')
      setPrevView(prev)
    }
    curView.current = v
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewKey])
  useEffect(() => {
    const onPop = (e: PopStateEvent) => {
      const st = e.state as { v?: View; prev?: View | null } | null
      if (!st?.v) return
      const k = `${st.v.tab}|${st.v.baseCity?.slug ?? ''}|${st.v.baseCity?.spec ?? ''}`
      const c = curView.current
      fromPop.current = k !== `${c.tab}|${c.baseCity?.slug ?? ''}|${c.baseCity?.spec ?? ''}`
      setTab(st.v.tab)
      setBaseCity(st.v.baseCity)
      setPrevView(st.prev ?? null)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  const goBack = () => { if (prevView) window.history.back(); else setTab('home') }
  const [mode, setMode] = useState<EntryMode>('reps')
  const [mobileParamsOpen, setMobileParamsOpen] = useState(true)
  const [rep, setRep] = useState(1)
  const [planView, setPlanView] = useState<'summary' | 'calendar' | 'zones'>('summary')
  const [month, setMonth] = useState('')
  const routePanel = useRef<HTMLDivElement>(null)
  const [selDate, setSelDate] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [fromProject, setFromProject] = useState<{ id: string; name: string; slug: string; revision: string; norms: CityNorms } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)
  const [showInfo, setShowInfo] = useState(false)

  // правки базы (число врачей, исключённые учреждения) — по городу и специальности, общие с «Базой»
  const [edits, setEdits] = useState<Edits>(EMPTY_EDITS)

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(e.message))
    api.cities().then(setCities).catch((e) => setError(e.message))
  }, [])

  const [clientList, setClientList] = useState<ClientBaseItem[]>([])
  useEffect(() => { if (tab === 'plan') api.clientBases().then(setClientList).catch(() => undefined) }, [tab])
  const [cityLoading, setCityLoading] = useState<string | null>(null)
  const refreshCities = useCallback(() => { api.cities().then(setCities).catch(() => undefined) }, [])
  const cityRequest = useRef(0)
  const loadCity = useCallback((slug: string, spec: string, client: string | null = null) => {
    if (!slug) return
    const sequence = ++cityRequest.current
    api.city(slug, spec, client).then((value) => {
      if (sequence === cityRequest.current) setCity(value)
    }).catch(() => { if (sequence === cityRequest.current) setCity(null) })
  }, [])
  const cur = cities.find((x) => x.slug === params.city)
  const curLoaded = cur?.loaded
  const inFlight = useRef<string | null>(null)
  useEffect(() => {
    if (!cur) return
    if (curLoaded) {
      loadCity(params.city, params.specialty, params.client_base ?? null)
      return
    }
    if (inFlight.current === cur.slug) return
    // города ещё нет на диске — скачиваем базу из OpenStreetMap
    ++cityRequest.current
    inFlight.current = cur.slug
    setCity(null)
    setCityLoading(cur.name)
    setError(null)
    api.loadCity(cur.slug)
      .then(() => api.cities())
      .then((cs) => setCities(cs))
      .catch((e) => setError(`${cur.name}: ${(e as Error).message}`))
      .finally(() => { inFlight.current = null; setCityLoading(null) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.city, params.specialty, params.client_base, curLoaded, loadCity])
  useEffect(() => {
    if (params.city && !fromProject) setEdits(loadEdits(params.city, params.specialty))
  }, [params.city, params.specialty, fromProject])
  // правки, сделанные в «Базе», сразу видны плану
  useEffect(() => {
    const h = (e: Event) => {
      const d = (e as CustomEvent<{ city: string; spec: string }>).detail
      if (!fromProject && d.city === params.city && d.spec === params.specialty) setEdits(loadEdits(params.city, params.specialty))
    }
    window.addEventListener('elomer-edits', h)
    return () => window.removeEventListener('elomer-edits', h)
  }, [params.city, params.specialty, fromProject])
  const standaloneDraft = useRef<{ params: PlanParams; mode: EntryMode } | null>(null)
  const cityFilters = useRef(new Map<string, Filters>())
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  useEffect(() => {
    if (!fromProject) setFilters(cityFilters.current.get(params.city) ?? DEFAULT_FILTERS)
  }, [params.city, fromProject])
  const changeFilters = (f: Filters) => {
    setFilters(f)
    if (!fromProject) cityFilters.current.set(params.city, f)
  }

  // пока дозаполняются адреса — обновляем базу
  useEffect(() => {
    if (!city?.geocoding.running) return
    const t = setTimeout(() => loadCity(city.slug, params.specialty, params.client_base ?? null), 8000)
    return () => clearTimeout(t)
  }, [city, loadCity, params.specialty, params.client_base])

  // границы зон, заданные на карте; хранятся по городу и числу медпредов, чтобы не терялись при переключениях
  const cityTerritories = useRef(new Map<string, ManualTerritories>())
  const terrKey = `elomer.terr.${params.city}.${params.reps}`
  const [manualTerr, setManualTerr] = useState<ManualTerritories | null>(null)
  useEffect(() => {
    if (!fromProject) setManualTerr(cityTerritories.current.get(terrKey) ?? null)
  }, [terrKey, fromProject])
  const applyTerr = (t: ManualTerritories | null) => {
    setManualTerr(t)
    if (!fromProject) {
      if (t) cityTerritories.current.set(terrKey, t)
      else cityTerritories.current.delete(terrKey)
    }
  }
  const [lastRequest, setLastRequest] = useState<PlanRequest | null>(null)
  const [calculatedKey, setCalculatedKey] = useState<string | null>(null)
  const fullParams = useCallback((p: PlanParams, terr: ManualTerritories | null = manualTerr): PlanRequest =>
    ({ ...p, ...filters, project_mix: true, frequency_unit: p.frequency_unit ?? 'project_month',
       doctor_overrides: edits.overrides, excluded_ids: edits.excluded,
       territories: terr && terr.centers.length === p.reps ? terr : null }), [edits, filters, manualTerr])
  const fresh = !!plan && !!lastRequest && calculatedKey === requestKey(fullParams(params))

  const acceptPlan = useCallback((result: Plan, draft: PlanRequest, selection?: { rep: number; date: string | null; month: string }) => {
    setLastRequest(result.request ?? draft)
    setCalculatedKey(requestKey(draft))
    setPlan(result)
    const repId = selection && result.reps.some((r) => r.id === selection.rep) ? selection.rep : 1
    const selectedDate = selection?.date
    const days = result.days.filter((d) => d.rep === repId)
    const day = days.find((d) => d.date === selectedDate) ??
      days.find((d) => d.date.startsWith(selection?.month || '-') && d.stops.length) ??
      days.find((d) => d.stops.length) ?? days[0]
    setRep(repId)
    setSelDate(day?.date ?? null)
    setMonth(day?.date.slice(0, 7) ?? result.summary.start.slice(0, 7))
    if (!selection) setMobileParamsOpen(false)
    setTab('plan')
  }, [])

  const run = async (p: PlanParams, terr: ManualTerritories | null = manualTerr) => {
    if (busy) return
    setBusy(true)
    setError(null)
    const sameCity = plan && lastRequest?.city === p.city
    const selection = sameCity && planView !== 'summary' ? { rep, date: selDay?.date ?? selDate, month } : undefined
    try {
      const draft = fullParams(p, terr)
      acceptPlan(await api.plan(draft), draft, selection)
      if (!sameCity) setPlanView('summary')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const [fileBusy, setFileBusy] = useState<string | null>(null)
  async function exportFile(kind: 'xlsx' | 'pdf' | 'routes') {
    if (!fresh || !plan?.calculation_id) return
    setFileBusy(kind)
    try { await api.downloadPlanResult(plan.calculation_id, kind) }
    catch (e) { setError((e as Error).message) } finally { setFileBusy(null) }
  }

  // проект, в который сохраняется план города
  const [projects, setProjects] = useState<ProjectItem[]>([])
  const [planProject, setPlanProject] = useState('')
  useEffect(() => { if (tab === 'plan') api.projects().then(setProjects).catch(() => undefined) }, [tab])
  const [saveMsg, setSaveMsg] = useState<string | null>(null)
  async function saveToProject() {
    if (!plan || !lastRequest || !fresh) return
    setSaveMsg(null)
    try {
      let pid = planProject
      if (!pid) {
        const pr = await api.createProject({ name: `${plan.summary.city}, ${params.specialty.toLowerCase()}`,
          conditions: conditionsFrom({ ...lastRequest, ...CITY_NORMS }) as ProjectConditions })
        pid = pr.id
        setPlanProject(pid)
      }
      const pr = await api.savePlanToProject(pid, lastRequest.city, lastRequest)
      setSaveMsg(`Сохранено в «${pr.name}»`)
      api.projects().then(setProjects).catch(() => undefined)
    } catch (e) { setError((e as Error).message) }
  }

  const [backBusy, setBackBusy] = useState(false)
  async function saveAndBack() {
    if (!fromProject || !lastRequest || !fresh || lastRequest.city !== fromProject.slug) return
    setBackBusy(true)
    try {
      await api.savePlanToProject(fromProject.id, fromProject.slug, lastRequest, true, fromProject.revision)
      backToProject()
    } catch (e) { setError((e as Error).message) } finally { setBackBusy(false) }
  }

  function backToProject() {
    if (!fromProject) return
    setProjectId(fromProject.id)
    restoreStandaloneDraft()
    setPlan(null)
    setTab('projects')
  }

  function restoreStandaloneDraft() {
    const draft = standaloneDraft.current
    standaloneDraft.current = null
    setFromProject(null)
    setParams(draft?.params ?? { ...DEFAULT, start_date: nextMonday() })
    setMode(draft?.mode ?? 'reps')
    setPicking(false)
    setMobileParamsOpen(true)
  }

  async function openPlanFromProject(pr: Project, slug: string) {
    if (!pr.result?.id) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.projectPlan(pr.id, slug, pr.result.id)
      if (!result.request) throw new Error('Пересчитайте проект, чтобы открыть маршрут.')
      const { territories, categories, lpu_care, lpu_ownership, pharmacy_ownership, lpu_networks, pharmacy_networks,
              pharmacy_min_turnover, ...settings } = result.request
      const base = { ...DEFAULT, ...settings }
      const projectFilters = { ...DEFAULT_FILTERS, categories: categories ?? DEFAULT_FILTERS.categories,
        lpu_care: lpu_care ?? 'all',
        lpu_ownership: lpu_ownership ?? DEFAULT_FILTERS.lpu_ownership, pharmacy_ownership: pharmacy_ownership ?? DEFAULT_FILTERS.pharmacy_ownership,
        lpu_networks: lpu_networks ?? null, pharmacy_networks: pharmacy_networks ?? null, pharmacy_min_turnover: pharmacy_min_turnover ?? null }
      if (!fromProject) standaloneDraft.current = { params, mode }
      setFromProject({ id: pr.id, name: pr.name, slug, revision: pr.result.id, norms: cityNormsFrom(pr.conditions) })
      setFilters(projectFilters)
      setEdits({ overrides: base.doctor_overrides, excluded: base.excluded_ids })
      setManualTerr(territories ?? null)
      setParams(base)
      setMode('reps')
      setPlanView('calendar')
      acceptPlan(result, { ...base, ...projectFilters, territories: territories ?? null })
    } catch (e) {
      setError((e as Error).message)
      setTab('plan')
    } finally { setBusy(false) }
  }

  async function createProjectFrom(slugs: string[], spec: string) {
    try {
      const pr = await api.createProject({
        name: `Проект: ${slugs.length} ${plural(slugs.length, 'город', 'города', 'городов')}`,
        cities: slugs.map((slug) => ({ slug, reps: null })),
        conditions: { ...conditionsFrom({ ...params, ...CITY_NORMS }), lpu_care: baseFilters.lpuCare,
          frequency_unit: 'project_month', ...(spec ? { specialty: spec } : {}) } as ProjectConditions,
      })
      setProjectId(pr.id)
      setTab('projects')
    } catch (e) { setError((e as Error).message) }
  }
  function openProject(id: string) {
    setProjectId(id)
    setTab('projects')
  }

  const [goalTarget, setGoalTarget] = useState<number | null>(null)
  const [goalSpec, setGoalSpec] = useState<GoalSpec>({ doctors: true, pharmacies: false, ph: null })
  async function runGoal(target: number | null) {
    setBusy(true)
    setError(null)
    try {
      const base = { ...params, doctor_visits_per_day: goalSpec.doctors ? params.doctor_visits_per_day : 0,
        extra_specialties: goalSpec.doctors ? params.extra_specialties : [],
        pharmacy_visits_per_day: goalSpec.pharmacies ? params.pharmacy_visits_per_day : 0 }
      const result = await api.plan({ ...fullParams(base, null), team_goal_doctors: goalSpec.doctors,
        team_goal_pharmacies: goalSpec.pharmacies, doctor_target: target, pharmacy_target: goalSpec.ph })
      const next = { ...base, reps: result.summary.reps, specialty_targets: result.request?.specialty_targets ?? null,
        pharmacy_target: result.request?.pharmacy_target ?? null }
      setParams(next)
      applyTerr(null)
      setMode('reps')
      setPlanView('summary')
      acceptPlan(result, fullParams(next, null))
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  function pickStart(pt: [number, number]) {
    if (busy) return
    setPicking(false)
    const sp = [...params.start_points]
    while (sp.length < params.reps) sp.push(null)
    sp[rep - 1] = pt
    const next = { ...params, start_points: sp }
    setParams(next)
    run(next)
  }

  const repDays = useMemo(() => plan?.days.filter((d) => d.rep === rep) ?? [], [plan, rep])
  const selDay = repDays.find((d) => d.date === selDate && d.date.startsWith(month)) ?? repDays.find((d) => d.date.startsWith(month) && d.stops.length)
  const repInfo = plan?.reps.find((r) => r.id === rep)

  function renderCalendar(calendar: ReactNode) {
    if (!selDay || !repInfo || !plan) return calendar
    return <DayDetail day={selDay} rep={repInfo} multiRep={plan.reps.length > 1} calendar={calendar} detailsRef={routePanel}
      facilities={plan.facilities} center={city?.center ?? selDay.home} limitH={plan.summary.workday_limit_h}
      busy={busy} picking={picking} onTogglePick={() => setPicking((v) => !v)} onPick={pickStart} />
  }

  if (!meta) {
    return <div className="app"><div className="main">{error ? <div className="error">{error}. Запущен ли сервер?</div> : <div className="loading">Загрузка…</div>}</div></div>
  }

  const viewName = (v: View) => {
    const cn = (slug: string) => cities.find((c) => c.slug === slug)?.name ?? ''
    if (v.tab === 'base') return v.baseCity ? `База: ${cn(v.baseCity.slug)}` : 'База'
    if (v.tab === 'plan') return params.city ? `План города: ${cn(params.city)}` : 'План города'
    return { home: 'Главная', projects: 'Проекты', data: 'Данные' }[v.tab]
  }
  const back = (
    <div className="backbar">
      <button type="button" className="back-btn" onClick={tab === 'plan' && fromProject ? backToProject : goBack}>
        <span aria-hidden="true">←</span> Назад{prevView ? <small>{viewName(prevView)}</small> : <small>Главная</small>}
      </button>
    </div>
  )

  const s = plan?.summary
  const shortDoc = s ? s.planned_doctor_visits - s.doctor_visits : 0
  // нехватку врачей и длину дня подробно разбирает «Что улучшить» — здесь не дублируем
  const dupe = (t: string) => t.startsWith('Не хватает врачей') || t.startsWith('По нормативам')
  const crit = plan?.warnings.filter((w) => w.level !== 'info' && !(plan.advice && dupe(w.text))) ?? []
  const info = plan?.warnings.filter((w) => w.level === 'info') ?? []
  const verdict = s && planVerdict(s, s.workday_limit_h)

  return (
    <div className="app">
      <header className="topbar">
        <button className="wordmark" onClick={() => setTab('home')} aria-current={tab === 'home' ? 'page' : undefined}>
          ЭЛОМЕР<small>планирование визитов</small>
        </button>
        <nav className="nav">
          {NAV.map(([k, l]) => (
            <button key={k} aria-current={tab === k ? 'page' : undefined}
              onClick={() => {
                if (k !== 'plan') setPicking(false)
                if (k === 'plan' && tab !== 'plan' && fromProject) { restoreStandaloneDraft(); setPlan(null) }
                if (k === 'base' && tab === 'base') setBaseCity(null)
                setTab(k)
              }}>{l}</button>
          ))}
        </nav>
      </header>

      {tab === 'home' ? (
        <main className="main page"><Home cities={cities} lastCity={params.city} onGo={setTab} onOpenProject={openProject} /></main>
      ) : tab === 'base' ? (
        <main className="main page">
          {back}
          {baseCity ? (
            <BaseView meta={meta} slug={baseCity.slug} specialty={baseCity.spec} initialClient={baseFilters.client}
              initialCare={baseCity.care} onBack={() => setBaseCity(null)}
              onPlan={(slug, spec, care) => {
                if (fromProject) restoreStandaloneDraft()
                setMobileParamsOpen(true)
                const nextFilters = { ...DEFAULT_FILTERS, lpu_care: care }
                cityFilters.current.set(slug, nextFilters)
                setFilters(nextFilters)
                setParams((p) => ({ ...p, ...CITY_NORMS, city: slug, specialty: spec || p.specialty, project_mix: false, specialty_targets: null }))
                setPlan(null)
                setTab('plan')
              }} />
          ) : (
            <AllCities meta={meta} cities={cities} specialty={params.specialty} filters={baseFilters} onFiltersChange={setBaseFilters}
              onOpenCity={(slug, spec) => setBaseCity({ slug, spec, care: baseFilters.lpuCare })} onCreateProject={createProjectFrom} />
          )}
        </main>
      ) : tab === 'data' ? (
        <main className="main page">{back}<DataView /></main>
      ) : tab === 'projects' ? (
        <ProjectsView meta={meta} cities={cities} onCitiesChanged={refreshCities} onOpenPlan={openPlanFromProject} initialProjectId={projectId} onSelectProject={setProjectId} backSlot={back} />
      ) : (
      <div className="workspace city-workspace">
        <aside className="side city-side">
          <button type="button" className="btn city-params-toggle" aria-expanded={mobileParamsOpen} aria-controls="city-params"
            onClick={() => setMobileParamsOpen((v) => !v)}>{mobileParamsOpen ? 'Скрыть параметры плана' : 'Параметры плана'}</button>
          <div id="city-params" className={mobileParamsOpen ? 'city-params' : 'city-params collapsed'}>
          <ParamsForm meta={meta} cities={cities} params={params} linked={!!fromProject} normDefaults={fromProject?.norms}
            onChange={(next) => setParams((current) => current.city === next.city ? next : { ...next, ...CITY_NORMS })} busy={busy}
            cityReady={!!city && city.slug === params.city && !cityLoading}
            cityStatus={cityLoading ? `Загружаю базу «${cityLoading}» из OpenStreetMap. Крупный город — до 3 минут.`
              : city && city.slug === params.city
                ? `В базе ${city.facilities.filter((f) => f.kind === 'lpu').length} ЛПУ и ${city.facilities.filter((f) => f.kind === 'pharmacy').length} аптек${city.geocoding.running ? `; уточняем адреса: ${city.geocoding.done} из ${city.geocoding.total}` : ''}.`
                : null}
            onReset={fromProject ? undefined : () => {
              if (!window.confirm('Сбросить параметры плана, фильтры учреждений и границы зон? Город останется, правки базы города — тоже.')) return
              setParams({ ...DEFAULT, city: params.city, start_date: nextMonday(), start_points: [] })
              changeFilters(DEFAULT_FILTERS)
              applyTerr(null)
              setMode('reps'); setGoalTarget(null); setGoalSpec({ doctors: true, pharmacies: false, ph: null }); setPlan(null); setPlanProject('')
            }}
            onSubmit={() => run(params)} onAddCity={() => { setBaseCity({ slug: params.city, spec: params.specialty, care: filters.lpu_care }); setTab('base') }}
            onGoal={runGoal} goalTarget={goalTarget} onGoalTarget={setGoalTarget} goalSpec={goalSpec} onGoalSpec={setGoalSpec} mode={mode} onMode={(next) => { setMode(next); setPlan(null); setParams({ ...params, specialty_targets: null, pharmacy_target: null }) }}
            clientBases={clientList.filter((c) => !c.job?.running && c.rows)}
            filtersSlot={<FiltersPanel city={city && city.slug === params.city ? city : null} filters={filters}
              onChange={changeFilters} specialty={params.specialty} />} />
          </div>
          {error && <div className="error" role="alert">{error}</div>}
        </aside>

        <main className="main">
          {back}
          {plan && !fresh && !busy && <div className="verdict warn">Условия изменены. Пересчитайте план, чтобы сохранить и выгрузить актуальный результат.</div>}
          {!plan || !s || lastRequest?.city !== params.city ? (
            <div className="empty">
              <h1>{mode === 'goal' ? 'Цель по охвату' : 'План города'}</h1>
              <p>Укажите условия слева. После расчёта здесь появятся охват, нагрузка и маршруты.</p>
              {busy && <div className="loading">Считаю…</div>}
            </div>
          ) : (
            <>
              {fromProject && fromProject.slug === params.city && (
                <div className="from-project">
                  <div>
                    <b>Город из проекта «{fromProject.name}»</b>
                    <span>Здесь можно уточнить команду, учреждения, зоны и нормативы этого города.</span>
                  </div>
                  <div className="from-project-act">
                    <button className="btn primary" onClick={saveAndBack} disabled={busy || backBusy || !fresh}>
                      {backBusy ? 'Сохраняю…' : 'Сохранить и вернуться к проекту'}</button>
                    <button className="linklike" onClick={backToProject}>Вернуться без сохранения</button>
                  </div>
                </div>
              )}
              <div className="headline">
                <h1>{s.city}, {(s.specialties?.map((x) => x.specialty) ?? [s.specialty]).join(' + ').toLowerCase()}{s.reps > 1 ? `, ${s.reps} медпреда` : ''}</h1>
                <span className="period">{dateRange(s.start, s.end)}, {s.working_days} {plural(s.working_days, 'рабочий день', 'рабочих дня', 'рабочих дней')}, {s.transport.toLowerCase()}</span>
              </div>
              <div className="plan-actions">
                {!fromProject && <><select value={planProject} onChange={(e) => { setPlanProject(e.target.value); setSaveMsg(null) }} aria-label="Проект для сохранения">
                  <option value="">Новый проект</option>
                  {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
                <button className="btn" onClick={saveToProject} disabled={busy || !fresh}>Сохранить в проект</button></>}
                {saveMsg && <span className="muted">{saveMsg}</span>}
                <div className="spacer" />
                <button className="btn quiet" onClick={() => exportFile('routes')} disabled={busy || !fresh || !!fileBusy}>{fileBusy === 'routes' ? 'Готовлю…' : 'Маршрутные листы'}</button>
                <button className="btn quiet" onClick={() => exportFile('pdf')} disabled={busy || !fresh || !!fileBusy}>{fileBusy === 'pdf' ? 'Готовлю PDF…' : 'Выгрузка PDF'}</button>
                <button className="btn quiet" onClick={() => exportFile('xlsx')} disabled={busy || !fresh || !!fileBusy}>Выгрузка Excel</button>
              </div>

              {verdict && <div className={`verdict ${verdict.level}`}>{verdict.text}</div>}

              <div className="seg plan-views" aria-label="Раздел плана">
                {([['summary', 'Сводка'], ['calendar', 'Календарь'], ['zones', 'Зоны']] as const)
                  .filter(([key]) => key !== 'zones' || plan.reps.length > 1)
                  .map(([key, label]) => <button key={key} aria-pressed={planView === key} onClick={() => { setPicking(false); setPlanView(key) }}>{label}</button>)}
              </div>

              {planView === 'summary' && <>
              <div className="figures">
                <div className={`fig${shortDoc > 0 && s.need_doctors > s.available_doctors ? ' bad' : ''}`}>
                  <div className="v">{s.doctor_visits}<small> из {s.planned_doctor_visits}</small></div>
                  <div className="l">визитов к врачам; {s.unique_doctors} врачей в {s.unique_lpu} ЛПУ</div>
                </div>
                <div className="fig"><div className="v">{s.pharmacy_visits}<small> из {s.planned_pharmacy_visits}</small></div><div className="l">визитов в {s.unique_pharmacies} аптек</div></div>
                <div className={`fig${s.over_limit_days ? ' bad' : ''}`}>
                  <div className="v">{num(s.avg_hours)}<small> ч</small></div>
                  <div className="l">средний день · среднее P80 {num(s.avg_p80_hours)} ч</div>
                </div>
                <div className="fig"><div className="v">{num(s.avg_km)}<small> км</small></div><div className="l">в день, {num(s.total_km, 0)} км за проект</div></div>
              </div>
              {s.month_windows?.length ? <p className="fig-more">
                Кратность проверяется по месяцам от даты старта. Первый месяц — {dateRange(s.month_windows[0].start, s.month_windows[0].end)}.
                {' '}Самый короткий месяц — {Math.min(...s.month_windows.map((window) => window.working_days))} рабочих дней.
              </p> : null}
              <p className="fig-more">
                {s.repeat_gap_days !== null && <>Повторный визит к врачу — через {num(s.repeat_gap_days, 0)} дн. </>}
                В контакте с врачом или аптекой {num(s.contact_share, 0)}% дня.
              </p>

              {(s.specialties?.length ?? 0) > 1 && (
                <div className="table-wrap" tabIndex={0} aria-label="Показатели по специальностям, с прокруткой">
                <table className="grid by-spec">
                  <thead><tr><th>Специальность</th><th>По условиям: в день / к врачу за проект</th><th>Визитов: в маршрутах из плана</th>
                    {s.project_mix && <th>Цель, врачей</th>}<th>Врачей охвачено</th><th>Врачей в зонах</th></tr></thead>
                  <tbody>
                    {s.specialties!.map((x) => (
                      <tr key={x.specialty} className={x.visits < x.planned * 0.95 ? 'short' : ''}>
                        <td>{x.specialty}</td><td>{x.per_day} / {x.frequency}</td>
                        <td>{num(x.visits, 0)} из {num(x.planned, 0)}</td>
                        {s.project_mix && <td>{x.target === undefined ? '—' : num(x.target, 0)}</td>}
                        <td>{num(x.unique, 0)}</td><td>{num(x.available, 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              )}

              {plan.advice && (
                <AdvicePanel advice={plan.advice} specialty={s.specialty}
                  hasExtraSpecialties={(s.specialties?.length ?? 0) > 1}
                  current={s.doctor_visits_per_day} busy={busy}
                  onApply={!fromProject && params.specialty === s.specialty ? (change) => {
                    const next = { ...params, ...change }
                    setParams(next)
                    run(next)
                  } : undefined} />
              )}

              {(crit.length > 0 || info.length > 0) && (
                <ul className="notes">
                  {crit.map((w, i) => <li key={i} className={w.level}>{w.text}</li>)}
                  {showInfo && info.map((w, i) => <li key={`i${i}`} className="info">{w.text}</li>)}
                </ul>
              )}
              {info.length > 0 && (
                <div className="notes-toggle">
                  <button className="linklike" onClick={() => setShowInfo((v) => !v)}>
                    {showInfo ? 'Скрыть пояснения' : `Пояснения к расчёту (${info.length})`}
                  </button>
                </div>
              )}

              </>}
              {planView === 'zones' && plan.reps.length > 1 && lastRequest && (
                <Territories plan={plan} request={lastRequest} busy={busy || !fresh}
                  center={city?.center ?? plan.reps[0].home} rep={rep} onRep={setRep}
                  onApply={(t) => { applyTerr(t); run(params, t) }} />
              )}
              {planView === 'calendar' && <>
              <div className="section-head">
                <h2>Календарь проекта{plan.reps.length > 1 ? `, МП ${rep}` : ''}</h2>
                {plan.reps.length > 1 && (
                  <div className="rep-tabs">
                    {plan.reps.map((r) => (
                      <button key={r.id} aria-pressed={rep === r.id} onClick={() => setRep(r.id)}>
                        <i style={{ background: r.color }} />МП {r.id}
                      </button>
                    ))}
                  </div>
                )}
                <CalendarLegend />
              </div>
              {renderCalendar(
                <section className="day-calendar" aria-label="Выбор даты">
                  <label className="calendar-month">Месяц
                    <input type="month" value={month} min={s.start.slice(0, 7)} max={s.end.slice(0, 7)}
                      onChange={(e) => {
                        if (!e.target.value) return
                        setMonth(e.target.value)
                        setSelDate(repDays.find((d) => d.date.startsWith(e.target.value))?.date ?? null)
                      }} />
                  </label>
                  <Calendar days={repDays} start={s.start > month + '-01' ? s.start : month + '-01'}
                    end={s.end < monthEnd(month) ? s.end : monthEnd(month)} selected={selDay?.date ?? null}
                    onSelect={(day) => {
                      setSelDate(day)
                      if (window.matchMedia('(max-width: 1199px)').matches) {
                        requestAnimationFrame(() => routePanel.current?.scrollIntoView({ block: 'start' }))
                      }
                    }} limitH={s.workday_limit_h} />
                </section>,
              )}
              </>}
            </>
          )}
        </main>
      </div>
      )}
    </div>
  )
}

function conditionsFrom(p: PlanParams): Partial<ProjectConditions> {
  return {
    specialty: p.specialty, doctor_visits_per_day: p.doctor_visits_per_day, pharmacy_visits_per_day: p.pharmacy_visits_per_day,
    doctor_frequency: p.doctor_frequency, pharmacy_frequency: p.pharmacy_frequency, start_date: p.start_date, months: p.months,
    transport: p.transport, workday_limit_h: p.workday_limit_h, coverage: p.coverage, extra_specialties: p.extra_specialties ?? [], frequency_unit: p.frequency_unit ?? 'project_month',
  }
}

function monthEnd(month: string) {
  const date = parseDate(month + '-01')
  return iso(new Date(date.getFullYear(), date.getMonth() + 1, 0))
}

function planVerdict(s: Plan['summary'], limit: number): { level: 'ok' | 'warn' | 'bad'; text: string } {
  const details = []
  if (s.doctor_visits < s.planned_doctor_visits || s.pharmacy_visits < s.planned_pharmacy_visits) {
    details.push('Часть запрошенных визитов не вошла в маршруты.')
  }
  if (s.over_limit_days) {
    details.push(`Дни сверх нормы ${num(limit)} ч по длительности или P80: ${num(s.over_limit_days, 0)}. Проверено расписаний: ${s.checked_variants ?? 1}.`)
  }
  if (details.length) return { level: s.over_limit_days ? 'bad' : 'warn', text: details.join(' ') }
  return { level: 'ok', text: s.doctor_visits || s.pharmacy_visits ? 'Все запрошенные визиты поставлены. Маршруты укладываются в норму.' : 'В плане нет визитов.' }
}
