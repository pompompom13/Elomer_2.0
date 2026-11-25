import type { CareFilter, CareLevel, ClientBaseItem, ClientReport, FacilityCard, CityStats, FieldImport, FieldJob, BaseExportQuery, BaseQuery, BaseSummary, CityData, CityItem, GeoStatus, GoalResult, JobStatus, Meta, Plan, PlanRequest, PreloadStatus, Project, ProjectItem, ZonesPreview } from './types'

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) {
    let msg = `Ошибка сервера (${r.status})`
    try {
      const j = await r.json()
      if (j.detail) msg = typeof j.detail === 'string' ? j.detail : 'Проверьте параметры расчёта'
    } catch {
      /* ответ не JSON */
    }
    throw new Error(msg)
  }
  if (!(r.headers.get('Content-Type') || '').includes('json')) {
    throw new Error('Сервер работает на старой версии программы. Закройте окно ЭЛОМЕР и снова запустите run.bat.')
  }
  return r.json() as Promise<T>
}

const json = (body: unknown, method = 'POST'): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** Сохранить файл: ссылка добавляется в страницу, а адрес освобождается не сразу —
 * иначе некоторые браузеры (Яндекс Браузер, Firefox) отменяют загрузку. */
function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.rel = 'noopener'
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  setTimeout(() => { a.remove(); URL.revokeObjectURL(url) }, 60_000)
}

/** Скачать файл из ответа сервера: имя берётся из Content-Disposition. */
async function download(url: string, init?: RequestInit, fallback = 'file') {
  const r = await fetch(url, init)
  if (!r.ok) {
    let msg = 'Не удалось сформировать файл'
    try { const j = await r.json(); if (typeof j.detail === 'string') msg = j.detail } catch { /* не JSON */ }
    throw new Error(msg)
  }
  const blob = await r.blob()
  const m = /filename\*=UTF-8''([^;]+)/.exec(r.headers.get('Content-Disposition') || '')
  saveBlob(blob, m ? decodeURIComponent(m[1]) : fallback)
}

