"""
База учреждений: ЛПУ и аптеки.

Источники:
  * OpenStreetMap (Overpass API) — открытые данные (ODbL), загружаются по названию города;
  * импорт Excel/CSV — собственная база заказчика (CRM, выгрузка врачей, купленный справочник).

Адреса, которых нет в OSM, дозаполняются обратным геокодированием (Nominatim,
не чаще 1 запроса в секунду, с кешем на диске).
"""
from __future__ import annotations

import io
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CITIES_DIR = DATA_DIR / "cities"
CITIES_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "ELOMER-field-planner/1.0 (route planning for medical reps)"
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
NOMINATIM = "https://nominatim.openstreetmap.org"

# ─── Категории учреждений ────────────────────────────────────────────────────

CATEGORY_LABELS = {
    "polyclinic_adult": "Поликлиника (взрослая)",
    "ambulatory": "Врачебная амбулатория",
    "polyclinic_child": "Детская поликлиника / больница",
    "hospital": "Больница",
    "private_center": "Медицинский центр",
    "cardio_center": "Кардиологический центр",
    "womens": "Женская консультация / роддом",
    "dental": "Стоматология",
    "pharmacy": "Аптека",
    "excluded": "Не подходит для визитов",
}

_EXCLUDE_PATTERNS = [
    "лаборатор", "инвитро", "invitro", "гемотест",
    "хеликс", "kdl", "cmd ", "ситилаб", "citilab", "лабквест", "анализ",
    "психиатр", "наркол", "психоневр", "ветерин", "травмпункт", "травматолог",
    "диализ", "переливан", "морг", "патологоанатом", "судебно", "скорой", "скорая",
    "санатор", "туберкул", "фтизи", "мрт", "кт-центр", "рентген", "косметолог", "массаж",
    "оптика", "лечебной физкультур", "спорт", "реабилитац", "психотерап", "психолог", "физио",
    "логопед", "мануальн", "остеопат", "слуховой", "слухов", "протезн", "ортопедический салон",
    "банк крови", "онколог", "кожно-венер", "инфекцион", "хоспис", "паллиатив", "медтехник", "медицинская техника", "медосмотр", "справк",
    # аудит качества базы: объекты, где нет приёма врачей для визита медпреда
    "медпункт", "здравпункт", "медицинский пункт", "медицинский кабинет", "медкабинет", "фельдшерский пункт",
    "фельдшерско", "фап ", "фап№", "фап №", "подстанция", "станция смп", "станция скорой", "осмп",
    "гигиены и эпидемиолог", "роспотребнадзор", "медико-социальн", "бюро мсэ", "бюро медико",
    "красот", "эстетическ", "эстетик", "beauty", "эпиляц", "салон", "барбер", "профилактори",
    "дом престарел", "пансионат для", "линз", "очков",
]
# слово «ФАП» целиком (в конце названия или само название)
_EXCLUDE_EXACT = {"фап", "медпункт", "здравпункт"}


def excluded_name(name: str) -> bool:
    """Название говорит, что это не место визита к врачу (лаборатория, медпункт, салон и т. п.)."""
    low = (name or "").lower().replace("ё", "е")
    return any(p in low for p in _EXCLUDE_PATTERNS) or low.strip() in _EXCLUDE_EXACT or low.endswith(" фап")


_DENTAL = ("стомат", "дентал", "dental", "зубн", "зубо", "ортодонт", "имплантолог")


def is_dental(name: str) -> bool:
    low = (name or "").lower().replace("ё", "е")
    return any(p in low for p in _DENTAL)


# ФАП обслуживают фельдшеры, врачебная амбулатория — 1–2 врача: отдельный тип с малым числом врачей
def is_ambulatory(name: str) -> bool:
    low = (name or "").lower().replace("ё", "е")
    return "амбулатори" in low and "поликлин" not in low and "амбулаторно-" not in low and "амбулаторный центр" not in low


