"""
Загрузка баз учреждений всех городов справочника из OpenStreetMap.

    backend\\.venv\\Scripts\\python backend\\preload_all.py          # только незагруженные
    backend\\.venv\\Scripts\\python backend\\preload_all.py --all    # обновить все

Адреса, которых нет в OSM, дозаполняются при первом открытии города в приложении.
"""
import sys
import time

from app import cities as C
from app import facilities as F


def main():
    refresh = "--all" in sys.argv
    loaded = {c["slug"] for c in F.list_cities()}
    todo = [r for r in C.index() if refresh or r["slug"] not in loaded]
    print(f"Городов к загрузке: {len(todo)}", flush=True)
    failed = []
    for i, r in enumerate(todo, 1):
        t = time.time()
        try:
            ds = F.fetch_city(r, geocode=False)
            fac = ds["facilities"]
            print(f"{i}/{len(todo)} {r['name']}: {sum(f['kind'] == 'lpu' for f in fac)} ЛПУ, "
                  f"{sum(f['kind'] == 'pharmacy' for f in fac)} аптек, {time.time() - t:.0f} с", flush=True)
        except Exception as e:
            failed.append(r["name"])
            print(f"{i}/{len(todo)} {r['name']}: ошибка — {e}", flush=True)
        time.sleep(2)
    d = C.dedupe_all()
    print(f"Убрано дублей между городами: {d['removed']}; районы других городов: {', '.join(d['dropped']) or 'нет'}")
    print("Готово." + (f" Не загрузились: {', '.join(failed)}" if failed else ""), flush=True)


if __name__ == "__main__":
    main()
