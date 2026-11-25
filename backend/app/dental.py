"""
Стоматологии во всех загруженных городах: дозагрузка из OpenStreetMap.

Раньше стоматологии в базу не попадали (не было специальности «Стоматолог»). Теперь это свой тип
учреждений: новые города загружаются сразу со стоматологиями, а уже загруженные дозагружаются
этим фоновым заданием — один лёгкий запрос на город, только стоматологии. Город помечается
в data/cache/dental_loaded.json, повторный запуск берёт только оставшиеся.
"""
from __future__ import annotations

import json
import threading
import time

from . import facilities as F
from .cities import index

DENTAL_FILTER = """
 nwr["amenity"="dentist"]{AREA};
 nwr["healthcare"="dentist"]{AREA};"""

_lock = threading.Lock()
_DONE = F.DATA_DIR / "cache" / "dental_loaded.json"   # какие города уже со стоматологиями


def _done() -> set[str]:
    try:
        return set(json.loads(_DONE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _mark(slug: str) -> None:
    d = _done() | {slug}
    _DONE.parent.mkdir(parents=True, exist_ok=True)
    _DONE.write_text(json.dumps(sorted(d)), encoding="utf-8")
_job: dict = {"running": False, "done": 0, "total": 0, "current": "", "failed": [], "added": 0}


def _query(entry: dict, radius: bool) -> str:
    name = entry["name"].replace('"', "")
    if radius:
        r = int(min(12000, max(2500, (entry.get("population") or 100000) ** 0.5 * 8)))
        area, head = f"(around.p:{r})", f"node({entry['osm_id']})->.p;"
    else:
        area = "(area.c)"
        head = (f"node({entry['osm_id']})->.p;\n.p is_in->.all;\n"
                f'area.all["name"="{name}"]["boundary"="administrative"]->.c;')
    return f"""[out:json][timeout:180];
{head}
({DENTAL_FILTER.replace("{AREA}", area)}
)->.f;
.f out center tags;
nwr(around.f:45)["addr:housenumber"]["addr:street"]->.a;
.a out center tags;"""


def fetch_city(entry: dict) -> int:
    """Стоматологии города → в файл города. Возвращает, сколько добавлено."""
    slug = entry["slug"]
    raw = F.overpass(_query(entry, radius=False), timeout=200)
    fac = [f for f in F.build_from_osm(raw, entry["name"]) if f["category"] == "dental"]
    if not fac:
        raw = F.overpass(_query(entry, radius=True), timeout=200)
        fac = [f for f in F.build_from_osm(raw, entry["name"]) if f["category"] == "dental"]
    ds = F.load_city(slug)
    have = {f["id"] for f in ds["facilities"]}
    new = [f for f in fac if f["id"] not in have]
    ds["facilities"] += new
    F.save_city(ds)
    _mark(slug)
    return len(new)


def todo() -> list[dict]:
    done = _done()
    return [r for r in index() if r["slug"] not in done and F.city_path(r["slug"]).exists()]


def status() -> dict:
    have = {p.stem for p in F.CITIES_DIR.glob("*.json")}
    from . import lpu_registry
    if lpu_registry.loaded():
        return {**_job, "failed": [], "loaded": len(have), "cities": len(have), "registry": True}
    return {**_job, "failed": list(_job["failed"]), "loaded": len(_done() & have), "cities": len(have)}


def start() -> dict:
    from . import lpu_registry
    if lpu_registry.loaded():   # стоматологии уже из реестра ЛПУ
        from . import resume
        resume.mark("dental", False)
        return status()
    with _lock:
        if _job["running"]:
            return status()
        items = todo()
        items.sort(key=lambda r: r.get("population") or 0)   # сначала небольшие города
        _job.update(running=True, done=0, total=len(items), current="", failed=[], added=0)
    from . import resume
    resume.mark("dental", True)   # перезапуск сервера не потеряет загрузку

    def run():
        try:
            for r in items:
                _job["current"] = r["name"]
                try:
                    _job["added"] += fetch_city(r)
                except Exception as ex:  # noqa: BLE001
                    _job["failed"].append({"name": r["name"], "error": str(ex)[:200]})
                _job["done"] += 1
                time.sleep(2)  # бережём общий сервер Overpass
        finally:
            _job["running"] = False
            _job["current"] = ""
            if not _job["failed"]:
                from . import resume
                resume.mark("dental", False)   # все города прошли

    threading.Thread(target=run, daemon=True).start()
    return status()