def classify(name: str, tags: dict) -> str:
    n = (name or "").lower()
    amenity = tags.get("amenity", "")
    hc = tags.get("healthcare", "")
    if amenity == "pharmacy" or hc == "pharmacy":
        return "pharmacy"
    if hc in ("laboratory", "blood_donation"):
        return "excluded"
    if (hc == "dentist" or amenity == "dentist" or is_dental(n)) and name:
        return "dental"
    if not name:
        return "excluded"  # безымянный объект нельзя поставить в маршрутный лист
    if excluded_name(n):
        return "excluded"
    if hc == "birthing_centre" or any(p in n for p in ("женская консультац", "роддом", "родильн", "перинатал")):
        return "womens"
    if "кардио" in n:
        return "cardio_center"
    if "детск" in n:
        return "polyclinic_child"
    if is_ambulatory(n):
        return "ambulatory"
    if "поликлин" in n or "медсанч" in n or "мсч" in n or "амбулатор" in n:
        return "polyclinic_adult"
    if amenity == "hospital" or "больниц" in n or "госпитал" in n or "диспансер" in n:
        return "hospital"
    if not name:
        return "excluded"
    return "private_center"


# ─── Геометрия ───────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def slugify(city: str) -> str:
    table = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                          "abvgdeejzijklmnoprstufhccss_y_eua")
    s = city.lower().translate(table)
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "city"


# ─── HTTP ────────────────────────────────────────────────────────────────────

