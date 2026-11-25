"""
Учреждения за пределами городов от 50 тыс. жителей: пригороды и остальная территория области.

- Аптеки — из реестра: все аптеки, которые не относятся ни к одному загруженному городу.
- ЛПУ — из OpenStreetMap в кольце вокруг каждого города (файлы data/cities/suburbs/*.json,
  загружаются по одному городу или все сразу в фоне).

Каждое учреждение получает:
  region    — область (по ближайшему городу базы),
  near_city — город, к пригороду которого оно относится (в пределах радиуса города + SUBURB_KM),
              или None — «остальная область».
"""
from __future__ import annotations

import json
import threading
import time

from . import facilities as F
from . import pharmacy_registry as R
from .cities import index

OUT_DIR = F.DATA_DIR / "regions"
OUT_DIR.mkdir(parents=True, exist_ok=True)
PH_FILE = OUT_DIR / "_pharmacies_outside.json"
LPU_FILE = OUT_DIR / "_lpu_outside.json"   # ЛПУ вне городов из реестра ЛПУ (см. lpu_registry.py)

_cache: dict = {"stamp": None, "cities": None, "outside": None}
_lock = threading.RLock()


def _stamp() -> tuple:
    files = list(F.CITIES_DIR.glob("*.json")) + list(F.SUBURBS_DIR.glob("*.json")) + [PH_FILE, LPU_FILE] + \
        list((F.DATA_DIR / "field_data").glob("[!_]*.json"))
    return tuple(sorted((p.name, p.stat().st_mtime) for p in files if p.exists()))


def cities_geo() -> list[dict]:
    """Загруженные города: центр, радиус (по 95% учреждений) и область."""
    regions = {r["slug"]: r for r in index()}
    out = []
    for c in F.list_cities():
        try:
            ds = F.load_city(c["slug"])
        except Exception:
            continue
        ctr = ds.get("center")
        if not ctr:
            continue
        d = sorted(F.haversine(ctr[0], ctr[1], f["lat"], f["lon"]) for f in ds["facilities"])
        radius = d[int(len(d) * 0.95)] if d else 10
        e = regions.get(c["slug"], {})
        out.append({"slug": c["slug"], "name": ds["name"], "region": e.get("region") or ds.get("region") or "",
                    "center": ctr, "radius": min(max(radius, 3.0), 45.0), "population": e.get("population"),
                    "ids": {f["id"] for f in ds["facilities"]}})
    return out


def _assign(f: dict, geo: list[dict]) -> None:
    best = min(geo, key=lambda c: F.haversine(f["lat"], f["lon"], *c["center"]))
    f["region"] = best["region"]
    near = [c for c in geo if F.haversine(f["lat"], f["lon"], *c["center"]) <= c["radius"] + F.SUBURB_KM]
    f["near_city"] = min(near, key=lambda c: F.haversine(f["lat"], f["lon"], *c["center"]))["slug"] if near else None


def build_pharmacies() -> dict:
    """Аптеки реестра вне городов базы (то же правило отнесения к городу, что и при сведении реестра)."""
    data = R.load()
    if not data:
        raise RuntimeError("Реестр аптек не загружен")
    geo = cities_geo()
    by_name: dict[str, list[dict]] = {}
    for c in geo:
        by_name.setdefault(R._norm(c["name"]), []).append(c)
    out = []
    for r in data["rows"]:
        cs = by_name.get(r["settlement"], [])
        if cs and min(F.haversine(r["lat"], r["lon"], *c["center"]) for c in cs) <= 60:
            continue  # аптека города — уже в его базе
        f = {"id": f"reg_{r['code']}", "name": r["network"] or "Аптека", "kind": "pharmacy", "category": "pharmacy",
             "address": ", ".join(x for x in (r["settlement"].capitalize(), R._short_addr(r)) if x),
             "lat": r["lat"], "lon": r["lon"], "phone": "", "hours": "", "source": "registry", "doctors": [],
             "brand": r["network"], "operator": r["legal"], "inn": r["inn"], "turnover": r["turnover"],
             "in_registry": True, "settlement": r["settlement"].capitalize(), "outside": True}
        _assign(f, geo)
        out.append(f)
    PH_FILE.write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M"), "facilities": out},
                                  ensure_ascii=False), encoding="utf-8")
    return {"pharmacies": len(out), "suburbs": sum(1 for f in out if f["near_city"])}


