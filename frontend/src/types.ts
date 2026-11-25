export type Transport = 'car' | 'public' | 'walk'
export type CareLevel = 'ambulatory' | 'hospital' | 'mixed' | 'unknown'
export type CareFilter = 'all' | CareLevel

export interface Meta {
  specialties: string[]
  wait_median: Record<string, number>
  doctor_visit_min: number
  pharmacy_visit_min: number
  pharmacy_wait_min: number
  lpu_entry_min: number
  lunch_min: number
  crm_min: number
  workday_limit_h: number
  day_start: string
  transport: Record<Transport, { label: string; speed_kmh: number; detour: number; overhead_min: number }>
  doctors_per_category: Record<string, Record<string, number>>
  categories: Record<string, string>
}

export interface CityItem {
  slug: string
  name: string
  region: string
  population: number | null
  loaded: boolean
  lpu: number
  pharmacies: number
  updated: string
}

export interface PreloadStatus {
  running: boolean
  done: number
  total: number
  current: string
  failed: { name: string; error: string }[]
}

export interface Facility {
  id: string
  name: string
  kind: 'lpu' | 'pharmacy'
  category: string
  care_level?: CareLevel
  address: string
  lat: number
  lon: number
  phone?: string
  hours?: string
  source: 'osm' | 'import' | 'field' | 'registry' | 'client'
  doctors: { name: string; spec: string }[]
  needs_coords?: boolean
  est?: number
  in_registry?: boolean
  turnover?: number | null
  known_doctors?: Record<string, number>
  ownership?: Ownership
  network?: string
  network_name?: string
}

export type Ownership = 'state' | 'private'

export interface NetItem {
  key: string
  name: string
  count: number
}

export interface CityData {
  slug: string
  name: string
  center: [number, number]
  updated: string
  sources: string[]
  facilities: Facility[]
  geocoding: GeoStatus
  networks: { lpu: NetItem[]; pharmacy: NetItem[] }
  doctors_stat?: { method: DoctorMethod; target: number; base: number }
}

export interface Filters {
  categories: string[]
  lpu_care: CareFilter
  lpu_ownership: Ownership[]
  pharmacy_ownership: Ownership[]
  lpu_networks: string[] | null
  pharmacy_networks: string[] | null
  pharmacy_min_turnover?: number | null
}

export interface GeoStatus {
  running: boolean
  done: number
  total: number
  error?: string
}

export interface CityNorms {
  transport: Transport
  day_start: string
  workday_limit_h: number
  travel_time_factor: number
  travel_detour: number | null
  travel_overhead_min: number | null
  wait_min: number | null
  doctor_visit_min: number | null
  pharmacy_visit_min: number | null
  pharmacy_wait_min: number | null
  lpu_entry_min: number | null
  lunch_min: number | null
  crm_min: number | null
}

export interface PlanParams extends CityNorms {
  city: string
  specialty: string
  reps: number
  doctor_visits_per_day: number
  pharmacy_visits_per_day: number
  doctor_frequency: number
  pharmacy_frequency: number
  start_date: string
  months: number
  start_points: ([number, number] | null)[]
  doctor_overrides: Record<string, number>
  excluded_ids: string[]
  coverage: 'spread' | 'compact'
  pharmacy_priority?: 'near' | 'turnover'
  frequency_unit?: 'project_month' | 'calendar_month' | 'month' | 'project'
  client_base?: string | null
  extra_specialties?: ExtraSpec[]
  project_mix?: boolean
  specialty_targets?: Record<string, number> | null
  team_goal_doctors?: boolean
  team_goal_pharmacies?: boolean
  doctor_target?: number | null
  pharmacy_target?: number | null
  doctor_override_specialty?: string | null
}

export type PlanRequest = PlanParams & Filters & { territories?: ManualTerritories | null }

export type SegType = 'travel' | 'entry' | 'wait' | 'visit' | 'pharmacy' | 'lunch' | 'crm'

export interface Stop {
  seq: number
  type: 'lpu' | 'pharmacy'
  facility_id: string
  name: string
  address: string
  lat: number
  lon: number
  category: string
  arrive: string
  leave: string
  travel_min: number
  km: number
  visit_no?: number
  doctors: { name: string; visit_no: number; start: string; end: string }[]
}

export interface Day {
  date: string
  weekday: string
  rep: number
  kind: 'work' | 'reserve' | 'no_doctors' | 'pharmacy_only'
  home: [number, number]
  stops: Stop[]
  segments: [SegType, number, number][]
  totals: {
    doctor_visits: number
    pharmacy_visits: number
    lpu: number
    start: string
    end: string
    hours: number
    km: number
    contact_min: number
    travel_min: number
    wait_min: number
    p50_hours: number
    p80_hours: number
    overtime_risk: number
  }
}