export const api = {
  meta: () => req<Meta>('/api/meta'),
  marketAnalysis: <T>(mode: 'comparison' | 'distribution', file: File) => {
    const body = new FormData()
    body.append('file', file)
    return req<T>(`/api/market-analysis/${mode}`, { method: 'POST', body })
  },
  downloadMarketAnalysis: async (mode: 'comparison' | 'distribution', file: File, name: string) => {
    const body = new FormData()
    body.append('file', file)
    const response = await fetch(`/api/market-analysis/${mode}?excel=true`, { method: 'POST', body })
    if (!response.ok) throw new Error('Не удалось сформировать Excel')
    saveBlob(await response.blob(), name)
  },
  cities: () => req<CityItem[]>('/api/cities'),
  city: (slug: string, specialty: string, clientBase?: string | null) =>
    req<CityData>(`/api/cities/${slug}?specialty=${encodeURIComponent(specialty)}${clientBase ? `&client_base=${clientBase}` : ''}`),
  geocoding: (slug: string) => req<GeoStatus>(`/api/cities/${slug}/geocoding`),
  loadCity: (slug: string) => req<{ slug: string; count: number }>(`/api/cities/${slug}/load`, { method: 'POST' }),
  preload: () => req<PreloadStatus>('/api/preload', { method: 'POST' }),
  preloadStatus: () => req<PreloadStatus>('/api/preload'),
  baseSummary: (q: BaseQuery) => req<BaseSummary>('/api/base/summary', json(q)),
  /** точки всех учреждений по фильтрам: [широта, долгота, тип/собственность, вне города] */
  baseTotals: () => req<{ fresh: boolean; lpu: number | null; pharmacies: number | null; doctors: number | null }>('/api/base/totals'),
  basePoint: (query: BaseQuery, kind: 'lpu' | 'pharmacy', i: number) =>
    req<{ name: string; address: string; type: string; city: string; ownership: string; network: string; doctors: number | null; specialty: string; care_level: CareLevel | null }>('/api/base/point', json({ query, kind, i })),
  basePoints: (q: BaseQuery) => req<{ lpu: number[][]; pharmacies: number[][] }>('/api/base/points', json(q)),
  async downloadBase(q: BaseExportQuery) {
    const r = await fetch('/api/base/xlsx', json(q))
    if (!r.ok) throw new Error('Не удалось сформировать файл')
    const blob = await r.blob()
    const m = /filename\*=UTF-8''([^;]+)/.exec(r.headers.get('Content-Disposition') || '')
    saveBlob(blob, m ? decodeURIComponent(m[1]) : 'base.xlsx')
  },
  territories: (p: PlanRequest) => req<ZonesPreview>('/api/territories', json(p)),
  plan: (p: PlanRequest) => req<Plan>('/api/plan', json(p)),
  importBase: (slug: string, cityName: string, file: File, replace: boolean) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('replace', String(replace))
    fd.append('city_name', cityName)
    return req<{ imported: number; need_geocoding: number; slug: string }>(
      `/api/cities/${slug}/import`, { method: 'POST', body: fd })
  },
  async downloadXlsx(p: PlanRequest) {
    const r = await fetch('/api/plan/xlsx', json(p))
    if (!r.ok) throw new Error('Не удалось сформировать файл')
    const blob = await r.blob()
    const cd = r.headers.get('Content-Disposition') || ''
    const m = /filename\*=UTF-8''([^;]+)/.exec(cd)
    const name = m ? decodeURIComponent(m[1]) : 'plan.xlsx'
    saveBlob(blob, name)
  },
  downloadPlanPdf: (p: PlanRequest, client = '') => download('/api/plan/pdf', json({ ...p, client }), 'report.pdf'),
  goal: (p: PlanRequest, target: number | null, autoD = false, spec?: { doctors: boolean; pharmacies: boolean; ph: number | null }) =>
    req<GoalResult>('/api/goal', json({ ...p, target_doctors: target, auto_d: autoD,
      ...(spec ? { use_doctors: spec.doctors, use_pharmacies: spec.pharmacies, target_pharmacies: spec.ph } : {}) })),
  projects: () => req<ProjectItem[]>('/api/projects'),
  project: (id: string) => req<Project>(`/api/projects/${id}`),
  createProject: (body: Partial<Project>) => req<Project>('/api/projects', json(body)),
  saveProject: (id: string, body: Partial<Project>) => req<Project>(`/api/projects/${id}`, json(body, 'PUT')),
  deleteProject: (id: string) => req<{ ok: boolean }>(`/api/projects/${id}`, { method: 'DELETE' }),
  computeProject: (id: string) => req<JobStatus>(`/api/projects/${id}/compute`, { method: 'POST' }),
  projectJob: (id: string) => req<JobStatus>(`/api/projects/${id}/job`),
  downloadProject: (id: string, kind: 'xlsx' | 'pdf', revision?: string) =>
    download(`/api/projects/${id}/${kind}${revision ? `?revision=${encodeURIComponent(revision)}` : ''}`,
      { method: 'GET' }, `project.${kind}`),
  fieldData: () => req<FieldImport[]>('/api/field-data'),
  importFieldData: (title: string, files: File[]) => {
    const fd = new FormData()
    fd.append('title', title)
    files.forEach((f) => fd.append('files', f))
    return req<FieldJob>('/api/field-data', { method: 'POST', body: fd })
  },
  fieldDataStatus: (key: string) => req<FieldJob>(`/api/field-data/${key}/status`),
  deleteFieldData: (key: string) => req<{ ok: boolean }>(`/api/field-data/${key}`, { method: 'DELETE' }),
  facility: (slug: string, id: string) => req<FacilityCard>(`/api/cities/${slug}/facility/${encodeURIComponent(id)}`),
  downloadRoutes: (p: PlanRequest) => download('/api/plan/routes', json(p), 'routes.xlsx'),
  projectPlan: (pid: string, slug: string, revision: string) =>
    req<Plan>(`/api/projects/${pid}/plans/${slug}?revision=${encodeURIComponent(revision)}`),
  downloadPlanResult: (id: string, kind: 'xlsx' | 'pdf' | 'routes') =>
    download(`/api/plan/results/${id}/${kind}`, { method: 'GET' }, kind === 'pdf' ? 'report.pdf' : 'plan.xlsx'),
  savePlanToProject: (pid: string, slug: string, p: PlanRequest, recompute = false, revision?: string) =>
    req<Project>(`/api/projects/${pid}/plans/${slug}?${new URLSearchParams({ recompute: String(recompute), ...(revision ? { revision } : {}) })}`, json(p, 'PUT')),
  territory: () => req<{ pharmacies: number; lpu: number; suburbs_loaded: number; cities: number; lpu_registry?: boolean; running: boolean; done: number; total: number; current: string; failed: { name: string; error: string }[]; partial?: number; missing_cities?: number }>('/api/territory'),
  lpuRegistry: () => req<LpuRegistryStatus>('/api/lpu-registry'),
  uploadLpuRegistry: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return req<LpuRegistryStatus>('/api/lpu-registry', { method: 'POST', body: fd })
  },
  dental: () => req<DentalStatus>('/api/dental'),
  loadDental: () => req<DentalStatus>('/api/dental/load', { method: 'POST' }),
  loadSuburbs: (retry = false) => req<{ running: boolean }>(`/api/territory/suburbs${retry ? '?retry=true' : ''}`, { method: 'POST' }),
  rebuildOutsidePharmacies: () => req<{ pharmacies: number; suburbs: number }>('/api/territory/pharmacies', { method: 'POST' }),
  clientBases: (projectId = '') => req<ClientBaseItem[]>(`/api/client-bases${projectId ? `?project_id=${projectId}` : ''}`),
  importClientBase: (name: string, projectId: string, file: File) => {
    const fd = new FormData()
    fd.append('name', name)
    fd.append('project_id', projectId)
    fd.append('files', file)
    return req<ClientBaseItem>('/api/client-bases', { method: 'POST', body: fd })
  },
  clientBaseReport: (id: string) => req<ClientReport>(`/api/client-bases/${id}/report`),
  reviewClientBase: (id: string, body: { action: 'accept' | 'reject'; ids?: string[] | null; doctors?: { id: string; spec: string }[] }) =>
    req<ClientBaseItem>(`/api/client-bases/${id}/review`, json(body)),
  deleteClientBase: (id: string) => req<{ ok: boolean }>(`/api/client-bases/${id}`, { method: 'DELETE' }),
  cityStats: (slug: string, care: CareFilter = 'all', client?: string | null) =>
    req<CityStats>(`/api/cities/${slug}/stats?lpu_care=${care}${client ? `&client_base=${encodeURIComponent(client)}` : ''}`),
}

export interface DentalStatus {
  running: boolean; done: number; total: number; current: string; added: number
  failed: { name: string; error: string }[]; loaded: number; cities: number; registry?: boolean
}

export interface LpuRegistryStatus {
  running: boolean; stage: string; done: number; total: number; current: string; read: number; error: string
  loaded: boolean
  report: null | {
    updated: string; cities: number; lpu_cities: number; lpu_outside: number; suburbs: number
    not_found?: string[]; stats: { rows: number; excluded: number; no_coords: number; merged: number }
  }
}