def _http(url: str, data: bytes | None = None, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT,
                                                         "Accept-Language": "ru"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def overpass(query: str, attempts: int = 4, timeout: int = 330, deadline: float | None = None) -> dict:
    last = None
    for i in range(attempts):
        for url in OVERPASS_MIRRORS:
            if deadline and time.time() > deadline:
                raise RuntimeError("время на город вышло")
            try:
                raw = _http(url, urllib.parse.urlencode({"data": query}).encode(), timeout=timeout)
                return json.loads(raw)
            except Exception as e:  # сервера Overpass часто перегружены — пробуем следующее зеркало
                last = e
        time.sleep(3 + 2 * i)
    raise RuntimeError(f"OpenStreetMap не ответил: {last}")


FACILITY_FILTER = """
 nwr["amenity"~"^(clinic|hospital|doctors|pharmacy|dentist)$"]{AREA};
 nwr["healthcare"~"^(clinic|hospital|doctor|centre|pharmacy|dentist)$"]{AREA};"""


def _osm_query_city(entry: dict) -> str:
    """Учреждения внутри административной границы города + адресные объекты рядом с ними."""
    name = entry["name"].replace('"', "")
    return f"""[out:json][timeout:300];
node({entry['osm_id']})->.p;
.p is_in->.all;
area.all["name"="{name}"]["boundary"="administrative"]->.c;
({FACILITY_FILTER.replace("{AREA}", "(area.c)")}
)->.f;
.f out center tags;
nwr(around.f:45)["addr:housenumber"]["addr:street"]->.a;
.a out center tags;"""


def _osm_query_radius(entry: dict) -> str:
    """Запасной вариант, если у города нет границы в OSM: круг вокруг центра."""
    # небольшой круг: у городов-спутников без границы в OSM большой радиус захватывает соседний мегаполис
    r = int(min(12000, max(2500, (entry.get("population") or 100000) ** 0.5 * 8)))
    return f"""[out:json][timeout:300];
node({entry['osm_id']})->.p;
({FACILITY_FILTER.replace("{AREA}", f"(around.p:{r})")}
)->.f;
.f out center tags;
nwr(around.f:45)["addr:housenumber"]["addr:street"]->.a;
.a out center tags;"""


# ─── Построение набора учреждений ────────────────────────────────────────────

def _address_from_tags(t: dict) -> str:
    street, house = t.get("addr:street"), t.get("addr:housenumber")
    if street and house:
        return f"{street}, {house}"
    return t.get("addr:full") or ""


def _is_facility_tags(t: dict) -> bool:
    return (t.get("amenity") in ("clinic", "hospital", "doctors", "pharmacy", "dentist")
            or t.get("healthcare") in ("clinic", "hospital", "doctor", "centre", "pharmacy", "dentist"))


def _coords(e: dict):
    lat = e.get("lat") or (e.get("center") or {}).get("lat")
    lon = e.get("lon") or (e.get("center") or {}).get("lon")
    return lat, lon


class _Grid:
    """Простой пространственный индекс: ячейки ~200 м."""

    def __init__(self, step: float = 0.002):
        self.step, self.cells = step, {}

    def key(self, lat, lon):
        return int(lat / self.step), int(lon / self.step)

    def add(self, lat, lon, obj):
        self.cells.setdefault(self.key(lat, lon), []).append(obj)

    def near(self, lat, lon):
        ky, kx = self.key(lat, lon)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yield from self.cells.get((ky + dy, kx + dx), ())


def build_from_osm(raw: dict, city: str) -> list[dict]:
    items: list[dict] = []
    addr_grid = _Grid()
    for e in raw.get("elements", []):
        t = e.get("tags", {})
        lat, lon = _coords(e)
        if lat is None or lon is None:
            continue
        if not _is_facility_tags(t):
            a = _address_from_tags(t)
            if a:
                addr_grid.add(lat, lon, (lat, lon, a))
            continue
        name = (t.get("name") or t.get("official_name") or "").strip()
        if name.lower() in ("аптека", "pharmacy") and t.get("brand"):
            name = f"Аптека «{t['brand']}»"
        cat = classify(name, t)
        if cat == "excluded":
            continue
        if cat == "pharmacy" and not name:
            name = "Аптека"
        items.append({
            "id": f"osm_{e['type'][0]}{e['id']}",
            "name": name,
            "kind": "pharmacy" if cat == "pharmacy" else "lpu",
            "category": cat,
            "address": _address_from_tags(t),
            "lat": round(lat, 6), "lon": round(lon, 6),
            "phone": t.get("phone") or t.get("contact:phone") or "",
            "hours": t.get("opening_hours") or "",
            "source": "osm",
            "doctors": [],
            "brand": t.get("brand") or "",
            "operator": t.get("operator") or "",
            "operator_type": t.get("operator:type") or "",
        })
    # адрес ближайшего здания (до 60 м), если у самого учреждения адреса нет
    for it in items:
        if it["address"]:
            continue
        best, bd = None, 0.06
        for lat, lon, a in addr_grid.near(it["lat"], it["lon"]):
            d = haversine(it["lat"], it["lon"], lat, lon)
            if d < bd:
                best, bd = a, d
        if best:
            it["address"] = best
    return dedupe(items)


def _norm_name(n: str) -> str:
    return re.sub(r"[^а-яa-z0-9]", "", n.lower().replace("ё", "е"))


def dedupe(items: list[dict]) -> list[dict]:
    """В OSM одно учреждение бывает и точкой, и контуром здания — склеиваем."""
    out: list[dict] = []
    grid = _Grid()
    for it in sorted(items, key=lambda x: (x["address"] == "",)):
        dup = None
        for o in grid.near(it["lat"], it["lon"]):
            if o["kind"] != it["kind"]:
                continue
            d = haversine(o["lat"], o["lon"], it["lat"], it["lon"])
            if d < 0.025 or (d < 0.15 and _norm_name(o["name"]) == _norm_name(it["name"])):
                dup = o
                break
        if dup is None:
            out.append(it)
            grid.add(it["lat"], it["lon"], it)
        elif not dup["address"] and it["address"]:
            dup["address"] = it["address"]
    return out


# ─── Хранилище ───────────────────────────────────────────────────────────────

_lock = threading.RLock()
_geocode_jobs: dict[str, dict] = {}


def city_path(slug: str) -> Path:
    return CITIES_DIR / f"{slug}.json"


def save_city(ds: dict) -> None:
    with _lock:
        city_path(ds["slug"]).write_text(json.dumps(ds, ensure_ascii=False, indent=1), encoding="utf-8")


def load_city(slug: str) -> dict:
    p = city_path(slug)
    if not p.exists():
        raise KeyError(slug)
    with _lock:
        ds = json.loads(p.read_text(encoding="utf-8"))
    ds["facilities"] = dedupe_loaded(ds["facilities"])
    return ds


def _dkey(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[«»\"'.,()№#-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def dedupe_loaded(facilities: list[dict]) -> list[dict]:
    """Склеивает дубликаты: одна аптека с карты и из реестра, одно ЛПУ из карты и из данных визитов.
    Остаётся запись с оборотом (аптека) или из карты (ЛПУ); подтверждённые врачи суммируются по максимуму."""
    groups: dict[tuple, list[dict]] = {}
    for f in facilities:
        groups.setdefault((f["kind"], _dkey(f.get("name"))), []).append(f)
    drop: set[int] = set()
    for (kind, nm), lst in groups.items():
        if len(lst) < 2 or not nm:
            continue
        # предпочтение: с оборотом, есть в реестре, из карты
        lst = sorted(lst, key=lambda f: (not f.get("turnover"), f.get("in_registry") is False, f.get("source") != "osm"))
        by_cell: dict[tuple, list[dict]] = {}   # сетка ~50 м: сравниваем только с соседними клетками
        by_addr: dict[str, list[dict]] = {}
        for f in lst:
            a = _dkey(f.get("address"))
            cx, cy = int(f["lat"] / 0.0005), int(f["lon"] / 0.0008)
            near = (k for dx in (-1, 0, 1) for dy in (-1, 0, 1) for k in by_cell.get((cx + dx, cy + dy), ()))
            twin = next((k for k in near if haversine(k["lat"], k["lon"], f["lat"], f["lon"]) < 0.03), None)
            if twin is None and a:
                twin = next((k for k in by_addr.get(a, ()) if haversine(k["lat"], k["lon"], f["lat"], f["lon"]) < 0.3), None)
            if twin is None:
                by_cell.setdefault((cx, cy), []).append(f)
                if a:
                    by_addr.setdefault(a, []).append(f)
                continue
            drop.add(id(f))
            for sp, n in (f.get("known_doctors") or {}).items():
                kd = twin.setdefault("known_doctors", {})
                kd[sp] = max(kd.get(sp, 0), n)
            for k in ("phone", "hours", "address", "inn", "operator"):
                if not twin.get(k) and f.get(k):
                    twin[k] = f[k]
    return [f for f in facilities if id(f) not in drop]


_list_cache: dict[str, tuple[float, dict]] = {}


def list_cities() -> list[dict]:
    """Краткие сведения о загруженных городах; файл перечитывается, только если он изменился."""
    res = []
    for p in sorted(CITIES_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        c = _list_cache.get(p.stem)
        if not c or c[0] != mt:
            try:
                ds = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            fac = ds.get("facilities", [])
            c = (mt, {
                "slug": ds["slug"], "name": ds["name"],
                "lpu": sum(1 for f in fac if f["kind"] == "lpu"),
                # аптеки карты, которых нет в реестре, не считаем
                "pharmacies": sum(1 for f in fac if f["kind"] == "pharmacy" and f.get("in_registry") is not False),
                "updated": ds.get("updated", ""),
            })
            _list_cache[p.stem] = c
        res.append(c[1])
    return res


def _centroid(fac: list[dict]) -> list[float]:
    if not fac:
        return [55.75, 37.62]
    return [sum(f["lat"] for f in fac) / len(fac), sum(f["lon"] for f in fac) / len(fac)]


def make_dataset(city: str, facilities: list[dict], slug: str | None = None, extra: dict | None = None) -> dict:
    return {
        "slug": slug or slugify(city), "name": city,
        "center": _centroid([f for f in facilities if not f.get("needs_coords")]),
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "sources": sorted({f["source"] for f in facilities}),
        **(extra or {}),
        "facilities": facilities,
    }


# ─── Пригороды ───────────────────────────────────────────────────────────────
# Учреждения вокруг города (за его границей) — для зон, которые вручную расширены за город.

SUBURBS_DIR = CITIES_DIR / "suburbs"
SUBURBS_DIR.mkdir(parents=True, exist_ok=True)
SUBURB_KM = 30  # насколько далеко за границу города можно расширить зону


def load_suburbs(slug: str) -> list[dict] | None:
    p = SUBURBS_DIR / f"{slug}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))["facilities"]


def fetch_suburbs(entry: dict, budget_s: float | None = None, only: list[str] | None = None) -> list[dict]:
    """Учреждения в кольце до SUBURB_KM вокруг города, без тех, что уже в базе города.

    budget_s — сколько секунд можно потратить на город (фоновая загрузка всех городов): квадраты,
    на которые не хватило времени или которые сервер не отдал, записываются в missing_tiles и
    дозагружаются позже (only — список этих квадратов, найденное добавляется к уже сохранённому)."""
    deadline = time.time() + budget_s if budget_s else None
    q_timeout = 100 if budget_s else 240
    ds = load_city(entry["slug"])
    city_fac = ds["facilities"]
    c = ds["center"]
    radius = max((haversine(c[0], c[1], f["lat"], f["lon"]) for f in city_fac), default=10)
    r_km = min(75, radius + SUBURB_KM)
    # большое кольцо (Москва, Петербург) режем на квадраты — иначе Overpass не успевает ответить
    n = 1 if r_km <= 35 else 3 if r_km <= 60 else 4
    dlat = r_km / 110.57
    dlon = r_km / (111.32 * math.cos(math.radians(c[0])))
    raw = {"elements": []}
    failed = []

    def tile(s_, w_, hs, hw, depth=0):
        bbox = f"({s_:.5f},{w_:.5f},{s_ + hs:.5f},{w_ + hw:.5f})"
        if deadline and time.time() > deadline:
            failed.append(bbox)  # время на город вышло — квадрат дозагрузим позже
            return
        q = f"""[out:json][timeout:{q_timeout}];
({FACILITY_FILTER.replace("{AREA}", bbox)}
)->.f;
.f out center tags;
nwr(around.f:45)["addr:housenumber"]["addr:street"]->.a;
.a out center tags;"""
        try:
            raw["elements"] += overpass(q, attempts=1 if budget_s else 2, timeout=q_timeout + 20,
                                        deadline=deadline)["elements"]
        except RuntimeError:
            if depth >= 1:
                failed.append(bbox)  # квадрат пропускаем, остальное сохраняем
                return
            for a in range(2):
                for b in range(2):
                    tile(s_ + hs / 2 * a, w_ + hw / 2 * b, hs / 2, hw / 2, depth + 1)

    if only is not None:
        for bb in only:
            s_, w_, n_, e_ = (float(x) for x in bb.strip("()").split(","))
            tile(s_, w_, n_ - s_, e_ - w_)
    else:
        for i in range(n):
            for j in range(n):
                tile(c[0] - dlat + 2 * dlat * i / n, c[1] - dlon + 2 * dlon * j / n, 2 * dlat / n, 2 * dlon / n)
    fac = [f for f in build_from_osm(raw, entry["name"])
           if haversine(c[0], c[1], f["lat"], f["lon"]) <= r_km]
    r_m = int(r_km * 1000)
    own = {f["id"] for f in city_fac}
    fac = [f for f in fac if f["id"] not in own]
    for f in fac:
        f["suburb"] = True
    if only is not None:  # дозагрузка: добавляем к уже найденному
        prev = load_suburbs(entry["slug"]) or []
        have = {f["id"] for f in prev}
        fac = prev + [f for f in fac if f["id"] not in have]
    (SUBURBS_DIR / f"{entry['slug']}.json").write_text(
        json.dumps({"slug": entry["slug"], "radius_km": r_m / 1000, "missing_tiles": failed, "facilities": fac},
                   ensure_ascii=False),
        encoding="utf-8")
    return fac


def fetch_city(entry: dict, geocode: bool = True) -> dict:
    """Загрузить город из индекса (см. cities.py) из OpenStreetMap и сохранить."""
    raw = overpass(_osm_query_city(entry))
    fac = build_from_osm(raw, entry["name"])
    if len(fac) < 5:  # у города нет границы в OSM или она названа иначе
        raw = overpass(_osm_query_radius(entry))
        fac = build_from_osm(raw, entry["name"])
    if not fac:
        raise RuntimeError(f"В OpenStreetMap не найдено учреждений для «{entry['name']}».")
    slug = entry["slug"]
    if city_path(slug).exists():  # сохраняем импортированные записи и данные визитов
        old = load_city(slug)
        known = {f["id"]: f["known_doctors"] for f in old["facilities"] if f.get("known_doctors")}
        for f in fac:
            if f["id"] in known:
                f["known_doctors"] = known[f["id"]]
        fac += [f for f in old["facilities"] if f["source"] in ("import", "field")]
    ds = make_dataset(entry["name"], fac, slug=slug,
                      extra={"region": entry.get("region", ""), "population": entry.get("population")})
    from . import lpu_registry   # реестр ЛПУ загружен — ЛПУ города из него, с карты только аптеки
    lpu_registry.apply_city(ds)
    save_city(ds)
    if geocode:
        start_geocoding(slug)
    return ds


# ─── Обратное геокодирование адресов ─────────────────────────────────────────

def _reverse(lat: float, lon: float) -> str:
    url = f"{NOMINATIM}/reverse?format=jsonv2&zoom=18&addressdetails=1&lat={lat}&lon={lon}"
    d = json.loads(_http(url, timeout=30))
    a = d.get("address", {})
    road = a.get("road") or a.get("pedestrian") or a.get("residential") or ""
    house = a.get("house_number") or ""
    if road and house:
        return f"{road}, {house}"
    if road:
        return road
    parts = [x.strip() for x in d.get("display_name", "").split(",")]
    # без улицы Nominatim начинает с номера дома — добавляем следующий элемент (квартал, улицу)
    return ", ".join(reversed(parts[:2])) if len(parts) > 1 and parts[0][:1].isdigit() else (parts[0] if parts else "")


def _forward(query: str) -> tuple[float, float] | None:
    url = f"{NOMINATIM}/search?format=jsonv2&limit=1&q={urllib.parse.quote(query)}"
    d = json.loads(_http(url, timeout=30))
    if not d:
        return None
    return float(d[0]["lat"]), float(d[0]["lon"])


# Единая очередь геокодирования: Nominatim разрешает не больше 1 запроса в секунду на всё приложение
_geo_queue: list[str] = []
_geo_cv = threading.Condition()
_geo_worker: threading.Thread | None = None


def geocode_status(slug: str) -> dict:
    job = _geocode_jobs.get(slug)
    if not job:
        return {"running": False, "done": 0, "total": 0, "error": ""}
    return {k: job[k] for k in ("running", "done", "total", "error")}


def start_geocoding(slug: str) -> None:
    global _geo_worker
    with _geo_cv:
        job = _geocode_jobs.get(slug)
        if job and job["running"]:
            return
        _geocode_jobs[slug] = {"running": True, "done": 0, "total": 0, "error": ""}
        _geo_queue.append(slug)
        _geo_cv.notify()
        if _geo_worker is None or not _geo_worker.is_alive():
            _geo_worker = threading.Thread(target=_geo_loop, daemon=True)
            _geo_worker.start()


def _geo_loop():
    while True:
        with _geo_cv:
            while not _geo_queue:
                _geo_cv.wait()
            slug = _geo_queue.pop(0)
        _geocode_city(slug, _geocode_jobs[slug])


def _geocode_city(slug: str, job: dict) -> None:
    try:
        ds = load_city(slug)
        todo = [f for f in ds["facilities"] if not f["address"] or f.get("needs_coords")]
        job["total"] = len(todo)
        city = ds["name"]
        for i, f in enumerate(todo):
            try:
                if f.get("needs_coords"):
                    c = _forward(f"{city}, {f['address']}")
                    if c:
                        f["lat"], f["lon"] = round(c[0], 6), round(c[1], 6)
                        f.pop("needs_coords", None)
                else:
                    f["address"] = _reverse(f["lat"], f["lon"])
            except Exception:
                pass
            job["done"] = i + 1
            time.sleep(1.05)
            if (i + 1) % 25 == 0 or i + 1 == len(todo):
                _merge_back(slug, todo[: i + 1])
    except Exception as e:
        job["error"] = str(e)
    finally:
        job["running"] = False


def _merge_back(slug: str, updated: list[dict]) -> None:
    with _lock:
        ds = load_city(slug)
        by_id = {f["id"]: f for f in updated}
        for i, f in enumerate(ds["facilities"]):
            u = by_id.get(f["id"])
            if u:
                f.update(address=u["address"], lat=u["lat"], lon=u["lon"])
                if not u.get("needs_coords"):
                    f.pop("needs_coords", None)
        ds["center"] = _centroid([f for f in ds["facilities"] if not f.get("needs_coords")])
        save_city(ds)


# ─── Импорт собственной базы ─────────────────────────────────────────────────

IMPORT_COLUMNS = ["Название", "Тип", "Адрес", "Широта", "Долгота", "Специальность", "ФИО врача", "Врачей"]

_COL_ALIASES = {
    "name": ["название", "учреждение", "лпу", "наименование", "name"],
    "kind": ["тип", "вид", "type"],
    "address": ["адрес", "address"],
    "lat": ["широта", "lat", "latitude"],
    "lon": ["долгота", "lon", "lng", "longitude"],
    "spec": ["специальность", "специализация", "spec"],
    "doctor": ["фио врача", "фио", "врач", "doctor"],
    "count": ["врачей", "кол-во врачей", "количество врачей"],
}


def import_table(slug: str, city: str, content: bytes, filename: str, replace: bool) -> dict:
    import pandas as pd

    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content), sep=None, engine="python", dtype=str)
    else:
        df = pd.read_excel(io.BytesIO(content), dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    low = {c.lower(): c for c in df.columns}

    def col(key):
        for a in _COL_ALIASES[key]:
            if a in low:
                return low[a]
        return None

    cn, ck, ca, clat, clon, cs, cd, cc = (col(k) for k in
                                          ("name", "kind", "address", "lat", "lon", "spec", "doctor", "count"))
    if not cn or not ca:
        raise ValueError("В файле нужны как минимум колонки «Название» и «Адрес». Скачайте шаблон.")

    def num(v):
        try:
            return float(str(v).replace(",", "."))
        except Exception:
            return None

    groups: dict[str, dict] = {}
    for _, r in df.iterrows():
        name = str(r[cn]).strip() if r[cn] == r[cn] else ""
        addr = str(r[ca]).strip() if r[ca] == r[ca] else ""
        if not name and not addr:
            continue
        kind_raw = (str(r[ck]).lower() if ck and r[ck] == r[ck] else "")
        is_ph = "апт" in kind_raw or (not kind_raw and "аптек" in name.lower())
        key = f"{_norm_name(name)}|{_norm_name(addr)}"
        g = groups.get(key)
        if g is None:
            lat = num(r[clat]) if clat else None
            lon = num(r[clon]) if clon else None
            cat = "pharmacy" if is_ph else classify(name, {})
            if cat == "excluded":
                cat = "private_center"
            g = {
                "id": f"imp_{len(groups) + 1}_{abs(hash(key)) % 10**6}",
                "name": name or "Без названия", "kind": "pharmacy" if is_ph else "lpu",
                "category": cat, "address": addr,
                "lat": lat or 0.0, "lon": lon or 0.0, "phone": "", "hours": "",
                "source": "import", "doctors": [],
            }
            if lat is None or lon is None:
                g["needs_coords"] = True
            groups[key] = g
        spec = str(r[cs]).strip() if cs and r[cs] == r[cs] else ""
        doc = str(r[cd]).strip() if cd and r[cd] == r[cd] else ""
        cnt = int(num(r[cc]) or 0) if cc and r[cc] == r[cc] else 0
        if spec and doc:
            g["doctors"].append({"name": doc, "spec": spec})
        elif spec and cnt:
            g["doctors"] += [{"name": "", "spec": spec}] * cnt

    new = list(groups.values())
    if city_path(slug).exists():
        ds = load_city(slug)
        keep = [f for f in ds["facilities"] if f["source"] != "import"] if replace else ds["facilities"]
        ds = make_dataset(ds["name"], keep + new, slug=slug,
                          extra={k: ds[k] for k in ("region", "population") if k in ds})
    else:
        ds = make_dataset(city, new, slug=slug)
    save_city(ds)
    need = sum(1 for f in new if f.get("needs_coords"))
    if need:
        start_geocoding(ds["slug"])
    return {"imported": len(new), "need_geocoding": need, "slug": ds["slug"]}


def import_template() -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "База"
    ws.append(IMPORT_COLUMNS)
    ws.append(["ГУЗ Городская поликлиника №1", "ЛПУ", "ул. Примерная, 1", "", "", "Кардиолог", "Иванова А.А.", ""])
    ws.append(["ГУЗ Городская поликлиника №1", "ЛПУ", "ул. Примерная, 1", "", "", "Кардиолог", "Петров Б.Б.", ""])
    ws.append(["МЦ Здоровье", "ЛПУ", "пр. Ленина, 10", "54.19", "37.61", "Кардиолог", "", "2"])
    ws.append(["Аптека Ромашка", "Аптека", "ул. Советская, 5", "", "", "", "", ""])
    for c, w in zip("ABCDEFGH", (34, 10, 30, 10, 10, 16, 22, 8)):
        ws.column_dimensions[c].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
