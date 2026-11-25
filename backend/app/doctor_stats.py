"""
Статистика врачей по специальностям (файл заказчика «Статистика_врачи_РФ.xlsx»).

В файле — сводные таблицы: число врачей по регионам РФ и отдельно по городам-миллионникам
и городам 500+ тыс. Здесь они превращаются в целевое число врачей специальности для города:

  * город есть в таблицах городов — берётся его число;
  * иначе — число по региону минус известные крупные города региона, а остаток делится
    между загруженными городами региона пропорционально их «ёмкости» (оценке по типам
    учреждений). Доля REGION_URBAN_SHARE остатка считается приходящейся на города
    от 50 тыс., остальное — на мелкие населённые пункты.

Затем целевое число распределяется по учреждениям города пропорционально весам типов
(поликлиника, больница, медцентр…) — так сумма по городу совпадает со статистикой.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .facilities import DATA_DIR

SOURCE = DATA_DIR / "source" / "Статистика_врачи_РФ.xlsx"
STATS_JSON = DATA_DIR / "doctor_stats.json"
REGION_URBAN_SHARE = 0.8

# Специальность в планировщике → столбцы статистики (взрослые и детские врачи вместе)
SPEC_COLUMNS = {
    "Кардиолог": ["Кардиология"],
    "Терапевт": ["Терапия", "Общая практика"],
    "Педиатр": ["Педиатрия", "Подростковая медицина (Педиатрия)"],
    "Невролог": ["Неврология", "Неврология сосудистая", "Детской неврология"],
    "Эндокринолог": ["Эндоскопия, Диабет & Метаболизм", "Детская эндокринология"],
    "ЛОР": ["Отоларингология", "Детская оториноларингология"],
    "Гастроэнтеролог": ["Гастроэнтерология", "Детская гастроэнтерология"],
    "Гинеколог": ["Акушерство & Гинекология", "Детская гинекология"],
    "Уролог": ["Урология", "Андрология (Мужское здоровье)", "Детская урология"],
    "Офтальмолог": ["Офтальмология", "Детская офтальмология"],
    "Дерматолог": ["Дерматология", "Детская дерматология"],
    "Пульмонолог": ["Пульмонология", "Детская пульмонология"],
    "Аллерголог-иммунолог": ["Аллергология", "Иммунология", "Клиническая и лабораторная иммунология",
                             "Детская аллергология"],
    "Ревматолог": ["Ревматология", "Детская ревматология"],
    "Хирург": ["Общая хирургия", "Детская хирургия"],
    "Онколог": ["Клиническая онкология", "Медицинская онкология", "Онкохирургия"],
    "Стоматолог": ["Стоматология", "Терапевтическая стоматология", "Ортопедическая стоматология",
                   "Детская стоматология", "Ортодонтия", "Пародонтология"],
    "Травматолог-ортопед": ["Ортопедия и ортопедическая хирургия, Ревматология", "Детская ортопедия"],
    "Нефролог": ["Нефрология", "Детская нефрология"],
    "Гематолог": ["Гематология (Внутренняя медицина)", "Детская внутренняя медицина_ Детская гематология"],
    "Инфекционист": ["Инфекционные болезни", "Детские инфекционные болезни"],
    "Колопроктолог": ["Проктология"],
    "Сосудистый хирург": ["Ангиохирургия", "Ангиология"],
    "Нейрохирург": ["Неврологическая хирургия", "Детская хирургия_Детская нейрохирургия"],
}

# Регион в справочнике городов (OSM) → строка в статистике, где названия расходятся
_REGION_ALIASES = {
    "Чечня": "Чеченская Республика",
    "Чувашия": "Чувашская Республика - Чувашия",
    "Удмуртия": "Удмуртская Республика",
    "Кабардино-Балкария": "Кабардино-Балкарская Республика",
    "Карачаево-Черкесия": "Карачаево-Черкесская Республика",
    "Республика Саха (Якутия)": "Республика Саха",
    "Северная Осетия — Алания": "Республика Северная Осетия-Алания",
    "Ханты-Мансийский автономный округ — Югра": "Ханты-Мансийский автономный округ - Югра",
}


def build_from_xlsx(path: Path = SOURCE) -> dict:
    """Разбирает сводные таблицы (первая строка с «Названия строк» — заголовок)."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    out = {"source": path.name, "regions": {}, "cities": {}}
    for ws in wb.worksheets:
        title = ws.title.lower()
        if "детск" in title:
            continue  # детские специальности уже есть в общей таблице регионов
        target = "cities" if "город" in title else "regions"
        header = None
        for row in ws.iter_rows(values_only=True):
            if header is None:
                if row and row[0] in ("Названия строк", "Область"):
                    header = [str(c).strip() if c else "" for c in row]
                continue
            name = (row[0] or "").strip() if isinstance(row[0], str) else row[0]
            if not name or name in ("Общий итог",) or str(name).lower().startswith("не "):
                continue
            vals = {header[i]: int(v) for i, v in enumerate(row) if i and v and header[i] and header[i] != "Общий итог"}
            out[target][str(name).strip()] = vals
    STATS_JSON.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    stats.cache_clear()
    return out


