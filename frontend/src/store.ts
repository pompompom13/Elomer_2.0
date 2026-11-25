/** Локальное хранилище настроек и правок; ошибки (приватный режим) молча игнорируются. */
export const store = {
  get<T>(k: string, fallback: T): T {
    try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : fallback } catch { return fallback }
  },
  set(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* приватный режим */ } },
}

export type Edits = { overrides: Record<string, number>; excluded: string[] }
export const EMPTY_EDITS: Edits = { overrides: {}, excluded: [] }

/** Правки базы города (число врачей, исключённые учреждения) — общие для «Базы» и «Плана города». */
export const editsKey = (city: string, spec: string) => `elomer.edits.${city}.${spec}`
export const loadEdits = (city: string, spec: string): Edits => store.get(editsKey(city, spec), EMPTY_EDITS)
export function saveEdits(city: string, spec: string, e: Edits) {
  store.set(editsKey(city, spec), e)
  window.dispatchEvent(new CustomEvent('elomer-edits', { detail: { city, spec } }))
}
export function patchEdits(e: Edits, id: string, change: { override?: number | null; off?: boolean }): Edits {
  const o = { ...e.overrides }
  let ex = e.excluded
  if (change.override !== undefined) { if (change.override === null) delete o[id]; else o[id] = change.override }
  if (change.off !== undefined) ex = change.off ? [...ex.filter((x) => x !== id), id] : ex.filter((x) => x !== id)
  return { overrides: o, excluded: ex }
}