export interface SpecialtyCoverage {
  specialty: string
  per_day: number
  frequency: number
  planned: number
  visits: number
  unique: number
  available: number
  target?: number
  capacity_per_rep?: number
}

export interface Summary {
  city: string
  specialty: string
  reps: number
  start: string
  end: string
  working_days: number
  reserve_days: number
  planned_doctor_visits: number
  planned_pharmacy_visits: number
  doctor_visits: number
  pharmacy_visits: number
  unique_doctors: number
  unique_lpu: number
  unique_pharmacies: number
  available_doctors: number
  available_pharmacies: number
  need_doctors: number
  need_pharmacies: number
  avg_hours: number
  avg_p80_hours: number
  max_hours: number
  risky_days: number
  workday_limit_h: number
  avg_km: number
  total_km: number
  contact_share: number
  repeat_gap_days: number | null
  recommended_doctor_visits: number | null
  specialties?: SpecialtyCoverage[]
  project_mix?: boolean
  over_limit_days?: number
  checked_variants?: number
  month_windows?: { start: string; end: string; working_days: number }[]
  transport: string
  doctor_frequency: number
  pharmacy_frequency: number
  doctor_visits_per_day: number
  pharmacy_visits_per_day: number
}

export interface RepInfo {
  id: number
  color: string
  home: [number, number]
  doctors: number
  lpu: number
  pharmacies: number
  territory_doctors: number
  territory_pharmacies: number
  groups: number
  polygon: [number, number][]
  center: [number, number]
  weight: number
  extension: number
  suburb: SuburbCounts
}

export interface ManualTerritories {
  centers: [number, number][]
  weights: number[]
  extensions?: number[]
  drawn?: { rep: number; polygon: [number, number][] }[]   // нарисованные области: rep — индекс МП (с 0)
  polygons?: [number, number][][] | null                   // границы всех зон, отредактированные вручную
  assign?: Record<string, number>                          // учреждение → индекс МП
}

export interface SuburbCounts { doctors: number; lpu: number; pharmacies: number }

export interface BaseQuery {
  cities: string[] | null
  kinds: ('lpu' | 'pharmacy')[]
  lpu_ownership: Ownership[]
  pharmacy_ownership: Ownership[]
  categories: string[]
  lpu_care?: CareFilter
  specialty: string
  pharmacy_min_turnover?: number | null
  pharmacy_networks?: string[] | null
  only_target?: boolean
  scope?: 'city' | 'suburbs' | 'region'
  client_base?: string | null
  regions?: string[] | null
}

export interface BaseExportQuery extends BaseQuery {
  doctor_specialties?: string[]
}

export interface BaseRow {
  city: string
  region: string
  lpu?: number
  lpu_state?: number
  lpu_private?: number
  polyclinic_adult?: number
  polyclinic_child?: number
  hospital?: number
  private_center?: number
  cardio_center?: number
  womens?: number
  dental?: number
  doctors?: number
  pharmacies?: number
  ph_state?: number
  ph_private?: number
  top_networks: string[]
  slug?: string
  center?: [number, number] | null
  population?: number | null
  turnover_median?: number | null
  ph_per_10k?: number | null
  doctors_per_10k?: number | null
  outside?: number
  cities_n?: number | null
}

export interface BaseSummary {
  rows: BaseRow[]
  total: Omit<BaseRow, 'city' | 'region' | 'top_networks'> & { settlements?: number }
  networks: [string, number][]
  all_networks: string[]
}

export interface FacilityCard {
  care_level?: CareLevel
  id: string
  name: string
  kind: 'lpu' | 'pharmacy'
  category: string
  category_label: string
  address: string
  lat: number
  lon: number
  phone?: string
  hours?: string
  ownership?: Ownership
  network_name?: string
  operator?: string | null
  inn?: string | null
  turnover?: number | null
  source: string
  doctors: { spec: string; n: number; confirmed: number }[]
}

export type ProjectStatus = 'draft' | 'sent' | 'active'

export interface Zone {
  id: number
  color: string
  polygon: [number, number][]
  center: [number, number]
  weight: number
  doctors: number
  lpu: number
  pharmacies: number
  extension: number
  suburb: SuburbCounts
}

export interface ZonesPreview {
  zones: Zone[]
  weight_step: number
  manual: boolean
  assignment?: Record<string, number>
  points?: [string, number, number, number][]
}

