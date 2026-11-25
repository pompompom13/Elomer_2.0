"""
Реестр ЛПУ (выгрузка DSM): основной источник медицинских учреждений.

Выгрузка — адреса лицензий на медицинскую деятельность: юрлицо, ИНН, тип учреждения
(поликлиника, ФАП, стоматологическая клиника…), адрес и координаты. После загрузки:
 - ЛПУ городов и пригородов берутся только из реестра; учреждения OpenStreetMap убираются;
 - учреждения из своей базы (импорт), данных визитов и баз заказчиков остаются;
 - не места визитов (ФАП, скорая, санатории, лаборатории, косметология и т. п.) не берутся;
 - корпуса одного юрлица в одном здании (до 80 м) — одно учреждение;
 - учреждение реестра, рядом с которым (до 150 м) было учреждение карты, получает его id,
   чтобы сохранённые планы и данные визитов продолжали на него ссылаться.

Файл .xlsx читается один раз и сохраняется в data/source/lpu_registry.json.gz.
"""
from __future__ import annotations

import gzip
import json
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from . import facilities as F
from . import pharmacy_registry as R

CACHE = F.DATA_DIR / "source" / "lpu_registry.json.gz"
RAW = F.DATA_DIR / "source" / "lpu_registry.xlsx"
OUTSIDE_FILE = F.DATA_DIR / "regions" / "_lpu_outside.json"
REPORT = F.DATA_DIR / "source" / "lpu_registry_report.json"
SAME_M = 150          # учреждение карты и точка реестра — одно учреждение
BUILDING_M = 80       # корпуса одного юрлица в одном здании
CITY_KM = 60

KEEP_SOURCES = ("import", "field", "client")

# ─── Тип учреждения ──────────────────────────────────────────────────────────

_SKIP_GROUP = {
    "ФАП", "САНАТОРНО-КУРОРТНАЯ ОРГАНИЗАЦИЯ", "СТАНЦИЯ СКОРОЙ МЕДИЦИНСКОЙ ПОМОЩИ", "СТАНЦИЯ ПЕРЕЛИВАНИЯ КРОВИ",
    "ЦЕНТР КРОВИ", "ДОМ РЕБЕНКА", "ХОСПИС", "ДОМ СЕСТРИНСКОГО УХОДА", "МОЛОЧНАЯ КУХНЯ", "ПУНКТ ВАКЦИНАЦИИ",
    "ЛЕПРОЗОРИЙ", "МЕДПУНКТ", "ЗДРАВПУНКТ", "ТРАВМПУНКТ",
}
_SKIP_SUB = ("МЕДИЦИНСКИХ ОСМОТРОВ", "ОЗДОРОВИТЕЛЬН", "ФИЗКУЛЬТУР", "СПИД", "ЛАБОРАТОР", "КОСМЕТОЛОГ",
             "ПЛАСТИЧЕСКОЙ", "ДИАЛИЗ", "РЕАБИЛИТАЦ", "ПСИХИАТР", "ПСИХОНЕВР", "НАРКОЛОГ", "ТУБЕРКУЛ",
             "ИНФЕКЦИОН", "КОЖНО-ВЕНЕР", "ОНКОЛОГ", "ПАЛЛИАТИВ", "СУДЕБНО", "ПАТОЛОГО")


def category(group: str | None, sub: str | None) -> str:
    p, s = (group or "").upper(), (sub or "").upper()
    if not p or p in _SKIP_GROUP:
        return "excluded"
    if "СТОМАТ" in s:
        return "dental"
    if any(w in s for w in _SKIP_SUB) or F.excluded_name(f"{p} {s}"):
        return "excluded"
    if p in ("ЖЕНСКАЯ КОНСУЛЬТАЦИЯ", "РОДИЛЬНЫЙ ДОМ") or any(w in s for w in ("ПЕРИНАТАЛ", "ГИНЕКОЛ", "РЕПРОДУК")):
        return "womens"
    if p.startswith("ДЕТСК"):
        return "polyclinic_child"
    if "КАРДИО" in s:
        return "cardio_center"
    if p in ("АМБУЛАТОРИЯ", "УЧАСТКОВАЯ БОЛЬНИЦА") or "ОБЩЕЙ ВРАЧЕБНОЙ ПРАКТИКИ" in s:
        return "ambulatory"
    if p in ("ПОЛИКЛИНИКА", "МЕДИКО-САНИТАРНАЯ ЧАСТЬ"):
        return "polyclinic_adult"
    if "БОЛЬНИЦА" in p or p in ("ГОСПИТАЛЬ", "ДИСПАНСЕР"):
        return "hospital"
    return "private_center"


