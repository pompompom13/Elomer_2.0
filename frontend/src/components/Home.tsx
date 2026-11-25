import { useEffect, useState, type ReactNode } from 'react'
import { api } from '../api'
import type { CityItem, ProjectItem } from '../types'
import { num, plural } from '../format'
import { store } from '../store'

export type Tab = 'home' | 'base' | 'projects' | 'plan' | 'data'

interface Props {
  cities: CityItem[]
  lastCity: string
  onGo: (tab: Tab) => void
  onOpenProject: (id: string) => void
}

export const PROJECT_STATUS: Record<string, string> = { draft: 'черновик', sent: 'отправлен заказчику', active: 'в работе' }

// простые линейные иконки, цвет — от текста
const svg = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>
)
const ICONS: Record<string, ReactNode> = {
  base: svg(<><ellipse cx="12" cy="5.5" rx="7.5" ry="2.8" /><path d="M4.5 5.5v6.5c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8V5.5" /><path d="M4.5 12v6.5c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8V12" /></>),
  projects: svg(<><rect x="3.5" y="7" width="17" height="12.5" rx="2" /><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7" /><path d="M3.5 12.5h17" /></>),
  plan: svg(<><circle cx="6" cy="18" r="2" /><circle cx="18" cy="6" r="2" /><path d="M8 18h6.5a3.5 3.5 0 0 0 0-7h-5a3.5 3.5 0 0 1 0-7H16" /></>),
}

const SCEN: { tab: Tab; title: string; text: string; go: string }[] = [
  { tab: 'base', title: 'Посмотреть рынок и выгрузить базу', go: 'Открыть базу',
    text: 'Сколько ЛПУ, врачей и аптек в городах России, карта, сравнение городов, выгрузка в Excel по фильтрам.' },
  { tab: 'projects', title: 'Подготовить предложение заказчику', go: 'К проектам',
    text: 'Несколько городов, специальности, цель по врачам и срок: сколько нужно медпредов, что выполнимо, PDF для заказчика.' },
  { tab: 'plan', title: 'Спланировать работу в городе', go: 'К плану города',
    text: 'Зоны медпредов на карте, маршруты и календарь визитов по дням, маршрутные листы на неделю.' },
]

/** Стартовый экран: сводка базы, три сценария и последние проекты. */
export function Home({ cities, lastCity, onGo, onOpenProject }: Props) {
  const [list, setList] = useState<ProjectItem[]>([])
  useEffect(() => { api.projects().then(setList).catch(() => undefined) }, [])
  // те же итоги, что в «Базе» без фильтров: все ЛПУ и аптеки областей целиком (города, пригороды, остальная территория)
  // последние итоги помнятся в браузере и показываются сразу; сервер отдаёт их из сохранённой сводки
  const [tot, setTot] = useState<{ lpu: number; pharmacies: number } | null>(() => store.get('elomer.home.totals', null))
  useEffect(() => {
    let alive = true, tries = 0
    const load = () => api.baseTotals().then((t) => {
      if (!alive) return
      if (t.lpu != null && t.pharmacies != null) {
        const v = { lpu: t.lpu, pharmacies: t.pharmacies }
        setTot(v)
        store.set('elomer.home.totals', v)
      }
      if (!t.fresh && ++tries < 30) timer = setTimeout(load, 10000)   // базы менялись — сводка досчитывается в фоне
    }).catch(() => undefined)
    let timer = setTimeout(load, 0)
    return () => { alive = false; clearTimeout(timer) }
  }, [])
  const loaded = cities.filter((c) => c.loaded)
  const city = cities.find((c) => c.slug === lastCity)

  return (
    <div className="home">
      <section className="home-hero">
        <div>
          <h1>Что нужно сделать?</h1>
          <p>ЭЛОМЕР считает, сколько медпредов нужно на проект, делит город на зоны и строит маршруты визитов
            по реальным ЛПУ и аптекам.</p>
        </div>
        <div className="home-stats">
          <div><b>{loaded.length}</b><span>{plural(loaded.length, 'город', 'города', 'городов')} в базе</span></div>
          <div><b>{tot ? num(tot.lpu, 0) : '…'}</b><span>ЛПУ</span></div>
          <div><b>{tot ? num(tot.pharmacies, 0) : '…'}</b><span>аптек</span></div>
        </div>
      </section>

      <div className="scen-grid">
        {SCEN.map((s) => (
          <button key={s.tab} className="scen-card" onClick={() => onGo(s.tab)}>
            <span className="scen-ic">{ICONS[s.tab]}</span>
            <b>{s.title}</b>
            <span>{s.text}</span>
            <em>{s.go} →</em>
          </button>
        ))}
      </div>

      <div className="home-row">
        <section>
          <h2>Последние проекты</h2>
          {list.length === 0 ? (
            <p className="muted">Проектов пока нет. <button className="linklike" onClick={() => onGo('projects')}>Создать первый</button></p>
          ) : (
            <ul className="home-proj">
              {list.slice(0, 6).map((p) => (
                <li key={p.id}>
                  <button className="rowlink" onClick={() => onOpenProject(p.id)}><b>{p.name}</b></button>
                  <span className="muted">
                    {[p.client, `${p.cities} ${plural(p.cities, 'город', 'города', 'городов')}`, PROJECT_STATUS[p.status ?? 'draft'], p.updated].filter(Boolean).join(' · ')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section>
          <h2>Продолжить</h2>
          <ul className="home-proj">
            {city && <li><button className="rowlink" onClick={() => onGo('plan')}><b>План города: {city.name}</b></button><span className="muted">последний открытый город</span></li>}
            <li><button className="rowlink" onClick={() => onGo('data')}><b>Данные</b></button>
              <span className="muted">загружено {loaded.length} из {cities.length} городов; данные визитов, своя база</span></li>
          </ul>
        </section>
      </div>
    </div>
  )
}