export interface PlanFacility {
  id: string
  name: string
  address: string
  kind: 'lpu' | 'pharmacy'
  category: string
  lat: number
  lon: number
  doctors: number
  visits: { date: string; rep: number; doctors: string[] }[]
  ownership: Ownership
  network: string
}

export interface AdviceParam {
  key: 'doctor_visits_per_day' | 'doctor_frequency' | 'reps' | 'months' | 'pharmacy_frequency'
  label: string
  current: number
  recommended: number
  status: 'ok' | 'warn' | 'bad'
  why: string
}

export interface Scenario {
  title: string
  note: string
  change: Partial<Record<'doctor_visits_per_day' | 'doctor_frequency' | 'reps' | 'months', number>>
  hours: number
  p80: number
  planned: number
  placed: number
  coverage: number
}

export interface Advice {
  ok: boolean
  params: AdviceParam[]
  scenarios: Scenario[]
  curve: { d: number; hours: number; p80: number; placed: number; planned: number; placed_alt?: number }[]
  alt_frequency: number | null
  alt_frequency_month?: number
  model: { per_doctor_min: number; per_pharmacy_min: number; fixed_min: number; p80_factor: number; max_doctor_visits: number; limit_h: number }
}

export interface Plan {
  calculation_id?: string
  request?: PlanRequest
  summary: Summary
  warnings: { level: 'critical' | 'warning' | 'info'; text: string }[]
  reps: RepInfo[]
  advice: Advice | null
  territories: { weight_step: number; manual: boolean; coverage: string }
  days: Day[]
  facilities: PlanFacility[]
}

// ─── Обратный расчёт ─────────────────────────────────────────────────────────

export interface GoalOption {
  reps: number
  d_needed: number
  hours: number
  p80: number
  fits: boolean
  covered_at_user_d: number
  doctor_visits_at_user_d: number
  pharmacies_needed: number
  pharmacies_short: boolean
  p_needed?: number
  ph_covered?: number
  ph_visits?: number
}

export interface GoalResult {
  target: number
  target_effective: number
  available: number
  working_days: number
  waves: number
  frequency: number
  doctor_visits_per_day: number
  pharmacy_visits_per_day: number
  max_doctor_visits: number
  limit_h: number
  reps_at_user_d: number
  reps_at_full_day: number
  full_day_d: number
  per_rep_unique: number
  recommended: { reps: number; doctor_visits_per_day: number; pharmacy_visits_per_day?: number }
  options: GoalOption[]
  notes: string[]
  auto_d?: boolean
  use_doctors?: boolean
  use_pharmacies?: boolean
  ph_target?: number
  ph_target_effective?: number
  ph_available?: number
  ph_waves?: number
  ph_frequency?: number
}

/** Цель охвата в режиме «Есть цель»: врачи и/или аптеки; пустое число — все в базе. */
export interface GoalSpec {
  doctors: boolean
  pharmacies: boolean
  ph: number | null
}

export interface ProjectVariant {
  key: 'asked' | 'recommended' | 'minimal'
  reps: number
  d: number[]
  hours: number
  p80: number
  covered: number
  pct: number
  visits: number
  fits: boolean
  days_to_goal: number | null
  split?: Record<string, [number, number]>
}

// ─── Проекты ─────────────────────────────────────────────────────────────────

export interface ProjectConditions extends Partial<CityNorms> {
  specialty: string
  doctor_visits_per_day: number
  pharmacy_visits_per_day: number
  doctor_frequency: number
  pharmacy_frequency: number
  start_date: string
  months: number
  transport: Transport
  workday_limit_h: number
  coverage: 'spread' | 'compact'
  lpu_ownership: Ownership[]
  pharmacy_ownership: Ownership[]
  categories: string[] | null
  lpu_care?: CareFilter
  extra_specialties?: ExtraSpec[]
  pharmacy_min_turnover?: number | null
  frequency_unit?: 'project_month' | 'calendar_month' | 'month' | 'project'
}

export interface ProjectGoal {
  mode: 'all' | 'total' | 'total_min' | 'per_city' | 'none'
  total_doctors: number | null
}

export interface ProjectCity {
  slug: string
  reps: number | null // null — подобрать под цель
  target?: number | null
}

export interface CityResult {
  slug: string
  city: string
  reps: number
  reps_auto: boolean
  reps_needed: number
  reps_full_day: number
  target: number
  available_doctors: number
  available_pharmacies: number
  covered_doctors: number
  unique_doctors: number
  coverage_pct: number
  doctor_visits: number
  planned_doctor_visits: number
  pharmacy_visits: number
  planned_pharmacy_visits: number
  unique_pharmacies: number
  unique_lpu: number
  avg_hours: number
  avg_p80_hours: number
  risky_days: number
  over_limit_days?: number
  checked_variants?: number
  working_days: number
  max_doctor_visits: number | null
  specialties?: SpecialtyCoverage[]
  status: 'ok' | 'warn' | 'bad'
  variant_full?: { reps: number; d: number; hours: number; p80: number; covered: number; days_to_goal: number }
  issues: { level: 'warn' | 'bad' | 'tip'; kind?: 'coverage' | 'day_limit'; text: string }[]
  fixes: string[]
}