def type_label(group: str | None, sub: str | None) -> str:
    """«КЛИНИКА» + «СТОМАТОЛОГИЧЕСКАЯ» → «стоматологическая клиника»."""
    p, s = (group or "").lower(), (sub or "").lower()
    if not s:
        return p
    if "отделение" in s or "стационар" in s:
        return f"{p}, {s}"
    if p.startswith("детск") and " " in p:
        a, b = p.split(" ", 1)
        return f"{a} {s} {b}"
    return f"{s} {p}"


# ─── Название и адрес ────────────────────────────────────────────────────────

_PRIVATE_FORMS = r"(ооо|ао|зао|пао|оао|нао|ано|нуз|чуз|ип|нко|мчуз|чу|ано\s+дпо)"
_IP_RE = re.compile(r"^\s*(ип|индивидуальный\s+предприниматель)\b", re.I)
_STATE_WORDS = ("государствен", "муниципал", "федерал", "бюджетн", "казенн", "казённ", "областн", "краев",
                "республиканск", "городск")


def _cap(s: str) -> str:
    """Название заглавными → обычными буквами (аббревиатуры и слова с цифрами — как были)."""
    if not s or not s.isupper():
        return s

    def word(w: str) -> str:
        vowels = sum(ch in "АЕЁИОУЫЭЮЯAEIOU" for ch in w)
        # аббревиатуры (ТОККД, ДРКБ, МКДЦ) и слова с цифрами — как были
        if re.search(r"\d", w) or len(w) <= 3 or vowels <= 1 or (len(w) <= 6 and vowels / len(w) < 0.34):
            return w
        return w[:1] + w[1:].lower()
    return re.sub(r"[^\s\"«»()\-/.,]+", lambda m: word(m[0]), s)


def legal_name(short: str, full: str) -> str:
    """ГАУЗ «Городская поликлиника №8» г.Казани → Городская поликлиника №8 г.Казани."""
    s = re.sub(r"\s+", " ", str(short or full or "")).strip()
    m = re.search(r"[«\"“]\s*(.+?)\s*[»\"”](.*)$", s)
    if m:
        s = (m[1] + " " + m[2]).strip(" ,")
    else:
        s = re.sub(rf"^{_PRIVATE_FORMS}\s+", "", s, flags=re.I)
    s = s.replace('"', "").replace("«", "").replace("»", "")
    s = re.sub(r"\s+", " ", s).strip()
    return _cap(s)


def display_name(r: dict) -> str:
    label = type_label(r["PGROUP"], r["SYBGROUP"])
    if _IP_RE.match(str(r.get("JURIDICAL_PERSON_1") or r.get("JURIDICAL_PERSON") or "")):
        return label[:1].upper() + label[1:]   # ИП — без имени предпринимателя
    name = legal_name(r.get("JURIDICAL_PERSON_1"), r.get("JURIDICAL_PERSON"))
    if not name:
        return label[:1].upper() + label[1:]
    low = name.lower().replace("ё", "е")
    stems = [w[:5] for w in label.split() if len(w) > 3]
    if not any(st in low for st in stems):
        name = f"{name} — {label}"
    return name


def _addr(r: dict) -> str:
    street = str(r.get("STREET") or "").strip()
    house = " ".join(str(r.get(k) or "").strip() for k in ("HOUSE", "BUILDING")).strip()
    if street:
        a = R._short_addr({"street": street, "house": re.sub(r"\s+", " ", house)})
        return re.sub(r"\b(к|корп|стр|литер)\s+", lambda m: m[1] + " ", a)
    full = str(r.get("ADDRESS_PLACE_FULL_STRING") or "")
    full = re.sub(r"^\s*\d{6},?\s*", "", full)
    parts = [p.strip() for p in full.split(",") if p.strip()]
    # без региона и района
    parts = [p for p in parts if not re.search(r"(област|край|республик|район|округ|^г\.?\s*москва$)", p, re.I)]
    return ", ".join(parts[-3:])


