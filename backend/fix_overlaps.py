"""
Перезагрузка городов, чьи базы сильно пересекаются с более крупным соседом
(города-спутники без границы в OSM), и удаление дублей между городами.

    backend\\.venv\\Scripts\\python backend\\fix_overlaps.py
"""
import json
import time

from app import cities as C
from app import facilities as F


def overlapping(threshold: float = 0.3) -> list[str]:
    pop = {r["slug"]: r["population"] or 0 for r in C.index()}
    files = sorted((p for p in F.CITIES_DIR.glob("*.json") if not p.name.startswith("_")),
                   key=lambda p: -pop.get(p.stem, 0))
    owner, bad = set(), []
    for p in files:
        fac = json.loads(p.read_text(encoding="utf-8"))["facilities"]
        ids = [f["id"] for f in fac if f["source"] == "osm"]
        dup = sum(1 for i in ids if i in owner)
        if ids and dup / len(ids) > threshold:
            bad.append(p.stem)
        owner.update(ids)
    return bad


def main():
    bad = overlapping()
    print(f"Пересекаются с соседями: {len(bad)} — {', '.join(bad)}", flush=True)
    for slug in bad:
        e = C.entry(slug)
        if not e:
            continue
        try:
            ds = F.fetch_city(e, geocode=False)
            print(f"{e['name']}: {len(ds['facilities'])} учреждений", flush=True)
        except Exception as ex:
            print(f"{e['name']}: ошибка — {ex}", flush=True)
        time.sleep(2)
    d = C.dedupe_all()
    print(f"Убрано дублей: {d['removed']}; районы других городов: {', '.join(d['dropped']) or 'нет'}", flush=True)


if __name__ == "__main__":
    main()
