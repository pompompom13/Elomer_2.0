import { useEffect, useRef, useState, type ReactNode } from 'react'

/** Кнопка-фильтр в строке фильтров: подпись, краткое значение и всплывающая панель. */
export function Drop({ label, value, children, wide, active }: { label: string; value: string; children: ReactNode; wide?: boolean; active?: boolean }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc) }
  }, [open])
  return (
    <div className="drop" ref={ref}>
      <button type="button" className={`drop-btn${active ? ' on' : ''}`} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <small>{label}</small><span title={value}>{value}</span>
      </button>
      {open && (
        <div className={`drop-pop${wide ? ' wide' : ''}`}>
          {children}
          <button type="button" className="btn primary drop-done" onClick={() => setOpen(false)}>Готово</button>
        </div>
      )}
    </div>
  )
}
