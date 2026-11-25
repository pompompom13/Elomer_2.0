"""
Дополнительные источники числа врачей: ПроДокторов (по городам) и база OBRFM (по регионам).

ПроДокторов — сколько врачей каждой специальности указано на сайте по городу (собрано вручную
отдельным сборщиком, data/source/prodoctorov.json). Сайт стабильно показывает больше врачей, чем
официальная статистика: на 36 городах, где есть оба источника, отношение по каждой специальности
держится в узком коридоре (гинекологи ×1,0 … аллергологи ×1,9). Поэтому цифра ПроДокторов
переводится в масштаб статистики делением на медианный коэффициент своей специальности.

OBRFM — сколько врачей специальности есть в контактной базе компании по региону
(data/source/obrfm.json). Это подмножество врачей региона (4–50% от статистики), поэтому
в расчёт числа врачей не входит; показывается как отдельный показатель — охват базы контактов.
"""
from __future__ import annotations

import json
import math
import statistics
from functools import lru_cache

from .facilities import DATA_DIR

PD_PATH = DATA_DIR / "source" / "prodoctorov.json"
OB_PATH = DATA_DIR / "source" / "obrfm.json"

# специальности ПроДокторов → наши: внутри группы берётся максимум (один врач бывает и аллергологом,
# и иммунологом), группы складываются (взрослые + детские — разные врачи)
PD_MAP = {
    "Кардиолог": [["Кардиолог"], ["Детский кардиолог"]],
    "Терапевт": [["Терапевт"], ["Врач общей практики (семейный врач)"]],
    "Педиатр": [["Педиатр"]],
    "Невролог": [["Невролог"], ["Детский невролог"]],
    "Эндокринолог": [["Эндокринолог"], ["Детский эндокринолог"]],
    "ЛОР": [["ЛОР"], ["Детский ЛОР"]],
    "Гастроэнтеролог": [["Гастроэнтеролог"], ["Детский гастроэнтеролог"]],
    "Гинеколог": [["Гинеколог"], ["Детский гинеколог"]],
    "Уролог": [["Уролог"], ["Детский уролог"]],
    "Офтальмолог": [["Офтальмолог (окулист)"], ["Детский офтальмолог"]],
    "Дерматолог": [["Дерматолог", "Дерматовенеролог"], ["Детский дерматолог"]],
    "Пульмонолог": [["Пульмонолог"], ["Детский пульмонолог"]],
    "Аллерголог-иммунолог": [["Аллерголог", "Иммунолог"], ["Детский аллерголог", "Детский иммунолог"]],
    "Ревматолог": [["Ревматолог"], ["Детский ревматолог"]],
    "Хирург": [["Хирург"], ["Детский хирург"]],
    "Онколог": [["Онколог"], ["Детский онколог"]],
    "Стоматолог": [["Стоматолог"], ["Детский стоматолог"]],
    "Травматолог-ортопед": [["Травматолог", "Ортопед"], ["Детский ортопед", "Детский травматолог"]],
    "Нефролог": [["Нефролог"], ["Детский нефролог"]],
    "Гематолог": [["Гематолог"], ["Детский гематолог"]],
    "Инфекционист": [["Инфекционист"], ["Детский инфекционист"]],
    "Колопроктолог": [["Проктолог (колопроктолог)"]],
    "Сосудистый хирург": [["Сосудистый хирург (ангиохирург)", "Флеболог"]],
    "Нейрохирург": [["Нейрохирург"]],
}

OB_SHEETS = {"Кардиолог": "Кардиологи", "Терапевт": "Терапевты", "Педиатр": "Педиатры", "Невролог": "Неврологи",
             "Эндокринолог": "Эндокринологи", "ЛОР": "ЛОР", "Гастроэнтеролог": "Гастро", "Гинеколог": "Гинекологи",
             "Уролог": "Урологи", "Офтальмолог": "Офтальмологи", "Дерматолог": "Дерматологи",
             "Пульмонолог": "Пульмонологи", "Аллерголог-иммунолог": "Аллергологи", "Ревматолог": "Ревматологи",
             "Хирург": "Хирурги", "Онколог": "Онкологи"}


def _norm(s: str) -> str:
    return str(s or "").strip().lower().replace("ё", "е")


# ─── Загрузка исходных файлов (один раз) ─────────────────────────────────────

def import_prodoctorov(paths: list[str]) -> dict:
    """JSON-файлы сборщика ({город: {специальность сайта: число}}) → data/source/prodoctorov.json.
    Позже указанный файл перекрывает раньше указанный по тому же городу."""
    raw = {}
    for p in paths:
        for city, v in json.load(open(p, encoding="utf-8")).items():
            if v:
                raw[city] = v
    data = {"cities": {c: {s: pd_count(v, s) for s in PD_MAP} for c, v in raw.items()}, "raw": raw}
    PD_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _pd.cache_clear()
    return {"cities": len(raw)}


