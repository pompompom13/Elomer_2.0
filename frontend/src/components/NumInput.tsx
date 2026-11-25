import { useEffect, useRef, useState, type InputHTMLAttributes } from 'react'

type Base = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type' | 'min' | 'max'>

interface Props extends Base {
  value: number | null | undefined
  /** Вызывается только с допустимым числом (или null, если поле можно оставить пустым). */
  onChange: (v: number | null) => void
  min?: number
  max?: number
  /** Пустое поле — допустимое значение (null): «авто», «все», «по нормативу». */
  allowEmpty?: boolean
  /** Целые числа (по умолчанию) или дробные (норма дня 7,5 ч). */
  decimal?: boolean
}

/**
 * Числовое поле, которое можно стереть целиком и вписать своё: пока человек печатает, пустое
 * или неполное значение не подменяется минимумом. При уходе из поля недопустимое значение
 * возвращается к последнему верному (или к пустому, если пустое разрешено).
 */
export function NumInput({ value, onChange, min, max, allowEmpty = false, decimal = false, onBlur, onFocus, ...rest }: Props) {
  const show = (v: number | null | undefined) => (v === null || v === undefined ? '' : String(v))
  const [text, setText] = useState(show(value))
  const focused = useRef(false)
  // значение поменялось снаружи (сброс, пересчёт) — показываем его, если человек сейчас не печатает
  useEffect(() => { if (!focused.current) setText(show(value)) }, [value])

  const parse = (t: string): number | null => {
    const s = t.trim().replace(',', '.')
    if (s === '') return null
    const n = decimal ? Number(s) : Number.parseInt(s, 10)
    if (!Number.isFinite(n) || (!decimal && !/^-?\d+$/.test(s))) return NaN
    return n
  }
  const valid = (n: number) => (min === undefined || n >= min) && (max === undefined || n <= max)

  return (
    <input {...rest} type="number" inputMode={decimal ? 'decimal' : 'numeric'} min={min} max={max} value={text}
      onFocus={(e) => { focused.current = true; onFocus?.(e) }}
      onChange={(e) => {
        const t = e.target.value
        setText(t)
        const n = parse(t)
        if (n === null) { if (allowEmpty) onChange(null); return }
        if (!Number.isNaN(n) && valid(n)) onChange(n)
      }}
      onBlur={(e) => {
        focused.current = false
        const n = parse(text)
        if (n === null && allowEmpty) setText('')
        else if (n === null || Number.isNaN(n)) setText(show(value))
        else {
          const c = Math.min(max ?? n, Math.max(min ?? n, n))   // больше максимума — до максимума
          if (c !== value) onChange(c)
          setText(show(c))
        }
        onBlur?.(e)
      }} />
  )
}