def _ownership(full: str, short: str) -> str:
    text = f"{full} {short}".lower().replace("ё", "е")
    if _IP_RE.match(short or "") or _IP_RE.match(full or ""):
        return "private"
    if re.match(rf"^\s*{_PRIVATE_FORMS}\b", (short or "").lower()) or "общество с ограниченной" in text \
            or "акционерное общество" in text or "автономная некоммерческая" in text or "частное учреждение" in text:
        return "private"
    if any(w in text for w in _STATE_WORDS) or re.match(r"^\s*(г|м|ф|о|к|р)[а-я]{0,3}(уз|бу|у)\b", text):
        return "state"
    return "private"


# ─── Чтение выгрузки ─────────────────────────────────────────────────────────

_SPECIAL_REGION = {"г. москва": "москва", "г. санкт-петербург": "санкт-петербург", "г. севастополь": "севастополь"}


def read_xlsx(path: str | Path, job: dict | None = None) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True)
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    need = {"PGROUP", "JURIDICAL_PERSON", "GPS_LATITUDE", "GPS_LONGITUDE", "ID_M"}
    if not need <= set(hdr):
        raise ValueError("В файле нет колонок выгрузки ЛПУ DSM (PGROUP, JURIDICAL_PERSON, GPS_LATITUDE, …)")
    pts, stats = [], defaultdict(int)
    for i, v in enumerate(it):
        if job is not None and i % 2000 == 0:
            job["read"] = i
        r = dict(zip(hdr, v))
        stats["rows"] += 1
        cat = category(r.get("PGROUP"), r.get("SYBGROUP"))
        if cat == "excluded":
            stats["excluded"] += 1
            continue
        lat, lon = r.get("GPS_LATITUDE"), r.get("GPS_LONGITUDE")
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            stats["no_coords"] += 1
            continue
        if not (40 < lat < 82 and 19 < lon < 191):
            stats["no_coords"] += 1
            continue
        full, short = str(r.get("JURIDICAL_PERSON") or ""), str(r.get("JURIDICAL_PERSON_1") or "")
        settlement = R._settlement(r.get("LOCALITY_NAME") or "") or \
            _SPECIAL_REGION.get(R._norm(r.get("REGION_NAME_DSM")), "")
        pts.append({
            "code": int(r["ID_M"]) if r.get("ID_M") else None, "cat": cat, "name": display_name(r),
            "type": type_label(r.get("PGROUP"), r.get("SYBGROUP")), "address": _addr(r),
            "settlement": settlement, "settlement_label": re.sub(r"^\S+\s+", "", str(r.get("LOCALITY_NAME") or "")).strip(),
            "region": R._norm(r.get("REGION_NAME_DSM")), "inn": str(r.get("INN") or ""),
            "operator": "Индивидуальный предприниматель" if _IP_RE.match(short or full) else (full or short),
            "ownership": _ownership(full, short), "lat": round(lat, 6), "lon": round(lon, 6),
        })
    wb.close()
    # корпуса одного юрлица одного типа в одном здании — одно учреждение
    grid = F._Grid(0.002)
    uniq = []
    for p in pts:
        if any(q["inn"] == p["inn"] and q["cat"] == p["cat"]
               and F.haversine(q["lat"], q["lon"], p["lat"], p["lon"]) * 1000 < BUILDING_M
               for q in grid.near(p["lat"], p["lon"])):
            stats["merged"] += 1
            continue
        grid.add(p["lat"], p["lon"], p)
        uniq.append(p)
    data = {"updated": time.strftime("%Y-%m-%d %H:%M"), "points": uniq, "stats": dict(stats)}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(CACHE, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return data


def load() -> dict | None:
    if not CACHE.exists():
        return None
    with gzip.open(CACHE, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def loaded() -> bool:
    return CACHE.exists()


# ─── Применение к базе ───────────────────────────────────────────────────────

def _facility(p: dict, fid: str | None = None) -> dict:
    return {"id": fid or f"dsm_{p['code']}", "dsm_id": p["code"], "name": p["name"], "kind": "lpu",
            "category": p["cat"], "type": p["type"], "address": p["address"], "lat": p["lat"], "lon": p["lon"],
            "phone": "", "hours": "", "source": "dsm", "doctors": [], "operator": p["operator"],
            "operator_type": "public" if p["ownership"] == "state" else "private", "inn": p["inn"]}


def _city_of(p: dict, by_name: dict, geo: list[dict]) -> str | None:
    cs = by_name.get(p["settlement"], [])
    if not cs and not p["settlement"]:  # населённый пункт не указан — по радиусу города
        cs = [g for g in geo if F.haversine(p["lat"], p["lon"], *g["center"]) <= g["radius"]]
    cs = [g for g in cs if F.haversine(p["lat"], p["lon"], *g["center"]) <= CITY_KM]
    if not cs:
        return None
    return min(cs, key=lambda g: F.haversine(p["lat"], p["lon"], *g["center"]))["slug"]


def _replace_city(ds: dict, pts: list[dict], idmap: dict[str, str]) -> dict:
    """ЛПУ города → точки реестра. idmap пополняется: старый id → новый."""
    old = [f for f in ds["facilities"] if f["kind"] == "lpu" and f["source"] not in KEEP_SOURCES]
    if not pts:   # город не нашёлся в реестре (другое написание) — прежние ЛПУ остаются
        return {"lpu": 0, "kept_own": 0, "removed_map": 0, "kept_ids": 0, "empty": True}
    old_ids = {id(f) for f in old}
    keep = [f for f in ds["facilities"] if id(f) not in old_ids]
    by_dsm = {f["dsm_id"]: f for f in old if f.get("dsm_id") is not None}
    grid = F._Grid(0.002)
    for f in old:
        if f.get("dsm_id") is None:
            grid.add(f["lat"], f["lon"], f)
    claimed: set[str] = set()
    new = []
    for p in pts:
        prev = by_dsm.get(p["code"])
        fid = prev["id"] if prev else None
        if fid is None:
            near = sorted((F.haversine(p["lat"], p["lon"], f["lat"], f["lon"]) * 1000, f["id"])
                          for f in grid.near(p["lat"], p["lon"]) if f["id"] not in claimed)
            near = [x for x in near if x[0] <= SAME_M]
            if near:
                fid = near[0][1]
        if fid:
            claimed.add(fid)
        f = _facility(p, fid)
        if prev and prev.get("known_doctors"):
            f["known_doctors"] = prev["known_doctors"]
        new.append(f)
    # старые учреждения → ближайшее новое (для данных визитов и баз заказчиков)
    g2 = F._Grid(0.002)
    for f in new:
        g2.add(f["lat"], f["lon"], f)
    for o in old:
        if o["id"] in claimed:
            idmap[o["id"]] = o["id"]
            continue
        near = [(F.haversine(o["lat"], o["lon"], f["lat"], f["lon"]) * 1000, f["id"]) for f in g2.near(o["lat"], o["lon"])]
        near = [x for x in near if x[0] <= SAME_M]
        if near:
            idmap[o["id"]] = min(near)[1]
    removed = sum(1 for o in old if o.get("dsm_id") is None)
    ds["facilities"] = keep + new
    return {"lpu": len(new), "kept_own": sum(1 for f in keep if f["kind"] == "lpu"),
            "removed_map": removed, "kept_ids": len(claimed)}


def apply_city(ds: dict, data: dict | None = None) -> None:
    """Новый или обновлённый из OpenStreetMap город: ЛПУ — из реестра (если он загружен)."""
    data = data or load()
    if not data:
        return
    name = R._norm(ds["name"])
    c = ds.get("center")
    pts = [p for p in data["points"] if p["settlement"] == name and c
           and F.haversine(p["lat"], p["lon"], c[0], c[1]) <= CITY_KM]
    if pts:
        _replace_city(ds, pts, {})


def _remap_refs(idmap: dict[str, str], valid: set[str]) -> dict:
    """Данные визитов и базы заказчиков ссылались на учреждения карты — переводим на учреждения реестра."""
    from . import client_bases as CB
    from . import field_data as FD
    res = {"field": 0, "client": 0}
    for folder, key in ((FD.DIR, "field"), (CB.DIR, "client")):
        for p in folder.glob("[!_]*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            changed = False
            for r in d.get("facilities") or []:
                oid = r.get("osm_id")
                if not oid or oid in valid:
                    continue
                nid = idmap.get(oid)
                if nid:
                    r["osm_id"] = nid
                elif r.get("lat") is not None:
                    r.update(osm_id=None, osm_name="", match="новое")   # останется как своё учреждение
                else:
                    continue
                changed = True
                res[key] += 1
            if changed:
                p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return res


def apply(data: dict | None = None, job: dict | None = None) -> dict:
    from . import territory as T
    data = data or load()
    if not data:
        raise RuntimeError("Реестр ЛПУ не загружен")
    geo = T.cities_geo()
    by_name: dict[str, list[dict]] = defaultdict(list)
    for g in geo:
        by_name[R._norm(g["name"])].append(g)
    per_city: dict[str, list[dict]] = defaultdict(list)
    outside_pts = []
    for p in data["points"]:
        s = _city_of(p, by_name, geo)
        if s:
            per_city[s].append(p)
        else:
            outside_pts.append(p)
    idmap: dict[str, str] = {}
    report = {"updated": data["updated"], "cities": 0, "lpu_cities": 0, "lpu_outside": 0, "kept_own": 0,
              "removed_map": 0, "kept_ids": 0, "stats": data.get("stats", {})}
    valid: set[str] = set()
    if job is not None:
        job.update(stage="Заменяю ЛПУ в городах", done=0, total=len(geo))
    for i, g in enumerate(geo):
        if job is not None:
            job["done"] = i
            job["current"] = g["name"]
        ds = F.load_city(g["slug"])
        r = _replace_city(ds, per_city.get(g["slug"], []), idmap)
        F.save_city(ds)
        valid |= {f["id"] for f in ds["facilities"]}
        report["cities"] += 1
        report["lpu_cities"] += r["lpu"]
        for k in ("kept_own", "removed_map", "kept_ids"):
            report[k] += r[k]
        if r.get("empty"):
            report.setdefault("not_found", []).append(g["name"])
    # вне городов: пригороды и области
    if job is not None:
        job.update(stage="Учреждения вне городов", current="")
    geo = T.cities_geo()
    out = []
    for p in outside_pts:
        f = _facility(p)
        f.update(outside=True, settlement=p["settlement_label"] or p["settlement"].capitalize())
        if f["settlement"] and f["settlement"].lower() not in f["address"].lower():
            f["address"] = ", ".join(x for x in (f["settlement"], f["address"]) if x)
        T._assign(f, geo)
        out.append(f)
    OUTSIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTSIDE_FILE.write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M"), "facilities": out},
                                       ensure_ascii=False), encoding="utf-8")
    report["lpu_outside"] = len(out)
    report["suburbs"] = sum(1 for f in out if f.get("near_city"))
    valid |= {f["id"] for f in out}
    if job is not None:
        job.update(stage="Переношу данные визитов")
    report["remapped"] = _remap_refs(idmap, valid)
    try:
        from . import field_data as FD
        FD.apply_all()
    except Exception:  # noqa: BLE001
        pass
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


# ─── Фоновая загрузка файла ──────────────────────────────────────────────────

_lock = threading.Lock()
_job: dict = {"running": False, "stage": "", "done": 0, "total": 0, "current": "", "read": 0, "error": ""}


def status() -> dict:
    rep = None
    if REPORT.exists():
        try:
            rep = json.loads(REPORT.read_text(encoding="utf-8"))
        except ValueError:
            rep = None
    return {**_job, "loaded": loaded(), "report": rep}


def start(content: bytes | None = None) -> dict:
    with _lock:
        if _job["running"]:
            return status()
        if content is not None:
            RAW.parent.mkdir(parents=True, exist_ok=True)
            RAW.write_bytes(content)
        _job.update(running=True, stage="Читаю файл", done=0, total=0, current="", read=0, error="")

    def run():
        try:
            data = read_xlsx(RAW, _job) if content is not None or not CACHE.exists() else load()
            apply(data, _job)
            # сводка «Базы» по всем областям считается заново (несколько минут) — сразу, а не при первом открытии
            _job.update(stage="Пересчитываю сводку базы", done=0, total=0, current="")
            from . import base_export
            from .main import BaseQuery
            base_export.summary(BaseQuery(scope="region").model_dump())
            base_export.alld_save()
        except Exception as ex:  # noqa: BLE001
            _job["error"] = str(ex)
        finally:
            _job.update(running=False, stage="", current="")

    threading.Thread(target=run, daemon=True).start()
    return status()
