"""
Реестр аптек (DSM): все аптеки России с координатами, сетью, юрлицом и ИНН.

Реестр — основной источник аптек. Для каждого города:
 - аптека реестра, рядом с которой (до 60 м или тот же адрес) есть аптека из OpenStreetMap, —
   это одна аптека: у неё уточняются сеть, юрлицо и адрес;
 - аптеки реестра, которых нет на карте, добавляются (source = "registry");
 - аптеки карты, которых нет в реестре, остаются, но помечаются in_registry = False — возможно,
   закрылись или переехали.
Среднемесячный оборот берётся по юрлицу (ИНН) и делится на число его аптек в регионе.

Сырой файл .xlsb читается один раз и сохраняется в data/source/pharmacies_registry.json.gz.
"""
from __future__ import annotations

import gzip
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from . import facilities as F

CACHE = F.DATA_DIR / "source" / "pharmacies_registry.json.gz"
SAME_M = 60


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").lower().replace("ё", "е")).strip()


def _net(name: str) -> str:
    """«РИГЛА (МОСКВА)» → «Ригла»; одиночные аптеки — без сети."""
    n = re.sub(r"\s*\(.*?\)\s*", " ", str(name or "")).strip()
    if not n or n.startswith("_"):
        return ""
    if not n.isupper():
        return n

    def word(w: str) -> str:
        # аббревиатуры (МЗ, ЛТД) и слова с цифрами (36,7С) оставляем заглавными
        if re.search(r"\d", w) or len(w) <= 3:
            return w
        return w[:1] + w[1:].lower()
    return re.sub(r"[^\s&\-/.]+", lambda m: word(m[0]), n)


def _settlement(s: str) -> str:
    return re.sub(r"^(г|пгт|рп|п|с|д|ст|х|аул|рп\.|г\.)\.?\s+", "", _norm(s))