@lru_cache(maxsize=1)
def stats() -> dict | None:
    if STATS_JSON.exists():
        return json.loads(STATS_JSON.read_text(encoding="utf-8"))
    if SOURCE.exists():
        return build_from_xlsx()
    return None


def _count(row: dict | None, spec: str) -> int | None:
    if row is None or spec not in SPEC_COLUMNS:
        return None
    return sum(row.get(c, 0) for c in SPEC_COLUMNS[spec])


def region_row(region: str) -> dict | None:
    s = stats()
    if not s or not region:
        return None
    regs = s["regions"]
    for cand in (region, _REGION_ALIASES.get(region), f"Республика {region}"):
        if cand and cand in regs:
            return regs[cand]
    norm = lambda x: re.sub(r"[^а-яё]", "", x.lower())
    for k, v in regs.items():
        if norm(k) == norm(region):
            return v
    return None


def city_row(name: str) -> dict | None:
    s = stats()
    return s["cities"].get(name) if s else None


# ─── Калибровка числа врачей по городу ──────────────────────────────────────

_cal_cache: dict = {}


def _weights_of(slug: str, spec: str, facilities: list[dict] | None = None) -> tuple[float, dict]:
    """Сумма весов типов учреждений города и сами веса (кеш по времени изменения файла)."""
    from . import facilities as F
    from .networks import annotate
    from .norms import base_weight
    key = None
    if facilities is None:
        p = F.city_path(slug)
        if not p.exists():
            return 0.0, {}
        key = (slug, spec, p.stat().st_mtime)
        if key in _cal_cache:
            return _cal_cache[key]
        facilities = annotate([f for f in F.load_city(slug)["facilities"] if f["kind"] == "lpu"])
    # больницы и поликлиники получают хотя бы вес 1 по любой специальности — иначе врачи города
    # уходят в одно случайное учреждение, если нужного типа (детской поликлиники) на карте нет
    w = {f["id"]: base_weight(f, spec) or (1 if f["category"] in ("hospital", "polyclinic_adult", "polyclinic_child")
                                              and not F.excluded_name(f["name"]) else 0)
         for f in facilities if f["kind"] == "lpu"}
    res = (float(sum(w.values())), w)
    if key is not None:
        _cal_cache[key] = res
    return res


