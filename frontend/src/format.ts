const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const WEEKDAYS_FULL = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота']

export const parseDate = (s: string) => {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}
export const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

export const longDate = (s: string) => {
  const d = parseDate(s)
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}, ${WEEKDAYS_FULL[d.getDay()]}`
}
export const dateRange = (a: string, b: string) => {
  const x = parseDate(a), y = parseDate(b)
  const sameYear = x.getFullYear() === y.getFullYear()
  return `${x.getDate()} ${MONTHS_GEN[x.getMonth()]}${sameYear ? '' : ' ' + x.getFullYear()} — ${y.getDate()} ${MONTHS_GEN[y.getMonth()]} ${y.getFullYear()}`
}
export const shortMonth = (d: Date) => MONTHS_SHORT[d.getMonth()]

export const hours = (h: number) => {
  const m = Math.round(h * 60)
  return `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, '0')} мин`
}
export const num = (v: number, digits = 1) => v.toLocaleString('ru-RU', { maximumFractionDigits: digits })

export function plural(n: number, one: string, few: string, many: string) {
  const a = Math.abs(n) % 100, b = a % 10
  if (a > 10 && a < 20) return many
  if (b > 1 && b < 5) return few
  if (b === 1) return one
  return many
}

export const toMin = (hm: string) => {
  const [h, m] = hm.split(':').map(Number)
  return h * 60 + m
}

/** Население: «13,3 млн жителей», «467 тыс. жителей». */
export const people = (n: number) =>
  n >= 1_000_000 ? `${num(n / 1_000_000, 1)} млн жителей` : `${num(n / 1000, 0)} тыс. жителей`