def outside() -> tuple[list[dict], list[dict]]:
    """(все учреждения вне городов, данные городов). Кеш до изменения файлов."""
    with _lock:
        st = _stamp()
        if _cache["stamp"] == st and _cache["outside"] is not None:
            return _cache["outside"], _cache["cities"]
        geo = cities_geo()
        if not PH_FILE.exists():
            try:
                build_pharmacies()
            except RuntimeError:
                pass
        res: list[dict] = []
        if PH_FILE.exists():
            res += json.loads(PH_FILE.read_text(encoding="utf-8"))["facilities"]
        city_ids = set().union(*(c["ids"] for c in geo)) if geo else set()
        seen = set()
        if LPU_FILE.exists():   # реестр ЛПУ загружен: ЛПУ вне городов — только из него
            for f in json.loads(LPU_FILE.read_text(encoding="utf-8"))["facilities"]:
                if f["id"] in city_ids or f["id"] in seen:
                    continue
                seen.add(f["id"])
                res.append(f)
        for p in ([] if LPU_FILE.exists() else F.SUBURBS_DIR.glob("*.json")):
            for f in json.loads(p.read_text(encoding="utf-8")).get("facilities", []):
                # из пригородов OSM берём только ЛПУ: аптеки вне городов — из реестра
                if f["kind"] != "lpu" or f["id"] in city_ids or f["id"] in seen:
                    continue
                seen.add(f["id"])
                f = {**f, "outside": True}
                _assign(f, geo)
                res.append(f)
        from . import field_data as FD
        for d in FD._imports():
            for r in d["facilities"]:
                if r.get("city") or r.get("match") != "вне городов" or r.get("lat") is None:
                    continue
                fid = FD._fid(d["key"], {**r, "city": "out"})
                if fid in seen:
                    continue
                seen.add(fid)
                f = {"id": fid, "name": r["name"], "kind": "lpu", "category": FD._category(r["full_name"]),
                     "address": r["address"], "lat": r["lat"], "lon": r["lon"], "phone": "", "hours": "",
                     "source": "field", "doctors": [], "outside": True, "settlement": r["settlement"]}
                _assign(f, geo)
                res.append(f)
        _cache.update(stamp=_stamp(), cities=geo, outside=res)
        return res, geo


def near(slug: str) -> list[dict]:
    """Учреждения пригорода города."""
    out, _ = outside()
    return [f for f in out if f.get("near_city") == slug]


def status() -> dict:
    out, geo = outside()
    return {"pharmacies": sum(1 for f in out if f["kind"] == "pharmacy"),
            "lpu": sum(1 for f in out if f["kind"] == "lpu"),
            "suburbs_loaded": len(list(F.SUBURBS_DIR.glob("*.json"))), "cities": len(geo),
            "lpu_registry": LPU_FILE.exists(),
            "missing_cities": len(missing()), **_job}


# ─── Фоновая загрузка ЛПУ пригородов всех городов ────────────────────────────

_job = {"running": False, "done": 0, "total": 0, "current": "", "failed": [], "partial": 0}


CITY_BUDGET_S = 6 * 60   # не дольше 6 минут на город; недокачанное — в «дозагрузить»


def missing() -> dict[str, list[str]]:
    """Города, у которых часть кольца не загрузилась: slug → квадраты."""
    out = {}
    for p in F.SUBURBS_DIR.glob("*.json"):
        try:
            m = json.loads(p.read_text(encoding="utf-8")).get("missing_tiles") or []
        except Exception:
            continue
        if m:
            out[p.stem] = m
    return out


def start_suburbs(retry: bool = False) -> dict:
    """Загрузить ЛПУ пригородов всех городов; retry — только недокачанные части."""
    if LPU_FILE.exists():   # ЛПУ пригородов уже из реестра ЛПУ — OpenStreetMap не нужен
        from . import resume
        resume.mark("suburbs", False)
        return dict(_job)
    with _lock:
        if _job["running"]:
            return dict(_job)
        if retry:
            miss = missing()
            todo = [r for r in index() if r["slug"] in miss]
        else:
            have = {p.stem for p in F.SUBURBS_DIR.glob("*.json")}
            todo = [r for r in index() if r["slug"] not in have and F.city_path(r["slug"]).exists()]
            miss = {}
        # сначала небольшие города: они загружаются за секунды, крупные кольца — в конце
        todo.sort(key=lambda r: r.get("population") or 0)
        _job.update(running=True, done=0, total=len(todo), current="", failed=[], partial=0)
    from . import resume
    if not todo:
        resume.mark("suburbs", False)
    elif not retry:
        resume.mark("suburbs", True)   # перезапуск сервера не потеряет загрузку

    def run():
        try:
            for r in todo:
                _job["current"] = r["name"]
                try:
                    F.fetch_suburbs(r, budget_s=CITY_BUDGET_S, only=miss.get(r["slug"]) if retry else None)
                    if (F.SUBURBS_DIR / f"{r['slug']}.json").exists() and \
                            json.loads((F.SUBURBS_DIR / f"{r['slug']}.json").read_text(encoding="utf-8")).get("missing_tiles"):
                        _job["partial"] = _job.get("partial", 0) + 1
                except Exception as ex:  # noqa: BLE001
                    _job["failed"].append({"name": r["name"], "error": str(ex)[:200]})
                _job["done"] += 1
                time.sleep(2)  # бережём общий сервер Overpass
        finally:
            _job["running"] = False
            _job["current"] = ""
            if not retry and _job["done"] >= _job["total"]:
                from . import resume
                resume.mark("suburbs", False)   # все города прошли

    threading.Thread(target=run, daemon=True).start()
    return dict(_job)