def _target_raw(slug: str, spec: str) -> tuple[int | None, str]:
    """Сколько врачей специальности в городе и откуда цифра. Источники по порядку:

    1. «city» — своя строка в статистике врачей РФ (миллионники и города от 500 тыс.);
    2. «prodoctorov» — ПроДокторов по городу, приведённый к масштабу статистики делением на
       медианное отношение «ПроДокторов / статистика» для этой специальности;
    3. «model» — регрессия по городам, где число врачей известно (п. 1–2):
       ln(врачей) = β0 + β1·ln(население) + β2·[областной центр] + β3·ln(поликлиник и больниц + 1)
                    + β4·ln(медцентров + 1) + β5·ln(аптек по реестру + 1), своя для каждой специальности;
       «density» — население × плотность врачей, если модель построить нельзя;
    4. «region» — запасной вариант: доля остатка статистики региона по численности населения.
    """
    from .cities import index
    from .doctor_sources import density, model_estimate, pd_estimate
    rows = index()
    me = next((r for r in rows if r["slug"] == slug), None)
    if not me or not stats() or spec not in SPEC_COLUMNS:
        return None, "estimate"
    if city_row(me["name"]):
        return _count(city_row(me["name"]), spec), "city"
    own_pd = pd_estimate(me["name"], spec)
    if own_pd is not None:
        return own_pd, "prodoctorov"
    est = model_estimate(slug, spec)
    if est is not None:
        return est, "model"
    dens = density(spec, me["region"])
    if dens and me.get("population"):
        return int(round(me["population"] * dens[0])), "density"
    reg = region_row(me["region"])
    if reg is None:
        return None, "estimate"
    same = [r for r in rows if r["region"] == me["region"]]
    known = sum(_count(city_row(r["name"]), spec) or 0 for r in same if city_row(r["name"]))
    rest = max(0, _count(reg, spec) - known) * REGION_URBAN_SHARE
    others = [r for r in same if not city_row(r["name"])]
    p_me = me.get("population") or 0
    p_all = sum(r.get("population") or 0 for r in others)
    if not p_me or not p_all:
        return None, "estimate"
    return int(round(rest * p_me / p_all)), "region"


# Пропорция гинекологов и педиатров. В исходной статистике графа «Акушерство и гинекология» включает
# акушеров роддомов и перинатальных центров, а участковые педиатры учтены не полностью: гинекологов
# там больше, чем педиатров (Москва 7 275 / 6 502), хотя по ПроДокторов и по данным Минздрава
# педиатров заметно больше. Сумма «гинекологи + педиатры» в городе сохраняется, а делится она
# в пропорции ПроДокторов: по самому городу, если он есть на сайте, иначе медианной (≈0,73).
_GYN, _PED = "Гинеколог", "Педиатр"


def _gyn_ped_ratio(name: str) -> float:
    from .doctor_sources import _norm, _pd
    import statistics as _st
    v = _pd().get(_norm(name))
    if v and v.get(_PED) and v.get(_GYN):
        r = v[_GYN] / v[_PED]
    else:
        all_r = [x[_GYN] / x[_PED] for x in _pd().values() if x.get(_PED) and x.get(_GYN)]
        r = _st.median(all_r) if all_r else 0.75
    return min(1.2, max(0.5, r))


def target(slug: str, spec: str) -> tuple[int | None, str]:
    """Сколько врачей специальности в городе (см. _target_raw); для гинекологов и педиатров —
    с поправкой их пропорции по ПроДокторов."""
    if spec not in (_GYN, _PED):
        return _target_raw(slug, spec)
    g, mg = _target_raw(slug, _GYN)
    pdd, mp = _target_raw(slug, _PED)
    if not g or not pdd:
        return _target_raw(slug, spec)
    from .cities import entry
    e = entry(slug) or {}
    r = _gyn_ped_ratio(e.get("name", ""))
    total = g + pdd
    return (int(round(total * r / (1 + r))), mg) if spec == _GYN else (int(round(total / (1 + r))), mp)


def calibration(slug: str, spec: str, *, facilities: list[dict] | None = None) -> dict:
    """Число врачей по учреждениям города: статистика, разложенная по весам типов.

    facilities — уже классифицированные учреждения города, если они загружены вызывающим кодом.
    {"counts": {id: n} | None, "scale": коэффициент, "target": врачей в городе, "method", "base"}.
    """
    total, method = target(slug, spec)
    base, w = _weights_of(slug, spec, facilities)
    if total is None or not base:
        return {"counts": None, "scale": 1.0, "target": int(base), "method": "estimate", "base": int(base)}
    scale = total / base
    raw = {k: v * scale for k, v in w.items() if v}
    counts = {k: int(x) for k, x in raw.items()}
    # метод наибольших остатков — сумма по городу точно равна статистике
    left = total - sum(counts.values())
    for k in sorted(raw, key=lambda k: raw[k] - int(raw[k]), reverse=True)[:max(0, left)]:
        counts[k] += 1
    for k in w:
        counts.setdefault(k, 0)
    return {"counts": counts, "scale": scale, "target": total, "method": method, "base": int(base)}