def import_obrfm(path: str) -> dict:
    """Выгрузка OBRFM (лист на специальность, строки — регионы) → data/source/obrfm.json."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: dict[str, dict] = {}
    for spec, sheet in OB_SHEETS.items():
        if sheet not in wb.sheetnames:
            continue
        for row in list(wb[sheet].iter_rows(values_only=True))[2:]:
            if row[0] and row[1] is not None and "ФО" not in str(row[0]):
                out.setdefault(str(row[0]).strip(), {})[spec] = int(row[1])
    OB_PATH.write_text(json.dumps({"regions": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    _ob.cache_clear()
    return {"regions": len(out)}


def pd_count(site: dict, spec: str) -> int:
    return sum(max((int(site.get(k, 0) or 0) for k in grp), default=0) for grp in PD_MAP[spec])


@lru_cache(maxsize=1)
def _pd() -> dict:
    if not PD_PATH.exists():
        return {}
    d = json.loads(PD_PATH.read_text(encoding="utf-8"))
    if d.get("raw"):  # счёт по сырым данным — так работают и специальности, добавленные позже импорта
        return {_norm(c): {s: pd_count(v, s) for s in PD_MAP} for c, v in d["raw"].items()}
    return {_norm(c): v for c, v in d["cities"].items()}


@lru_cache(maxsize=1)
def _ob() -> dict:
    if not OB_PATH.exists():
        return {}
    return {_norm(r): v for r, v in json.loads(OB_PATH.read_text(encoding="utf-8"))["regions"].items()}


# ─── Использование в расчёте ─────────────────────────────────────────────────

def prodoctorov(city_name: str, spec: str) -> int | None:
    v = _pd().get(_norm(city_name))
    return v.get(spec) if v else None


@lru_cache(maxsize=32)
def pd_ratio(spec: str) -> float | None:
    """Медианное отношение ПроДокторов / статистика по городам, где есть оба источника."""
    from .doctor_stats import _count, stats
    s = stats()
    r = []
    for city, row in (s or {}).get("cities", {}).items():
        v = _pd().get(_norm(city))
        st = _count(row, spec)
        if v and st and v.get(spec):
            r.append(v[spec] / st)
    return statistics.median(r) if len(r) >= 5 else None


def pd_estimate(city_name: str, spec: str) -> int | None:
    """Врачей в городе в масштабе официальной статистики по данным ПроДокторов."""
    n, k = prodoctorov(city_name, spec), pd_ratio(spec)
    if not n or not k:
        return None
    return int(round(n / k))


@lru_cache(maxsize=1)
def _features() -> dict[str, list[float]]:
    """Признаки города для модели: ln(население), областной центр, ln(поликлиник и больниц + 1),
    ln(медцентров + 1), ln(аптек по реестру + 1)."""
    from .cities import index
    from .facilities import CITIES_DIR
    idx = [e for e in index() if e.get("population")]
    largest: dict[str, dict] = {}
    for e in idx:
        if e["region"] not in largest or e["population"] > largest[e["region"]]["population"]:
            largest[e["region"]] = e
    out = {}
    for e in idx:
        p = CITIES_DIR / f"{e['slug']}.json"
        if not p.exists():
            continue
        fac = json.loads(p.read_text(encoding="utf-8"))["facilities"]
        lpu = sum(1 for f in fac if f["kind"] == "lpu" and f["category"] in ("polyclinic_adult", "polyclinic_child", "hospital"))
        priv = sum(1 for f in fac if f["kind"] == "lpu" and f["category"] == "private_center")
        ph = sum(1 for f in fac if f["kind"] == "pharmacy" and f.get("in_registry") is not False)
        out[e["slug"]] = [1.0, math.log(e["population"]), 1.0 if largest[e["region"]]["slug"] == e["slug"] else 0.0,
                          math.log(lpu + 1), math.log(priv + 1), math.log(ph + 1)]
    return out


@lru_cache(maxsize=32)
def _model(spec: str):
    """ln(врачей) = β·признаки; обучение на городах со статистикой и с данными ПроДокторов."""
    import numpy as np
    from .cities import index
    from .doctor_stats import _count, city_row
    feats = _features()
    X, Y = [], []
    for e in index():
        if e["slug"] not in feats:
            continue
        row = city_row(e["name"])
        y = _count(row, spec) if row else pd_estimate(e["name"], spec)
        if y:
            X.append(feats[e["slug"]])
            Y.append(math.log(y))
    if len(X) < 20:
        return None
    beta, *_ = np.linalg.lstsq(np.array(X), np.array(Y), rcond=None)
    return beta


def model_estimate(slug: str, spec: str) -> int | None:
    beta, f = _model(spec), _features().get(slug)
    if beta is None or f is None:
        return None
    return max(1, int(round(math.exp(float(sum(b * x for b, x in zip(beta, f)))))))


@lru_cache(maxsize=1)
def _pd_cities() -> list[dict]:
    """Города справочника, где есть ПроДокторов и нет своей строки в статистике."""
    from .cities import index
    from .doctor_stats import city_row
    return [e for e in index() if e.get("population") and _norm(e["name"]) in _pd() and not city_row(e["name"])]


@lru_cache(maxsize=512)
def density(spec: str, region: str) -> tuple[float, str] | None:
    """Врачей специальности на одного жителя по городам с данными ПроДокторов:
    по региону, если там не меньше 3 таких городов, иначе медиана по стране."""
    vals = [(e["region"], pd_estimate(e["name"], spec) / e["population"]) for e in _pd_cities()
            if pd_estimate(e["name"], spec) is not None]
    same = [v for r, v in vals if r == region]
    if len(same) >= 3:
        return statistics.median(same), "region"
    if len(vals) >= 10:
        return statistics.median(v for _, v in vals), "country"
    return None


def obrfm_region(region: str, spec: str) -> int | None:
    from .doctor_stats import _REGION_ALIASES
    reg = _norm(region)
    v = _ob().get(reg)
    if v is None:  # «Республика Татарстан» ↔ «Татарстан» и т. п.
        alias = _norm(_REGION_ALIASES.get(region, ""))
        v = _ob().get(alias) or next((x for k, x in _ob().items() if reg and (reg in k or k in reg)), None)
    return v.get(spec) if v else None


def summary() -> dict:
    return {"prodoctorov_cities": len(_pd()), "obrfm_regions": len(_ob()),
            "pd_ratios": {s: round(pd_ratio(s) or 0, 2) for s in PD_MAP}}
