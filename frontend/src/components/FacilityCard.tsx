import { useEffect, useRef, useState } from 'react'
import type { FacilityCard as Card } from '../types'
import { num } from '../format'
import { CARE_LABELS } from './CareLevelField'

interface Props {
  load: () => Promise<Card>
  specialty: string
  editing?: {
    override?: number
    excluded: boolean
    onOverride: (n: number | null) => void
    onExclude: (off: boolean) => void
  }
  onClose: () => void
}

const SRC: Record<string, string> = { osm: 'карта', import: 'импорт', field: 'данные визитов', registry: 'реестр аптек', dsm: 'реестр ЛПУ', client: 'база заказчика' }

/** Карточка учреждения: сведения, врачи по специальностям, правки для плана. */
export function FacilityCard({ load, specialty, editing, onClose }: Props) {
  const [c, setC] = useState<Card | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const close = useRef<HTMLButtonElement>(null)
  useEffect(() => { close.current?.focus() }, [])
  useEffect(() => {
    let active = true
    setC(null)
    setErr(null)
    load().then((card) => { if (active) setC(card) }).catch((e) => { if (active) setErr((e as Error).message) })
    return () => { active = false }
  }, [load])
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])

  const est = c?.doctors.find((d) => d.spec === specialty)?.n ?? 0
  return (
    <aside className="fcard" role="dialog" aria-label="Карточка учреждения">
      <button ref={close} className="fcard-x" onClick={onClose} aria-label="Закрыть">×</button>
      {err && <div className="error">{err}</div>}
      {!c && !err && <div className="loading">Загружаю…</div>}
      {c && (
        <>
          <div className="fcard-kind"><span className={`dot ${c.kind}`} />{c.category_label || (c.kind === 'pharmacy' ? 'Аптека' : 'ЛПУ')}
            {c.ownership && (c.ownership === 'state' ? ' · государственная' : ' · частная')}</div>
          <h2>{c.name}</h2>
          <dl className="fcard-dl">
            {c.care_level && <><dt>Звено ЛПУ</dt><dd>{CARE_LABELS[c.care_level]}</dd></>}
            {c.settlement && <><dt>Нас. пункт</dt><dd>{c.settlement}</dd></>}
            <dt>Адрес</dt><dd>{c.address || '—'}</dd>
            {c.network_name && <><dt>Сеть</dt><dd>{c.network_name}</dd></>}
            {c.operator && <><dt>Юрлицо</dt><dd>{c.operator}{c.inn ? `, ИНН ${c.inn}` : ''}</dd></>}
            {c.turnover ? <><dt>Оборот</dt><dd>{num(c.turnover / 1e6, 1)} млн ₽ в месяц на аптеку</dd></> : null}
            {c.phone && <><dt>Телефон</dt><dd>{c.phone}</dd></>}
            {c.hours && <><dt>Часы работы</dt><dd>{c.hours}</dd></>}
            <dt>Источник</dt><dd>{SRC[c.source] ?? c.source}</dd>
          </dl>

          {editing && <label className="chk fcard-in">
            <input type="checkbox" checked={!editing.excluded} onChange={(e) => editing.onExclude(!e.target.checked)} />
            Учитывать в плане
          </label>}

          {c.kind === 'lpu' && (
            <>
              {specialty && editing && <div className="field fcard-ov">
                <label htmlFor="fc-ov">{specialty}ов для плана</label>
                <input id="fc-ov" type="number" min={0} max={99} value={editing.override ?? est}
                  onChange={(e) => editing.onOverride(e.target.value === '' ? null : Number(e.target.value))} />
                {editing.override !== undefined && editing.override !== est && (
                  <button className="linklike" onClick={() => editing.onOverride(null)}>вернуть оценку ({est})</button>
                )}
              </div>}
              <h3>Врачи по специальностям</h3>
              {c.doctors.length === 0 ? <p className="muted">Оценка — врачей нужных специальностей нет.</p> : (
                <ul className="fcard-docs">
                  {c.doctors.map((d) => (
                    <li key={d.spec} className={d.spec === specialty ? 'hl' : ''}>
                      <span>{d.spec}</span><b>{d.n}</b>
                      {d.confirmed > 0 && <small>{d.confirmed} подтверждено визитами</small>}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
          <a className="linklike fcard-map" target="_blank" rel="noreferrer"
            href={`https://yandex.ru/maps/?pt=${c.lon},${c.lat}&z=17&l=map`}>Открыть на Яндекс Картах</a>
        </>
      )}
    </aside>
  )
}
