"""
Дополнение базы по таблицам реальных визитов (выгрузки CRM, АКБ и т. п.).

Таблица: Город, Учреждение (или ЛПУ), Адрес, Специальность (Таргет-группа), Врач — по строке на врача.
Колонки ищутся по названию, несколько файлов и листов объединяются, повторы убираются.

Что делается с данными:
 1. Учреждение ищется в базе города по адресу (улица + дом), затем по координатам из Nominatim
    (ЛПУ не дальше 120 м). Небольшие посёлки относятся к ближайшему городу (до 35 км).
 2. Учреждения, которых нет на карте, добавляются в базу города (источник «данные визитов»).
 3. По каждому учреждению считается число врачей каждой специальности — оно становится нижней
    границей оценки (known_doctors): в клинике не может быть меньше врачей, чем к ним ходили.
 4. Строится сверка: данные визитов против оценки по статистике — по учреждениям и городам.

ФИО врачей не сохраняются — только количество. Все загрузки хранятся в data/field_data/<ключ>.json
(без ФИО) и применяются к базам заново при каждой загрузке или удалении.
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
import urllib.parse
from collections import defaultdict

from . import facilities as F

DIR = F.DATA_DIR / "field_data"
DIR.mkdir(parents=True, exist_ok=True)
GEO_CACHE = DIR / "_geocache.json"

SPEC_MAP = {
    "кардиолог": "Кардиолог", "невролог": "Невролог", "подростковая терапия": "Терапевт", "терапи": "Терапевт",
    "терапевт": "Терапевт", "общая практика": "Терапевт", "семейн": "Терапевт", "педиатр": "Педиатр",
    "эндокрин": "Эндокринолог", "гастро": "Гастроэнтеролог", "гинек": "Гинеколог", "уролог": "Уролог",
    "офтальм": "Офтальмолог", "дермат": "Дерматолог", "пульмон": "Пульмонолог", "аллерг": "Аллерголог-иммунолог",
    "ревмат": "Ревматолог", "хирург": "Хирург", "онколог": "Онколог", "лор": "ЛОР", "оторин": "ЛОР",
}
COLS = {
    "doc": ["врач", "фио врача", "фио"],
    "city": ["город", "населенный пункт", "населённый пункт"],
    "spec": ["таргет-группа", "специальность", "таргет группа", "специализация"],
    "inst": ["учреждение", "наименование учреждения"],
    "lpu": ["лпу", "наименование", "лпу кратко"],
    "addr": ["адрес"],
    "comment": ["комментарий"],
}
NEAR_M = 120
SETTLEMENT_KM = 35
_STREET_WORDS = {
    "улица", "ул", "проспект", "пр", "пр-т", "пркт", "просп", "переулок", "пер", "бульвар", "б-р", "бул",
    "шоссе", "ш", "площадь", "пл", "проезд", "пр-д", "набережная", "наб", "квартал", "кв-л", "мкр",
    "микрорайон", "мкрн", "тракт", "тупик", "аллея", "линия", "дом", "д", "корп", "корпус", "стр",
    "строение", "литер", "лит", "г", "город", "им", "имени",
}

_jobs: dict[str, dict] = {}
_apply_lock = threading.Lock()


def _norm(s) -> str:
    s = str(s or "").lower().replace("ё", "е").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def addr_key(addr: str) -> tuple[str, str] | None:
    """«Комсомольский 75А» и «Комсомольский проспект, 75А» → ('комсомольский', '75а')."""
    s = re.sub(r"[,.;()«»\"]", " ", _norm(addr))
    house, street = "", []
    for t in s.split():
        m = re.match(r"^(\d+)([а-я]?)(\d*)", t)
        if m and not house:
            # «38к1», «38с2» — корпус/строение, дом 38; «75а» — дом 75а
            house = m[1] if m[3] else m[1] + m[2]
        elif not re.search(r"\d", t) and t not in _STREET_WORDS and len(t) > 1:
            street.append(t)
    if not house or not street:
        return None
    return " ".join(sorted(street)), house.split("/")[0]


def inst_tag(name: str) -> tuple[str, str] | None:
    """Тип и номер учреждения: «ДГП 69», «Детская городская поликлиника №69» → ('дп', '69')."""
    n = _norm(name)
    words = set(re.findall(r"[а-я]+", n))
    if words & {"стоматологическая", "стоматология", "стоматологии"}:
        return None
    if "дгп" in words or ("детская" in words and words & {"поликлиника", "поликлиники"}):
        kind = "дп"
    elif words & {"гп", "кдп", "кдц"} or words & {"поликлиника", "поликлиники", "поликлиническое"}:
        kind = "п"
    elif words & {"гкб", "гб", "крб", "црб", "дгкб"} or words & {"больница", "больницы"}:
        kind = "б"
    elif "жк" in words or {"женская", "консультация"} <= words:
        kind = "жк"
    else:
        return None
    m = re.search(r"(?:№|\bn|\bno)?\s*(\d{1,3})\b", n)
    return (kind, m[1]) if m else None


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[а-яa-z0-9]+", _norm(s)) if len(w) > 2}


def map_spec(s) -> str | None:
    n = _norm(s)
    for k, v in SPEC_MAP.items():
        if n.startswith(k) or k in n:
            return v
    return None


# ─── Чтение ──────────────────────────────────────────────────────────────────

def read_rows(files: list[tuple[str, bytes]]) -> list[dict]:
    """Строки «врач в учреждении»; ФИО нужно только для подсчёта уникальных врачей."""
    import pandas as pd
    rows, seen = [], set()
    for fname, content in files:
        if fname.lower().endswith(".csv"):
            sheets = {"csv": pd.read_csv(io.BytesIO(content), dtype=str, header=None, sep=None, engine="python")}
        else:
            sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, dtype=str, header=None)
        for df in sheets.values():
            hdr = None
            for i in range(min(10, len(df))):
                vals = [_norm(v) for v in df.iloc[i].tolist()]
                if any(v in vals for v in COLS["city"]) and any(v in vals for v in COLS["addr"]):
                    hdr = i
                    break
            if hdr is None:
                continue
            names = [_norm(v) for v in df.iloc[hdr].tolist()]
            idx = {}
            for k, al in COLS.items():
                for a in al:
                    if a in names and k not in idx:
                        idx[k] = names.index(a)
            for r in df.iloc[hdr + 1:].itertuples(index=False):
                def get(k):
                    return str(r[idx[k]]).strip() if k in idx and r[idx[k]] == r[idx[k]] else ""
                city, inst = get("city"), get("inst") or get("lpu")
                comment = get("comment")
                if not city or not inst or "не работает" in comment.lower():
                    continue
                addr = get("addr")
                if comment and re.search(r"\d", comment) and "аптек" not in comment.lower():
                    addr = comment
                spec = map_spec(get("spec"))
                doc = get("doc")
                key = (_norm(doc), _norm(city), _norm(inst), spec)
                if doc and key in seen:
                    continue
                seen.add(key)
                rows.append({"doc": bool(doc), "doc_key": _norm(doc), "city": city, "inst": inst, "lpu": get("lpu"),
                             "addr": addr, "spec": spec})
    return rows


# ─── Геокодирование ──────────────────────────────────────────────────────────

_geo: dict | None = None


def _forward(city: str, addr: str):
    """Координаты через Nominatim (не чаще раза в секунду); ответы кэшируются на диске."""
    global _geo
    if _geo is None:
        _geo = json.loads(GEO_CACHE.read_text(encoding="utf-8")) if GEO_CACHE.exists() else {}
    a = re.sub(r"\bим\.?\s+", "", addr.strip(), flags=re.I)
    a = re.sub(r"(\d+)-?ая\b", r"\1-я", a)
    q = f"{city}, {a}".strip(", ")
    if q in _geo:
        return tuple(_geo[q]) if _geo[q] else None
    url = f"{F.NOMINATIM}/search?format=jsonv2&limit=1&countrycodes=ru&q=" + urllib.parse.quote(q)
    d = json.loads(F._http(url, timeout=30))
    time.sleep(1.05)
    res = (float(d[0]["lat"]), float(d[0]["lon"])) if d else None
    _geo[q] = list(res) if res else None
    GEO_CACHE.write_text(json.dumps(_geo, ensure_ascii=False), encoding="utf-8")
    return res


def _centers() -> dict[str, tuple[float, float]]:
    out = {}
    for p in F.CITIES_DIR.glob("*.json"):
        try:
            c = json.loads(p.read_text(encoding="utf-8")).get("center")
        except Exception:
            continue
        if c:
            out[p.stem] = (c[0], c[1])
    return out


# ─── Сопоставление ───────────────────────────────────────────────────────────

def build(key: str, title: str, files: list[tuple[str, bytes]], job: dict) -> dict:
    from .visit_reports import facility_groups, read
    visits = read(files, project=title)
    return _match(key, title, facility_groups(visits), job)


def read_lpu_list(files: list[tuple[str, bytes]]) -> list[dict]:
    """Список ЛПУ без врачей: Учреждение, Тип учреждения, Город, Регион, Адрес."""
    import pandas as pd
    out, seen = [], set()
    for fname, content in files:
        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, dtype=str, header=None)
        for df in sheets.values():
            hdr = None
            for i in range(min(10, len(df))):
                vals = [_norm(v) for v in df.iloc[i].tolist()]
                if any(v in vals for v in COLS["city"]) and any(v in vals for v in COLS["addr"]):
                    hdr = i
                    break
            if hdr is None:
                continue
            names = [_norm(v) for v in df.iloc[hdr].tolist()]
            col = lambda *al: next((names.index(a) for a in al if a in names), None)  # noqa: E731
            ci, ii, ai = col(*COLS["city"]), col(*COLS["inst"], *COLS["lpu"]), col(*COLS["addr"])
            ri, ti = col("регион", "область"), col("тип учреждения", "тип")
            for r in df.iloc[hdr + 1:].itertuples(index=False):
                def get(i):
                    return str(r[i]).strip() if i is not None and r[i] == r[i] else ""
                city = re.sub(r"^deprecated_", "", get(ci), flags=re.I).strip()
                inst, addr, typ = get(ii), get(ai), get(ti).lower()
                if not city or not inst or "санаторн" in typ:
                    continue  # санатории — не место визита медпреда
                k = (_norm(city), _norm(inst), _norm(addr))
                if k in seen:
                    continue
                seen.add(k)
                out.append({"settlement": city, "inst": inst, "addr": addr, "counts": {}, "hint_city": "",
                            "region": get(ri), "type": typ})
    return out


def _is_lpu_list(files: list[tuple[str, bytes]]) -> bool:
    """В таблице нет ни врачей, ни специальностей — это справочник ЛПУ, а не визиты."""
    import pandas as pd
    for fname, content in files:
        if fname.lower().endswith(".csv"):
            return False
        for df in pd.read_excel(io.BytesIO(content), sheet_name=None, dtype=str, header=None, nrows=10).values():
            for i in range(min(10, len(df))):
                vals = [_norm(v) for v in df.iloc[i].tolist()]
                if any(v in vals for v in COLS["city"]) and any(v in vals for v in COLS["addr"]):
                    return not any(v in vals for v in COLS["doc"] + COLS["spec"])
    return False


def start_lpu_list(title: str, files: list[tuple[str, bytes]]) -> dict:
    """Загрузка справочника ЛПУ: сверка с базой, новые учреждения добавляются (в городах и вне их)."""
    key = F.slugify(title)[:40] or "lpu"
    job = {"running": True, "stage": "Читаю файлы", "done": 0, "total": 0, "error": "", "key": key}
    _jobs[key] = job

    def run():
        try:
            _match(key, title, read_lpu_list(files), job)
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)
        finally:
            job["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return dict(job)


def _match(key: str, title: str, raw: list[dict], job: dict) -> dict:
    out, skipped, settlements = match_records(raw, job)
    data = {"key": key, "title": title, "created": time.strftime("%Y-%m-%d %H:%M"), "facilities": out,
            "skipped_cities": skipped, "settlements": settlements}
    (DIR / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    job["stage"] = "Обновляю базы городов"
    apply_all()
    return data


def match_records(raw: list[dict], job: dict) -> tuple[list[dict], list[str], list[str]]:
    """Сопоставить учреждения таблицы с базой городов, ничего не меняя в базе.

    raw — {settlement, inst, addr, counts, hint_city, region?, lat?, lon?, ref?, type?}. Записи результата
    получают match: «адрес», «название», «координаты» (найдено у нас, osm_id), «новое» (город базы,
    у нас нет), «вне городов», «не найдено»; refs — номера исходных строк."""
    from . import cities as C
    job["stage"] = "Сопоставляю города"
    index = C.index()
    by_name = {_norm(e["name"]): e for e in index}
    by_slug = {e["slug"]: e for e in index}
    centers = _centers()

    outside_pt: dict[tuple, tuple] = {}

    def resolve(name: str, hint: str, region: str = ""):
        """Город справочника: по названию; посёлок — ближайший город в регионе медпреда (до 35 км).
        Посёлок дальше — запоминаются его координаты: учреждение пойдёт в «остальную область»."""
        e = by_name.get(_norm(name))
        if e:
            return e
        h = by_name.get(_norm(hint))
        if not name:
            return h
        try:
            c = _forward(name, region or (h["region"] if h else ""))
        except Exception:
            c = None
        if c:
            near = [(F.haversine(c[0], c[1], *xy), s) for s, xy in centers.items() if s in by_slug]
            if near and min(near)[0] <= SETTLEMENT_KM:
                return by_slug[min(near)[1]]
            outside_pt[(_norm(name), _norm(hint))] = (c[0], c[1], region)
        return None

    city_of: dict[tuple, dict | None] = {}
    settlements = []
    groups: dict[tuple, dict] = {}
    far: list[dict] = []
    for g in raw:
        k = (_norm(g["settlement"]), _norm(g["hint_city"]))
        if k not in city_of:
            city_of[k] = resolve(g["settlement"], g["hint_city"], g.get("region", ""))
            if city_of[k] and g["settlement"] and _norm(g["settlement"]) != _norm(city_of[k]["name"]):
                settlements.append(f"{g['settlement']} → {city_of[k]['name']}")
        e = city_of[k]
        if not e:
            if k in outside_pt or g.get("lat") is not None:
                far.append({**g, "pt": outside_pt.get(k) or (g["lat"], g["lon"], g.get("region", "")), "refs": [g.get("ref")]})
            continue
        gg = groups.setdefault((e["slug"], _norm(g["inst"]), _norm(g["addr"])), {
            "city": e["slug"], "settlement": g["settlement"] or e["name"], "inst": g["inst"], "lpu": "",
            "addr": g["addr"], "counts": {}, "refs": [], "type": g.get("type", ""),
            "lat": g.get("lat"), "lon": g.get("lon")})
        gg["refs"].append(g.get("ref"))
        for s_, n in g["counts"].items():
            gg["counts"][s_] = max(gg["counts"].get(s_, 0), n)

    job["stage"] = "Ищу учреждения в базе"
    job["total"] = len(groups)
    loaded = {c["slug"] for c in F.list_cities()}
    cache: dict[str, tuple] = {}
    out = []
    for i, g in enumerate(groups.values()):
        job["done"] = i
        slug = g["city"]
        if slug not in cache:
            if slug not in loaded:
                try:
                    C.load(slug)
                except Exception:
                    pass
            try:
                lpus = [f for f in F.load_city(slug)["facilities"]
                        if f["kind"] == "lpu" and not f.get("needs_coords") and f["source"] != "field"]
            except KeyError:
                lpus = []
            by_addr: dict[tuple, list] = {}
            for f in lpus:
                k = addr_key(f.get("address", ""))
                if k:
                    by_addr.setdefault(k, []).append(f)
            by_tag: dict[tuple, list] = defaultdict(list)
            for f in lpus:
                t = inst_tag(f["name"])
                if t:
                    by_tag[t].append(f)
            cache[slug] = (lpus, by_addr, by_tag)
        lpus, by_addr, by_tag = cache[slug]
        names = _words(g["inst"]) | _words(g["lpu"])
        rec = {"city": slug, "settlement": g["settlement"], "name": g["lpu"] or g["inst"], "full_name": g["inst"],
               "address": g["addr"].strip(), "counts": dict(g["counts"]), "refs": g.get("refs", []),
               "type": g.get("type", ""), "osm_id": None, "osm_name": "", "lat": None, "lon": None, "match": ""}
        k = addr_key(g["addr"])
        cand = by_addr.get(k, []) if k else []
        tag = inst_tag(g["inst"]) or inst_tag(g["lpu"])
        by_name = by_tag.get(tag, []) if tag else []
        if cand:
            best = max(cand, key=lambda f: len(names & _words(f["name"])))
            rec.update(osm_id=best["id"], osm_name=best["name"], lat=best["lat"], lon=best["lon"], match="адрес")
        elif len(by_name) == 1:
            # «ДГП 125» = «Детская городская поликлиника №125»: тип и номер совпали, такое учреждение в городе одно
            best = by_name[0]
            rec.update(osm_id=best["id"], osm_name=best["name"], lat=best["lat"], lon=best["lon"], match="название")
        else:
            if g.get("lat") is not None:
                c = (g["lat"], g["lon"])  # координаты есть в таблице — геокодер не нужен
            else:
                try:
                    c = _forward(g["settlement"], g["addr"]) if g["addr"].strip() else None
                except Exception:
                    c = None
            if c:
                near = sorted(((F.haversine(c[0], c[1], f["lat"], f["lon"]) * 1000, f) for f in lpus),
                              key=lambda t: t[0])
                near = [f for d, f in near[:5] if d <= NEAR_M]
                if near:
                    best = max(near, key=lambda f: len(names & _words(f["name"])))
                    rec.update(osm_id=best["id"], osm_name=best["name"], lat=best["lat"], lon=best["lon"],
                               match="координаты")
                else:
                    rec.update(lat=round(c[0], 6), lon=round(c[1], 6), match="новое")
            else:
                rec["match"] = "не найдено"
        out.append(rec)
    # учреждения вне городов базы: координаты по адресу, иначе центр посёлка
    job["stage"] = "Ищу координаты учреждений вне городов"
    job["total"] = len(groups) + len(far)
    for i, g in enumerate(far):
        job["done"] = len(groups) + i
        if g.get("lat") is not None:
            c = (g["lat"], g["lon"])
        else:
            try:
                c = _forward(g["settlement"], g["addr"]) if g["addr"].strip() else None
            except Exception:
                c = None
        lat, lon = (c[0], c[1]) if c else g["pt"][:2]
        out.append({"city": None, "settlement": g["settlement"], "region": g["pt"][2], "name": g["inst"],
                    "full_name": g["inst"], "address": f"{g['settlement']}, {g['addr'].strip()}".strip(", "),
                    "counts": dict(g["counts"]), "osm_id": None, "osm_name": "", "lat": round(lat, 6),
                    "lon": round(lon, 6), "match": "вне городов", "approx": not c,
                    "refs": g.get("refs", []), "type": g.get("type", "")})
    job["done"] = job["total"]
    skipped = sorted({k[0] or k[1] for k, e in city_of.items() if not e and k not in outside_pt})
    return out, skipped, sorted(set(settlements))


# ─── Применение к базам ──────────────────────────────────────────────────────

def _imports() -> list[dict]:
    out = []
    for p in sorted(DIR.glob("[!_]*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _fid(key: str, r: dict) -> str:
    return "fld_" + F.slugify(f"{key}_{r['city']}_{r['name']}_{r['address']}")[:60]


def apply_all() -> None:
    """Пересобрать данные визитов во всех городах: число врачей и добавленные учреждения."""
    with _apply_lock:
        imports = _imports()
        known: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
        added: dict[str, dict[str, dict]] = defaultdict(dict)
        for d in imports:
            for r in d["facilities"]:
                if not r.get("city"):
                    continue
                if r["match"] in ("адрес", "название", "координаты"):
                    k = known[r["city"]][r["osm_id"]]
                    for s, n in r["counts"].items():
                        k[s] = max(k.get(s, 0), n)
                elif r["match"] == "новое":
                    fid = _fid(d["key"], r)
                    f = added[r["city"]].get(fid) or {
                        "id": fid, "name": r["name"], "kind": "lpu", "category": _category(r["full_name"]),
                        "address": r["address"], "lat": r["lat"], "lon": r["lon"], "phone": "", "hours": "",
                        "source": "field", "doctors": [], "known_doctors": {}}
                    for s, n in r["counts"].items():
                        f["known_doctors"][s] = max(f["known_doctors"].get(s, 0), n)
                    added[r["city"]][fid] = f
        touched = set(known) | set(added)
        for p in F.CITIES_DIR.glob("*.json"):
            slug = p.stem
            try:
                ds = F.load_city(slug)
            except Exception:
                continue
            had = any(f.get("known_doctors") or f["source"] == "field" for f in ds["facilities"])
            if slug not in touched and not had:
                continue
            fac = [f for f in ds["facilities"] if f["source"] != "field"]
            for f in fac:
                f.pop("known_doctors", None)
                if f["id"] in known.get(slug, {}):
                    f["known_doctors"] = dict(known[slug][f["id"]])
            # новое учреждение не дальше самого дальнего учреждения карты этого города (+5 км):
            # иначе адрес найден не в том городе
            c = ds["center"]
            osm = [F.haversine(c[0], c[1], f["lat"], f["lon"]) for f in fac if f["source"] == "osm"]
            limit = (max(osm) if osm else 30) + 5
            new = [f for f in added.get(slug, {}).values()
                   if F.haversine(c[0], c[1], f["lat"], f["lon"]) <= limit]
            ds["facilities"] = fac + new
            F.save_city(ds)


def _category(name: str) -> str:
    c = F.classify(name, {})
    return "polyclinic_adult" if c in ("excluded", "pharmacy") else c


# ─── Сверка с оценкой ────────────────────────────────────────────────────────

def comparison(key: str) -> dict:
    """Данные визитов против оценки по статистике (оценка — без учёта этих данных)."""
    from . import norms as N
    from .doctor_stats import calibration, target
    d = get(key)
    rows, per_city = [], defaultdict(lambda: defaultdict(lambda: {"field": 0, "lpu": 0, "est": 0}))
    calib_cache, ds_cache = {}, {}
    for r in d["facilities"]:
        if r["match"] == "не найдено" or not r.get("city") or not r["counts"]:
            continue
        slug = r["city"]
        if slug not in ds_cache:
            try:
                ds_cache[slug] = {f["id"]: f for f in F.load_city(slug)["facilities"]}
            except KeyError:
                ds_cache[slug] = {}
        f = ds_cache[slug].get(r["osm_id"] or _fid(key, r))
        for s, n in r["counts"].items():
            if (slug, s) not in calib_cache:
                calib_cache[(slug, s)] = calibration(slug, s)
            est = None
            if f is not None:
                base = {k: v for k, v in f.items() if k != "known_doctors"}  # оценка без данных визитов
                est = len(N.doctors_in(base, s, calib=calib_cache[(slug, s)]))
            rows.append({"city": slug, "name": r["name"], "osm_name": r["osm_name"], "address": r["address"],
                         "match": r["match"], "category": (f or {}).get("category", ""), "spec": s,
                         "field": n, "est": est})
            c = per_city[slug][s]
            c["field"] += n
            c["lpu"] += 1
            c["est"] += est or 0
    from . import cities as C
    names = {e["slug"]: e["name"] for e in C.index()}
    cities = []
    for slug, specs in per_city.items():
        for s, c in specs.items():
            t, method = target(slug, s)
            cities.append({"city": names.get(slug, slug), "slug": slug, "spec": s, **c,
                           "stat": t, "stat_method": method})
    by_cat = defaultdict(lambda: {"field": 0, "est": 0, "n": 0})
    for x in rows:
        if x["est"] is not None and x["match"] != "новое":
            b = by_cat[(x["category"], x["spec"])]
            b["field"] += x["field"]
            b["est"] += x["est"]
            b["n"] += 1
    return {"rows": rows, "cities": sorted(cities, key=lambda x: (x["city"], x["spec"])),
            "categories": [{"category": k[0], "spec": k[1], **v} for k, v in sorted(by_cat.items())]}


def comparison_xlsx(key: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    cmp_ = comparison(key)
    d = get(key)
    wb = Workbook()
    head = PatternFill("solid", fgColor="1E2A35")
    low = PatternFill("solid", fgColor="F8DEDA")

    def sheet(ws, headers, widths):
        ws.append(headers)
        for i, w in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = w
            ws.cell(1, i + 1).fill, ws.cell(1, i + 1).font = head, Font(bold=True, color="FFFFFF")
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "По городам"
    sheet(ws, ["Город", "Специальность", "Учреждений в таблице", "Врачей в таблице",
               "Оценка в этих же учреждениях", "Врачей в городе по статистике", "Источник статистики",
               "Доля таблицы от статистики, %"], [20, 16, 12, 12, 14, 14, 16, 14])
    src = {"city": "статистика города", "region": "доля региона", "estimate": "оценка",
           "prodoctorov": "ПроДокторов", "density": "плотность по ПроДокторов"}
    for c in cmp_["cities"]:
        ws.append([c["city"], c["spec"], c["lpu"], c["field"], c["est"], c["stat"], src.get(c["stat_method"], ""),
                   round(c["field"] / c["stat"] * 100) if c["stat"] else None])
    ws2 = wb.create_sheet("По учреждениям")
    sheet(ws2, ["Город", "Учреждение (таблица)", "Учреждение (карта)", "Адрес", "Как найдено", "Специальность",
                "Врачей в таблице", "Оценка до уточнения", "Разница"], [16, 36, 36, 26, 12, 14, 10, 12, 9])
    for x in cmp_["rows"]:
        diff = x["field"] - x["est"] if x["est"] is not None else None
        ws2.append([x["city"], x["name"], x["osm_name"], x["address"], x["match"], x["spec"], x["field"], x["est"], diff])
        if diff is not None and diff > 0:
            for c in ws2[ws2.max_row]:
                c.fill = low
    ws3 = wb.create_sheet("По типам учреждений")
    sheet(ws3, ["Тип", "Специальность", "Учреждений", "Врачей в таблице", "Оценка", "Таблица / оценка"],
          [26, 16, 12, 12, 10, 14])
    labels = F.CATEGORY_LABELS
    for x in cmp_["categories"]:
        ws3.append([labels.get(x["category"], x["category"]), x["spec"], x["n"], x["field"], x["est"],
                    round(x["field"] / x["est"], 2) if x["est"] else None])
    ws4 = wb.create_sheet("Не найдено")
    sheet(ws4, ["Город / посёлок", "Учреждение", "Адрес"], [22, 40, 30])
    for r in d["facilities"]:
        if r["match"] == "не найдено":
            ws4.append([r["settlement"], r["name"], r["address"]])
    for n in d.get("skipped_cities", []):
        ws4.append([n, "населённый пункт не найден рядом с городами справочника", ""])
    import io as _io
    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─── Доступ и фоновый импорт ─────────────────────────────────────────────────

def get(key: str) -> dict:
    p = DIR / f"{key}.json"
    if not p.exists() or key.startswith("_"):
        raise KeyError(key)
    return json.loads(p.read_text(encoding="utf-8"))


def summary(d: dict) -> dict:
    fac = d["facilities"]
    docs = lambda m=None: sum(sum(r["counts"].values()) for r in fac if m is None or r["match"] == m)
    return {"key": d["key"], "title": d["title"], "created": d["created"],
            "facilities": len(fac), "doctors": docs(), "cities": len({r["city"] for r in fac if r.get("city")}),
            "outside": sum(1 for r in fac if r["match"] == "вне городов"),
            "matched": sum(1 for r in fac if r["match"] in ("адрес", "название", "координаты")),
            "added": sum(1 for r in fac if r["match"] in ("новое", "вне городов")),
            "not_found": sum(1 for r in fac if r["match"] == "не найдено"),
            "skipped_cities": d.get("skipped_cities", [])}


def list_all() -> list[dict]:
    return [summary(d) for d in _imports()]


def delete(key: str) -> None:
    (DIR / f"{key}.json").unlink(missing_ok=True)
    apply_all()


def start_import(title: str, files: list[tuple[str, bytes]]) -> dict:
    key = F.slugify(title)[:40] or "visits"
    job = {"running": True, "stage": "Читаю файлы", "done": 0, "total": 0, "error": "", "key": key}
    _jobs[key] = job

    def run():
        try:
            if _is_lpu_list(files):  # справочник ЛПУ без врачей и специальностей
                _match(key, title, read_lpu_list(files), job)
                return
            build(key, title, files, job)
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)
        finally:
            job["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return dict(job)


def status(key: str) -> dict:
    return dict(_jobs.get(key) or {"running": False, "stage": "", "done": 0, "total": 0, "error": "", "key": key})