export interface ProjectTotals {
  has_goal?: boolean
  reps: number
  reps_needed: number
  target: number
  available_doctors: number
  covered_doctors: number
  coverage_pct: number
  doctor_visits: number
  planned_doctor_visits: number
  pharmacy_visits: number
  planned_pharmacy_visits: number
  unique_pharmacies: number
  unique_lpu: number
  avg_hours: number
  avg_p80_hours: number
  total_km: number
  working_days: number
  cities_bad: number
  cities_warn: number
  goal_visits?: number
  unallocated_target?: number
  over_limit_days?: number
}

export interface ProjectResult {
  id?: string
  inputs?: { conditions: ProjectConditions }
  computed: string
  stale: boolean
  planner_updated?: boolean
  cities: CityResult[]
  totals: ProjectTotals
  allocation: { reps: number; covered: number; visits: number; split: Record<string, number> }[]
  variants?: ProjectVariant[]
  project_mix?: boolean
}

export interface JobStatus {
  city?: string
  running: boolean
  done: number
  total: number
  current: string
  error: string
}

export interface Project {
  id: string
  name: string
  client: string
  created: string
  updated: string
  conditions: ProjectConditions
  goal: ProjectGoal
  cities: ProjectCity[]
  result: ProjectResult | null
  job?: JobStatus
  status?: ProjectStatus
  plans?: Record<string, PlanRequest>
}

export interface ProjectItem {
  id: string
  name: string
  client: string
  updated: string
  cities: number
  specialty: string
  specialties?: string[]
  reps: number | null
  computed: boolean
  status?: ProjectStatus
}


// ─── Данные визитов ──────────────────────────────────────────────────────────

export interface FieldImport {
  key: string
  title: string
  created: string
  facilities: number
  doctors: number
  cities: number
  matched: number
  added: number
  not_found: number
  skipped_cities: string[]
  outside?: number
}

export interface FieldJob {
  running: boolean
  stage: string
  done: number
  total: number
  error: string
  key: string
}

// ─── Статистика города ───────────────────────────────────────────────────────

export interface CityStats {
  slug: string
  name: string
  region: string
  population: number | null
  updated: string
  lpu: { total: number; state: number; private: number; by_category: Record<string, number>; sources: Record<string, number>; confirmed: number }
  pharmacies: {
    total: number; state: number; private: number
    networks: { name: string; n: number }[]
    other_networks: number; singles: number; network_count: number
    sources: { map_and_registry: number; registry_only: number; map_only: number; not_in_registry: number }
    turnover_median: number | null; per_10k: number | null
  }
  doctors: { spec: string; n: number; confirmed: number }[]
  lpu_per_100k: number | null
}

export interface ExtraSpec {
  specialty: string
  doctor_visits_per_day: number
  doctor_frequency: number
}

export type DoctorMethod = 'city' | 'prodoctorov' | 'density' | 'region' | 'estimate'

// ─── Базы заказчиков ─────────────────────────────────────────────────────────

export interface ClientBaseItem {
  id: string
  name: string
  project_id: string | null
  created: string
  rows?: number
  cities?: number
  matched?: number
  new?: number
  outside?: number
  not_found?: number
  pending?: number
  accepted?: number
  rejected?: number
  with_doctors?: number
  job: { running: boolean; stage?: string; done?: number; total?: number; error?: string }
}

export interface ClientNew {
  id: string
  name: string
  address: string
  city: string | null
  city_name: string
  match: 'новое' | 'вне городов'
  review: 'pending' | 'accepted' | 'rejected'
  lat: number | null
  lon: number | null
  counts: Record<string, number>
  near?: { name: string; address: string; m: number; lat: number; lon: number }[]
}

export interface ClientReport extends ClientBaseItem {
  new_items: ClientNew[]
  matched_items: { id: string; name: string; osm_name: string; match: string; address: string; city_name: string }[]
  not_found_items: { id: string; name: string; address: string; city_name: string }[]
  doctor_diffs: { id: string; name: string; osm_name: string; city_name: string; spec: string; client: number; ours: number; accepted: boolean }[]
  missing: { city_name: string; name: string; address: string }[]
  skipped_cities: string[]
}