def read_xlsb(path: str | Path) -> dict:
    from pyxlsb import open_workbook
    rows, turnover = [], {}
    with open_workbook(str(path)) as wb:
        with wb.get_sheet("Лист1") as ws:
            hdr = None
            for r in ws.rows():
                v = [c.v for c in r]
                if hdr is None:
                    if "ИНН" in v:
                        hdr = v
                    continue
                d = dict(zip(hdr, v))
                inn, cnt, s = d.get("ИНН"), d.get("Количество по полю Адрес"), d.get("Сумма по полю Среднемесячный оборот, руб.")
                if inn and cnt:
                    key = f"{inn}|{_norm(d.get('Регион DSM'))}"
                    turnover[key] = (s or 0) / cnt
        sheet = [s for s in wb.sheets if s != "Лист1"][0]
        with wb.get_sheet(sheet) as ws:
            hdr = None
            for r in ws.rows():
                v = [c.v for c in r]
                if hdr is None:
                    hdr = v
                    continue
                d = dict(zip(hdr, v))
                lat, lon = d.get("GPS_Широта"), d.get("GPS_Долгота")
                if not lat or not lon:
                    continue
                street = str(d.get("Улица") or "").strip()
                house = " ".join(str(d.get(k) or "").strip() for k in ("Дом", "Корпус")).strip()
                rows.append({
                    "code": int(d["Код DSM"]) if d.get("Код DSM") else None,
                    "settlement": _settlement(d.get("Населенный пункт")),
                    "region": _norm(d.get("Регион DSM")),
                    "street": street, "house": house, "address": d.get("Адрес") or "",
                    "network": _net(d.get("Аптечная сеть")), "legal": d.get("Юр. лицо") or "",
                    "inn": str(d.get("ИНН") or ""), "lat": round(float(lat), 6), "lon": round(float(lon), 6),
                })
    for r in rows:
        t = turnover.get(f"{r['inn']}|{r['region']}")
        r["turnover"] = round(t) if t else None
    data = {"rows": rows}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(CACHE, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return data


def load() -> dict | None:
    if not CACHE.exists():
        return None
    with gzip.open(CACHE, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _short_addr(r: dict) -> str:
    s = re.sub(r"^(ул|пр-кт|пр|пер|б-р|ш|пл|проезд|наб|мкр|тракт)\s+", "", r["street"])
    t = {"ул": "улица", "пр-кт": "проспект", "пер": "переулок", "б-р": "бульвар", "ш": "шоссе", "пл": "площадь",
         "наб": "набережная", "мкр": "микрорайон"}.get(r["street"].split(" ")[0], "")
    h = re.sub(r"^д\s+", "", r["house"])
    return ", ".join(x for x in (f"{t} {s}".strip() if t else s, h) if x)


def apply(data: dict | None = None) -> dict:
    """Свести реестр с аптеками всех загруженных городов. Возвращает сводку изменений."""
    from . import cities as C
    from .field_data import addr_key
    data = data or load()
    if not data:
        raise RuntimeError("Реестр аптек не загружен")
    index = C.index()
    by_name = defaultdict(list)
    for e in index:
        by_name[_norm(e["name"])].append(e["slug"])
    datasets = {}
    for p in F.CITIES_DIR.glob("*.json"):
        datasets[p.stem] = None
    centers = {}
    for slug in datasets:
        try:
            ds = F.load_city(slug)
        except Exception:
            continue
        datasets[slug] = ds
        centers[slug] = ds.get("center")

    per_city = defaultdict(list)
    for r in data["rows"]:
        slugs = [s for s in by_name.get(r["settlement"], []) if datasets.get(s)]
        if not slugs:
            continue
        if len(slugs) > 1:  # одноимённые города — ближайший к координатам
            slugs.sort(key=lambda s: F.haversine(r["lat"], r["lon"], *centers[s]))
        s = slugs[0]
        if F.haversine(r["lat"], r["lon"], *centers[s]) > 60:
            continue
        per_city[s].append(r)

    report = {"cities": 0, "matched": 0, "added": 0, "osm_not_in_registry": 0, "registry_total": 0, "per_city": {}}
    for slug, regs in per_city.items():
        ds = datasets[slug]
        fac = [f for f in ds["facilities"] if f["source"] != "registry"]
        osm_ph = [f for f in fac if f["kind"] == "pharmacy"]
        for f in osm_ph:
            f.pop("in_registry", None)
            f.pop("turnover", None)
        grid = F._Grid(0.001)
        for f in osm_ph:
            grid.add(f["lat"], f["lon"], f)
        by_addr = defaultdict(list)
        for f in osm_ph:
            k = addr_key(f.get("address", ""))
            if k:
                by_addr[k].append(f)
        used, added, matched = set(), [], 0
        for r in regs:
            k = addr_key(_short_addr(r))
            cands = [f for f in grid.near(r["lat"], r["lon"])
                     if id(f) not in used and F.haversine(r["lat"], r["lon"], f["lat"], f["lon"]) * 1000 <= SAME_M]
            if not cands and k:
                cands = [f for f in by_addr.get(k, []) if id(f) not in used
                         and F.haversine(r["lat"], r["lon"], f["lat"], f["lon"]) < 1.0]
            if cands:
                f = min(cands, key=lambda f: F.haversine(r["lat"], r["lon"], f["lat"], f["lon"]))
                used.add(id(f))
                matched += 1
                f["in_registry"] = True
                if r["network"]:
                    f["brand"] = r["network"]
                f["operator"] = r["legal"] or f.get("operator", "")
                f["inn"] = r["inn"]
                if r["turnover"]:
                    f["turnover"] = r["turnover"]
                if not f.get("address"):
                    f["address"] = _short_addr(r)
            else:
                added.append({
                    "id": f"reg_{r['code']}", "name": r["network"] or "Аптека", "kind": "pharmacy",
                    "category": "pharmacy", "address": _short_addr(r), "lat": r["lat"], "lon": r["lon"],
                    "phone": "", "hours": "", "source": "registry", "doctors": [], "brand": r["network"],
                    "operator": r["legal"], "inn": r["inn"], "turnover": r["turnover"], "in_registry": True,
                })
        not_in = 0
        for f in osm_ph:
            if not f.get("in_registry"):
                f["in_registry"] = False
                not_in += 1
        ds["facilities"] = fac + added
        F.save_city(ds)
        report["cities"] += 1
        report["matched"] += matched
        report["added"] += len(added)
        report["osm_not_in_registry"] += not_in
        report["registry_total"] += len(regs)
        report["per_city"][slug] = {"registry": len(regs), "osm": len(osm_ph), "matched": matched,
                                    "added": len(added), "osm_not_in_registry": not_in}
    (F.DATA_DIR / "source" / "pharmacies_registry_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
