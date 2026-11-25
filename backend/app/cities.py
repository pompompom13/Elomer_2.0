"""
Справочник городов России и фоновая загрузка их баз учреждений.

Список строится один раз из OpenStreetMap: города и посёлки с населением от 50 тыс.
вместе с субъектом РФ. Хранится в data/city_index.json.
"""
from __future__ import annotations

import json
import threading
import time

from . import facilities as F

INDEX_PATH = F.DATA_DIR / "city_index.json"
MIN_POPULATION = 50_000

_INDEX_QUERY = """[out:json][timeout:900];
area["ISO3166-1"="RU"][admin_level=2]->.ru;
rel(area.ru)["boundary"="administrative"]["admin_level"="4"];
map_to_area->.regs;
foreach.regs->.r(
  .r out tags;
  node(area.r)["place"~"^(city|town)$"]["population"];
  out tags;
);"""


def _pop(v) -> int:
    try:
        return int(str(v).replace(" ", "").split(";")[0].split(".")[0])
    except Exception:
        return 0


def build_index() -> list[dict]:
    raw = F.overpass(_INDEX_QUERY, attempts=3)
    region, rows, seen = "", [], set()
    for e in raw.get("elements", []):
        t = e.get("tags", {})
        if e["type"] == "area":
            region = t.get("name", "")
            continue
        pop = _pop(t.get("population"))
        if pop < MIN_POPULATION or not t.get("name") or e["id"] in seen:
            continue
        seen.add(e["id"])
        rows.append({"osm_id": e["id"], "name": t["name"], "region": region, "population": pop,
                     "lat": e.get("lat"), "lon": e.get("lon")})
    rows.sort(key=lambda r: -r["population"])
    names: dict[str, int] = {}
    for r in rows:
        names[r["name"]] = names.get(r["name"], 0) + 1
    for r in rows:
        r["slug"] = F.slugify(r["name"]) if names[r["name"]] == 1 else F.slugify(f"{r['name']} {r['region']}")
    INDEX_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def index() -> list[dict]:
    if INDEX_PATH.exists():
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return build_index()


def entry(slug: str) -> dict | None:
    return next((r for r in index() if r["slug"] == slug), None)


def catalog() -> list[dict]:
    """Все города: из справочника и загруженные (в том числе добавленные импортом)."""
    loaded = {c["slug"]: c for c in F.list_cities()}
    out = []
    for r in index():
        c = loaded.pop(r["slug"], None)
        out.append({"slug": r["slug"], "name": r["name"], "region": r["region"], "population": r["population"],
                    "loaded": c is not None, "lpu": c["lpu"] if c else 0,
                    "pharmacies": c["pharmacies"] if c else 0, "updated": c["updated"] if c else ""})
    for c in loaded.values():
        out.append({**c, "region": "", "population": None, "loaded": True})
    return out


def load(slug: str) -> dict:
    e = entry(slug)
    if not e:
        raise KeyError(slug)
    F.fetch_city(e)
    dedupe_all()
    return F.load_city(slug)


def dedupe_all() -> dict:
    """Одно учреждение — один город: при пересечении баз оно остаётся за более крупным городом.

    Города-спутники без своей границы в OSM загружаются кругом вокруг центра и могут
    захватить учреждения соседнего мегаполиса. Город, у которого после этого не осталось
    ни одного своего учреждения, — район другого города: он убирается из справочника.
    """
    rows = index()
    pop = {r["slug"]: r["population"] or 0 for r in rows}
    files = sorted((p for p in F.CITIES_DIR.glob("*.json") if not p.name.startswith("_")),
                   key=lambda p: -pop.get(p.stem, 0))
    owner: dict[str, str] = {}
    removed, dropped = 0, []
    for p in files:
        ds = json.loads(p.read_text(encoding="utf-8"))
        keep = []
        for f in ds["facilities"]:
            if f["source"] == "osm" and f["id"] in owner:
                removed += 1
                continue
            owner[f["id"]] = p.stem
            keep.append(f)
        if len(keep) != len(ds["facilities"]):
            if not keep:
                p.unlink()
                dropped.append(p.stem)
                continue
            ds["facilities"] = keep
            F.save_city(ds)
    if dropped:
        INDEX_PATH.write_text(json.dumps([r for r in rows if r["slug"] not in dropped], ensure_ascii=False, indent=1),
                              encoding="utf-8")
    return {"removed": removed, "dropped": dropped}


# ─── Фоновая загрузка всех городов ───────────────────────────────────────────

_job = {"running": False, "done": 0, "total": 0, "current": "", "failed": [], "started": ""}
_job_lock = threading.Lock()


def preload_status() -> dict:
    return dict(_job)


def start_preload(only_missing: bool = True) -> dict:
    with _job_lock:
        if _job["running"]:
            return dict(_job)
        loaded = {c["slug"] for c in F.list_cities()}
        todo = [r for r in index() if not (only_missing and r["slug"] in loaded)]
        _job.update(running=True, done=0, total=len(todo), current="", failed=[],
                    started=time.strftime("%Y-%m-%d %H:%M"))

    def run():
        try:
            for r in todo:
                _job["current"] = r["name"]
                try:
                    F.fetch_city(r)
                except Exception as ex:
                    _job["failed"].append({"name": r["name"], "error": str(ex)[:200]})
                _job["done"] += 1
                time.sleep(2)  # бережём общий сервер Overpass
        finally:
            dedupe_all()
            _job["running"] = False
            _job["current"] = ""

    threading.Thread(target=run, daemon=True).start()
    return dict(_job)
