import type { GoalResult } from '../types'
import { num, plural } from '../format'

interface Props {
  goal: GoalResult
  busy: boolean
  onApply: (reps: number, d: number, p: number | null) => void
  onClose: () => void
}

const mp = (n: number) => `${n} ${plural(n, 'медпред', 'медпреда', 'медпредов')}`
const visits = (n: number) => `${n} ${plural(n, 'визит', 'визита', 'визитов')}`

/** Обратный расчёт: сколько медпредов и визитов в день нужно для цели охвата врачей и/или аптек. */
export function GoalPanel({ goal: g, busy, onApply, onClose }: Props) {
  const rec = g.recommended
  const months = Math.round((g.working_days / 20.6) * 10) / 10
  // новый расчёт (цель по врачам и/или аптекам) приходит с флагами use_*; старый — только врачи
  const auto = !!g.auto_d
  const useD = auto ? g.use_doctors !== false : true
  const useP = auto ? !!g.use_pharmacies : false
  const recP = rec.pharmacy_visits_per_day ?? g.pharmacy_visits_per_day
  const dayText = [
    useD ? `${visits(rec.doctor_visits_per_day)} к врачам` : '',
    useP ? `${recP} в аптеки` : '',
  ].filter(Boolean).join(' + ')
  const targetText = [
    useD ? `${num(g.target_effective, 0)} врачей × ${g.frequency} ${plural(g.frequency, 'визит', 'визита', 'визитов')}` : '',
    useP ? `${num(g.ph_target_effective ?? 0, 0)} аптек × ${g.ph_frequency ?? 1} ${plural(g.ph_frequency ?? 1, 'визит', 'визита', 'визитов')}` : '',
  ].filter(Boolean).join(', ')

  return (
    <section className="goal">
      <div className="section-head">
        <h2>Подбор под цель</h2>
        <span className="muted">
          {targetText} за {g.working_days} рабочих {plural(g.working_days, 'день', 'дня', 'дней')} (≈ {num(months)} мес.)
        </span>
        <button className="linklike goal-close" onClick={onClose}>Скрыть</button>
      </div>

      <div className="goal-answer">
        {auto ? (
          <div className="goal-best">
            <div className="v">{mp(rec.reps)} × {dayText} в день</div>
            <div className="l">оптимально: наименьшая команда, при которой цель достигается, а день с вероятностью 80% укладывается в {num(g.limit_h)} ч.
              Визиты в день подобраны по специальности, транспорту и нормативам города.</div>
          </div>
        ) : (
          <>
            <div>
              <div className="v">{mp(g.reps_at_user_d)}</div>
              <div className="l">нужно при {visits(g.doctor_visits_per_day)} к врачам в день, как в условиях</div>
            </div>
            <div>
              <div className="v">{mp(rec.reps)} × {rec.doctor_visits_per_day}</div>
              <div className="l">меньше всего медпредов, при котором день укладывается в {num(g.limit_h)} ч</div>
            </div>
          </>
        )}
        {useD && (
          <div>
            <div className="v">до {g.max_doctor_visits}</div>
            <div className="l">визитов к врачам в день помещается в норму при {useP || !auto ? `${auto ? recP : g.pharmacy_visits_per_day} аптеках` : 'дне без аптек'}</div>
          </div>
        )}
      </div>
      {g.notes.length > 0 && (
        <ul className="notes">{g.notes.map((t, i) => <li key={i} className="warning">{t}</li>)}</ul>
      )}

      <div className="table-wrap goal-wrap">
        <table className="grid goal-table">
          <thead>
            <tr>
              <th>Медпредов</th>
              {useD && <th>{auto ? 'Визитов к врачам в день' : 'Визитов к врачам в день для цели'}</th>}
              {useP && <th>Визитов в аптеки в день</th>}
              <th>Длина дня</th>
              {useD && <th>{auto ? 'Врачей охватят' : `Врачей охватят при ${g.doctor_visits_per_day} в день`}</th>}
              {useP && <th>Аптек охватят</th>}
              {!auto && <th>Аптек хватает</th>}
              <th />
            </tr>
          </thead>
          <tbody>
            {g.options.map((o) => {
              const isRec = o.reps === rec.reps
              const isUser = o.reps === g.reps_at_user_d
              return (
                <tr key={o.reps} className={isRec ? 'rec' : ''}>
                  <td>
                    <b>{o.reps}</b>
                    {isRec && <span className="tag ok">оптимально</span>}
                    {isUser && !isRec && !auto && <span className="tag">при ваших условиях</span>}
                  </td>
                  {useD && <td>{o.d_needed}</td>}
                  {useP && <td>{o.p_needed ?? 0}</td>}
                  <td className={o.fits ? '' : 'bad'}>
                    {num(o.hours)} ч, до {num(o.p80)} ч{o.fits ? '' : ' — больше нормы'}
                  </td>
                  {useD && (
                    <td>
                      {num(o.covered_at_user_d, 0)} из {num(g.target_effective, 0)}
                      <span className="muted"> · {num(o.doctor_visits_at_user_d, 0)} визитов</span>
                    </td>
                  )}
                  {useP && (
                    <td>
                      {num(o.ph_covered ?? 0, 0)} из {num(g.ph_target_effective ?? 0, 0)}
                      <span className="muted"> · {num(o.ph_visits ?? 0, 0)} визитов</span>
                    </td>
                  )}
                  {!auto && <td className={o.pharmacies_short ? 'bad' : ''}>{o.pharmacies_short ? `нет, нужно ${num(o.pharmacies_needed, 0)}` : 'да'}</td>}
                  <td className="goal-act">
                    <button className="btn quiet small" disabled={busy}
                      onClick={() => onApply(o.reps, useD ? Math.max(1, o.d_needed) : 0, auto ? (useP ? Math.max(1, o.p_needed ?? 0) : 0) : null)}>
                      Построить план</button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="muted goal-foot">
        {useD && <>Один медпред за проект охватывает {num(g.waves, 0)} × визиты в день уникальных врачей: {g.working_days} рабочих
          {' '}{plural(g.working_days, 'день', 'дня', 'дней')} делятся на {g.frequency} {plural(g.frequency, 'круг', 'круга', 'кругов')} визитов
          {' '}по {num(g.waves, 0)} дней. </>}
        {useP && <>Аптек — {num(g.ph_waves ?? 0, 0)} × визиты в аптеки в день. </>}
        Длина дня — по модели, откалиброванной на маршрутах этого города.
      </p>
    </section>
  )
}
