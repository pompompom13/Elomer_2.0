"""
Базы заказчиков: собственные списки ЛПУ заказчика (можно с числом врачей).

База заказчика хранится отдельно от основной (data/client_bases/<id>.json) и привязывается к проекту.
С ней можно работать так же, как с основной базой — в «Базе» и в «Плане города» она подставляется
вместо наших ЛПУ (аптеки остаются наши).

Сверка с основной базой — тем же способом, что и данные визитов (field_data.match_records):
по адресу, по типу и номеру учреждения, по координатам. Результат:
  * совпало — учреждение есть у нас;
  * новое / вне городов — у нас его нет: встаёт в очередь проверки (pending → accepted / rejected);
  * не найдено — адрес не удалось найти на карте;
  * «нет у заказчика» — наши ЛПУ в городах базы, которых в таблице заказчика нет.

Принятые ЛПУ и числа врачей переносятся в основную базу через отдельную загрузку
«Принято из баз заказчиков» (data/field_data/bazy_zakazchikov.json) и остаются у нас,
даже если базу заказчика удалить.
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
import uuid

from . import facilities as F
from . import field_data as FD

DIR = F.DATA_DIR / "client_bases"
DIR.mkdir(parents=True, exist_ok=True)
ACCEPTED_KEY = "bazy_zakazchikov"
_lock = threading.RLock()
_jobs: dict[str, dict] = {}

MATCHED = ("адрес", "название", "координаты")
NEW = ("новое", "вне городов")

_COLS = {
    "inst": ["учреждение", "наименование учреждения", "название", "наименование", "лпу", "организация"],
    "city": ["город", "населенный пункт", "населённый пункт", "нас. пункт"],
    "region": ["регион", "область", "субъект"],
    "addr": ["адрес", "адрес учреждения", "фактический адрес"],
    "type": ["тип учреждения", "тип", "категория"],
    "lat": ["широта", "lat", "latitude"],
    "lon": ["долгота", "lon", "lng", "longitude"],
    "spec": ["специальность", "таргет-группа", "специализация"],
    "count": ["врачей", "кол-во врачей", "количество врачей", "количество", "число врачей"],
}


def _path(bid: str):
    return DIR / f"{bid}.json"


def _num(v) -> float | None:
    try:
        x = float(str(v).replace(",", ".").strip())
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def read_file(files: list[tuple[str, bytes]]) -> list[dict]:
    """Строки таблицы заказчика: учреждение, город, адрес и (по желанию) врачи по специальностям.
    Врачи — либо колонками-специальностями («Терапевт», «Кардиологи»…), либо парой колонок
    «Специальность» + «Врачей» (строка на специальность)."""
    import pandas as pd
    rows: dict[tuple, dict] = {}
    for fname, content in files:
        if fname.lower().endswith(".csv"):
            sheets = {"csv": pd.read_csv(io.BytesIO(content), dtype=str, header=None, sep=None, engine="python")}
        else:
            sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, dtype=str, header=None)
        for df in sheets.values():
            hdr = None
            for i in range(min(10, len(df))):
                vals = [FD._norm(v) for v in df.iloc[i].tolist()]
                if any(v in vals for v in _COLS["inst"]) and any(v in vals for v in _COLS["city"] + _COLS["addr"]):
                    hdr = i
                    break
            if hdr is None:
                continue
            names = [FD._norm(v) for v in df.iloc[hdr].tolist()]
            idx = {k: next((names.index(a) for a in al if a in names), None) for k, al in _COLS.items()}
            used = {i for i in idx.values() if i is not None}
            # колонки-специальности с числами
            spec_cols = {i: FD.map_spec(n) for i, n in enumerate(names) if i not in used and n and FD.map_spec(n)}
            for r in df.iloc[hdr + 1:].itertuples(index=False):
                def get(k):
                    i = idx.get(k)
                    return str(r[i]).strip() if i is not None and r[i] == r[i] else ""
                inst = get("inst")
                if not inst:
                    continue
                city = re.sub(r"^deprecated_", "", get("city"), flags=re.I).strip()
                key = (FD._norm(city), FD._norm(inst), FD._norm(get("addr")))
                row = rows.setdefault(key, {
                    "ref": len(rows), "settlement": city, "inst": inst, "addr": get("addr"), "counts": {},
                    "hint_city": "", "region": get("region"), "type": get("type"),
                    "lat": _num(get("lat")), "lon": _num(get("lon"))})
                for i, sp in spec_cols.items():
                    n = _num(r[i])
                    if n:
                        row["counts"][sp] = max(row["counts"].get(sp, 0), int(n))
                sp, n = FD.map_spec(get("spec")) if get("spec") else None, _num(get("count"))
                if sp and n:
                    row["counts"][sp] = max(row["counts"].get(sp, 0), int(n))
    out = list(rows.values())
    for r in out:
        if r["lat"] is None or r["lon"] is None or not (-90 <= r["lat"] <= 90):
            r["lat"] = r["lon"] = None
    return out


# ─── Хранение ────────────────────────────────────────────────────────────────

def get(bid: str) -> dict:
    p = _path(bid)
    if not p.exists():
        raise KeyError(bid)
    return json.loads(p.read_text(encoding="utf-8"))


def _save(d: dict) -> None:
    with _lock:
        _path(d["id"]).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def summary(d: dict) -> dict:
    fac = d.get("facilities") or []
    c = lambda pred: sum(1 for f in fac if pred(f))  # noqa: E731
    return {
        "id": d["id"], "name": d["name"], "project_id": d.get("project_id"), "created": d["created"],
        "rows": d.get("rows", 0), "cities": len({f["city"] for f in fac if f.get("city")}),
        "matched": c(lambda f: f["match"] in MATCHED),
        "new": c(lambda f: f["match"] == "новое"), "outside": c(lambda f: f["match"] == "вне городов"),
        "not_found": c(lambda f: f["match"] == "не найдено"),
        "pending": c(lambda f: f["match"] in NEW and f.get("review", "pending") == "pending"),
        "accepted": c(lambda f: f.get("review") == "accepted"),
        "rejected": c(lambda f: f.get("review") == "rejected"),
        "with_doctors": c(lambda f: bool(f.get("counts"))),
        "job": dict(_jobs.get(d["id"]) or {"running": False}),
    }


def list_all(project_id: str | None = None) -> list[dict]:
    out = []
    for p in sorted(DIR.glob("*.json"), key=lambda p: -p.stat().st_mtime):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if project_id and d.get("project_id") != project_id:
            continue
        out.append(summary(d))
    for bid, j in _jobs.items():  # ещё считается — файла пока нет
        if j.get("running") and not _path(bid).exists():
            out.insert(0, {"id": bid, "name": j.get("name", ""), "project_id": j.get("project_id"),
                           "created": j.get("started", ""), "job": dict(j)})
    return out


def delete(bid: str) -> None:
    """Удалить базу заказчика. Уже принятое в основную базу остаётся."""
    _path(bid).unlink(missing_ok=True)


def start_import(name: str, project_id: str | None, files: list[tuple[str, bytes]]) -> dict:
    bid = uuid.uuid4().hex[:10]
    job = {"running": True, "stage": "Читаю файл", "done": 0, "total": 0, "error": "", "name": name,
           "project_id": project_id, "started": time.strftime("%Y-%m-%d %H:%M")}
    _jobs[bid] = job

    def run():
        try:
            raw = read_file(files)
            if not raw:
                raise ValueError("В файле не нашлось таблицы с колонками «Учреждение» и «Город» или «Адрес».")
            out, skipped, settlements = FD.match_records(raw, job)
            for i, f in enumerate(out):
                f["id"] = f"{bid}_{i}"
                if f["match"] in NEW:
                    f["review"] = "pending"
            _save({"id": bid, "name": name, "project_id": project_id, "created": time.strftime("%Y-%m-%d %H:%M"),
                   "rows": len(raw), "facilities": out, "skipped_cities": skipped, "settlements": settlements})
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)
        finally:
            job["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return {"id": bid, **job}


def status(bid: str) -> dict:
    return dict(_jobs.get(bid) or {"running": False, "error": ""})


# ─── Сверка ──────────────────────────────────────────────────────────────────

def report(bid: str) -> dict:
    """Сверка с основной базой: новые (очередь проверки), совпавшие с расхождениями по врачам,
    не найденные и наши ЛПУ, которых нет у заказчика."""
    from .doctor_stats import calibration
    from . import norms as N
    d = get(bid)
    fac = d["facilities"]
    from .cities import index
    names = {e["slug"]: e["name"] for e in index()}
    our_cache: dict[str, dict] = {}

    def ours(slug):
        if slug not in our_cache:
            try:
                our_cache[slug] = {f["id"]: f for f in F.load_city(slug)["facilities"] if f["kind"] == "lpu"}
            except KeyError:
                our_cache[slug] = {}
        return our_cache[slug]

    new = [{**f, "city_name": names.get(f.get("city") or "", f.get("settlement", ""))} for f in fac if f["match"] in NEW]
    # соседи нового ЛПУ в нашей базе — помогают понять, не дубль ли это
    for f in new:
        if f.get("city") and f.get("lat") is not None:
            near = sorted(((F.haversine(f["lat"], f["lon"], o["lat"], o["lon"]) * 1000, o) for o in ours(f["city"]).values()),
                          key=lambda t: t[0])[:2]
            f["near"] = [{"name": o["name"], "address": o.get("address", ""), "m": round(m), "lat": o["lat"], "lon": o["lon"]}
                         for m, o in near if m < 1500]
    diffs = []
    for f in fac:
        if f["match"] not in MATCHED or not f.get("counts"):
            continue
        o = ours(f["city"]).get(f["osm_id"])
        if not o:
            continue
        for spec, n in f["counts"].items():
            est = len(N.doctors_in(o, spec, calib=calibration(f["city"], spec)))
            if n != est:
                diffs.append({"id": f["id"], "name": f["name"], "osm_name": f["osm_name"], "city_name": names.get(f["city"], ""),
                              "spec": spec, "client": n, "ours": est, "accepted": spec in (f.get("doctors_accepted") or [])})
    matched_ids = {f["osm_id"] for f in fac if f["match"] in MATCHED}
    missing = []
    for slug in sorted({f["city"] for f in fac if f.get("city")}):
        for o in ours(slug).values():
            if o["id"] not in matched_ids and not F.excluded_name(o["name"]) and o.get("source") != "registry":
                missing.append({"city_name": names.get(slug, slug), "name": o["name"], "address": o.get("address", "")})
    return {
        **summary(d),
        "new_items": new,
        "matched_items": [{"id": f["id"], "name": f["name"], "osm_name": f["osm_name"], "match": f["match"], "address": f["address"],
                     "city_name": names.get(f["city"], "")} for f in fac if f["match"] in MATCHED],
        "not_found_items": [{"id": f["id"], "name": f["name"], "address": f["address"], "city_name": names.get(f.get("city") or "", f.get("settlement", ""))}
                      for f in fac if f["match"] == "не найдено"],
        "doctor_diffs": diffs,
        "missing": missing,
        "skipped_cities": d.get("skipped_cities", []),
    }


def _accepted_store() -> dict:
    p = FD.DIR / f"{ACCEPTED_KEY}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"key": ACCEPTED_KEY, "title": "Принято из баз заказчиков", "created": time.strftime("%Y-%m-%d %H:%M"),
            "facilities": [], "skipped_cities": [], "settlements": []}


def review(bid: str, ids: list[str] | None, action: str, doctors: list[dict] | None = None, who: str = "") -> dict:
    """Принять / отклонить новые ЛПУ (ids=None — все ожидающие) или принять числа врачей
    (doctors=[{id, spec}]). Принятое переносится в основную базу."""
    with _lock:
        d = get(bid)
        store = _accepted_store()
        have = {(r.get("src"), r.get("src_id")) for r in store["facilities"]}
        stamp = time.strftime("%Y-%m-%d %H:%M")
        changed = 0
        by_id = {f["id"]: f for f in d["facilities"]}
        if doctors:
            for x in doctors:
                f = by_id.get(x["id"])
                if not f or f["match"] not in MATCHED or x["spec"] not in (f.get("counts") or {}):
                    continue
                acc = f.setdefault("doctors_accepted", [])
                if x["spec"] in acc:
                    continue
                acc.append(x["spec"])
                store["facilities"].append({**{k: f[k] for k in ("city", "settlement", "name", "full_name", "address",
                                                                   "osm_id", "osm_name", "lat", "lon", "match")},
                                            "counts": {x["spec"]: f["counts"][x["spec"]]},
                                            "src": bid, "src_id": f"{f['id']}:{x['spec']}", "accepted": stamp, "by": who})
                changed += 1
        else:
            for f in d["facilities"]:
                if f["match"] not in NEW or f.get("review", "pending") != "pending":
                    continue
                if ids is not None and f["id"] not in ids:
                    continue
                if action == "reject":
                    f["review"] = "rejected"
                    changed += 1
                    continue
                if f.get("lat") is None:
                    continue
                f["review"] = "accepted"
                f["accepted"] = stamp
                if (bid, f["id"]) not in have:
                    store["facilities"].append({**{k: f.get(k) for k in ("city", "settlement", "region", "name", "full_name",
                                                                         "address", "osm_id", "osm_name", "lat", "lon",
                                                                         "match", "counts", "approx")},
                                                "src": bid, "src_id": f["id"], "accepted": stamp, "by": who})
                changed += 1
        _save(d)
        if changed and (action == "accept" or doctors):
            (FD.DIR / f"{ACCEPTED_KEY}.json").write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
            FD.apply_all()
        return {"changed": changed, **summary(d)}


# ─── База заказчика как источник учреждений ───────────────────────────────────

def city_lpu(bid: str, slug: str) -> list[dict]:
    """ЛПУ города из базы заказчика в формате основной базы. Координаты и тип — от нашего
    совпавшего учреждения, если оно есть; число врачей заказчика — в client_doctors."""
    d = get(bid)
    try:
        ours = {f["id"]: f for f in F.load_city(slug)["facilities"] if f["kind"] == "lpu"}
    except KeyError:
        ours = {}
    out = []
    for f in d["facilities"]:
        if f.get("city") != slug or f.get("lat") is None:
            continue
        o = ours.get(f.get("osm_id") or "")
        cat = o["category"] if o else FD._category(f["full_name"])
        out.append({
            "id": f"cb_{f['id']}", "name": f["name"], "kind": "lpu", "category": cat,
            "type": (o or {}).get("type") or f["full_name"],
            "address": f["address"] or (o or {}).get("address", ""), "lat": f["lat"], "lon": f["lon"],
            "phone": (o or {}).get("phone", ""), "hours": (o or {}).get("hours", ""), "source": "client",
            "doctors": [], "operator": (o or {}).get("operator"), "brand": (o or {}).get("brand"),
            "client_doctors": dict(f.get("counts") or {}), "ours_id": o["id"] if o else None,
        })
    return out


def cities_of(bid: str) -> list[str]:
    return sorted({f["city"] for f in get(bid)["facilities"] if f.get("city")})


def with_client(ds: dict, bid: str | None) -> dict:
    """Город с ЛПУ заказчика вместо наших (аптеки — наши)."""
    if not bid:
        return ds
    return {**ds, "facilities": city_lpu(bid, ds["slug"]) + [f for f in ds["facilities"] if f["kind"] == "pharmacy"],
            "client_base": bid}


def doctor_counts(facilities: list[dict], slug: str, spec: str, *, calib: dict | None = None) -> dict[str, int]:
    """Число врачей специальности для ЛПУ заказчика: из его таблицы, иначе — как у нашего
    совпавшего учреждения (по статистике города). Новые ЛПУ без чисел оцениваются по типу."""
    from .doctor_stats import calibration
    cal = calibration(slug, spec) if calib is None else calib
    counts = cal.get("counts") or {}
    out = {}
    for f in facilities:
        if f.get("source") != "client":
            continue
        if spec in (f.get("client_doctors") or {}):
            out[f["id"]] = int(f["client_doctors"][spec])
        elif f.get("ours_id") and f["ours_id"] in counts:
            out[f["id"]] = int(counts[f["ours_id"]])
    return out


def report_xlsx(bid: str) -> bytes:
    """Сверка в Excel: новые, совпавшие, расхождения по врачам, не найденные, нет у заказчика."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    r = report(bid)
    wb = Workbook()
    fill, font = PatternFill("solid", fgColor="2A52AD"), Font(bold=True, color="FFFFFF")
    st = {"pending": "на проверке", "accepted": "принято", "rejected": "отклонено"}

    def sheet(title, head, rows, widths, first=False):
        ws = wb.active if first else wb.create_sheet(title)
        ws.title = title
        ws.append(head)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
            ws.cell(1, i).fill, ws.cell(1, i).font = fill, font
        for x in rows:
            ws.append(x)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    sheet("Новые", ["Город / посёлок", "Учреждение", "Адрес", "Где", "Статус", "Широта", "Долгота"],
          [[f["city_name"], f["name"], f["address"], "город базы" if f["match"] == "новое" else "вне городов",
            st.get(f.get("review", "pending"), ""), f.get("lat"), f.get("lon")] for f in r["new_items"]],
          [22, 46, 40, 14, 14, 11, 11], first=True)
    sheet("Совпало", ["Город", "Учреждение заказчика", "Наше учреждение", "Как совпало", "Адрес"],
          [[f["city_name"], f["name"], f["osm_name"], f["match"], f["address"]] for f in r["matched_items"]], [20, 44, 44, 13, 40])
    sheet("Врачи расходятся", ["Город", "Учреждение", "Специальность", "У заказчика", "Наша оценка", "Принято"],
          [[x["city_name"], x["name"], x["spec"], x["client"], x["ours"], "да" if x["accepted"] else ""] for x in r["doctor_diffs"]],
          [20, 44, 18, 12, 12, 9])
    sheet("Не найдено", ["Город / посёлок", "Учреждение", "Адрес"],
          [[x["city_name"], x["name"], x["address"]] for x in r["not_found_items"]], [22, 46, 44])
    sheet("Нет у заказчика", ["Город", "Наше учреждение", "Адрес"],
          [[x["city_name"], x["name"], x["address"]] for x in r["missing"]], [20, 46, 44])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
