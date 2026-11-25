import { useEffect, useRef, useState } from 'react'
import type { BaseExportQuery } from '../types'

interface Props {
  query: BaseExportQuery
  specialties: string[]
  onDownload: (query: BaseExportQuery) => void
  onClose: () => void
}

export function BaseExportDialog({ query, specialties, onDownload, onClose }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [kinds, setKinds] = useState(query.kinds)
  const [selected, setSelected] = useState(() => specialties.filter((s) => s === query.specialty))
  const showDoctors = kinds.includes('lpu')
  useEffect(() => { if (!dialog.current?.open) dialog.current?.showModal() }, [])

  return (
    <dialog ref={dialog} className="base-export-dialog" aria-labelledby="base-export-title"
      onCancel={(e) => { e.preventDefault(); onClose() }}>
      <form onSubmit={(e) => {
        e.preventDefault()
        if (kinds.length) onDownload({ ...query, kinds, doctor_specialties: showDoctors ? specialties.filter((s) => selected.includes(s)) : [] })
      }}>
        <h2 id="base-export-title">Выгрузка в Excel</h2>
        <fieldset className="base-export-kinds">
          <legend>Учреждения</legend>
          {query.kinds.map((kind) => (
            <label className="chk" key={kind}>
              <input type="checkbox" checked={kinds.includes(kind)} onChange={(e) =>
                setKinds((current) => e.target.checked ? [...current, kind] : current.filter((k) => k !== kind))} />
              {kind === 'lpu' ? 'ЛПУ' : 'Аптеки'}
            </label>
          ))}
        </fieldset>
        {showDoctors && (
          <fieldset>
            <legend>Число врачей по специальностям</legend>
            <div className="base-export-controls">
              <button type="button" className="linklike" onClick={() => setSelected(specialties)}>Все специальности</button>
              <button type="button" className="linklike" onClick={() => setSelected([])}>Без врачей</button>
            </div>
            <div className="base-export-specialties">
              {specialties.map((s) => (
                <label className="chk" key={s}>
                  <input type="checkbox" checked={selected.includes(s)} onChange={(e) =>
                    setSelected((current) => e.target.checked ? [...current, s] : current.filter((item) => item !== s))} />
                  {s}
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <div className="base-export-controls base-export-actions">
          <button type="button" className="btn quiet" onClick={onClose}>Отмена</button>
          <button type="submit" className="btn primary" disabled={!kinds.length}>Скачать Excel</button>
        </div>
      </form>
    </dialog>
  )
}
