"""Общая база ЛПУ и аптек по выбранным городам: сводка и выгрузка в Excel."""
from __future__ import annotations

import io
from collections import Counter, defaultdict
from hashlib import blake2s

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import facilities as F
from . import norms as N
from . import care
from .cities import index
from .networks import annotate
from .doctor_stats import calibration

CATS_LPU = ["polyclinic_adult", "polyclinic_child", "hospital", "private_center", "cardio_center", "womens", "dental"]
OWN = {"state": "гос./муниц.", "private": "частная"}


_meta: dict[str, dict] = {}

# Врачи всех специальностей в ЛПУ — для итога «врачей всего», когда специальность не выбрана.
# Считать долго (все специальности × все ЛПУ), поэтому кеш: по городу — до изменения файла города,
# вне городов — по учреждению.
_alld_city: dict[str, tuple[float, dict[str, int]]] = {}
_alld_out: dict[tuple[str, str | None], int] = {}
_ALLD_FILE = F.DATA_DIR / "cache" / f"doctors_all_{len(N.SPECIALTIES)}.json"  # новый набор специальностей — новый кеш


def _alld_load() -> None:
    """Кеш с диска: после перезапуска «врачи всех специальностей» не пересчитываются минуту заново."""
    import json as _json
    try:
        d = _json.loads(_ALLD_FILE.read_text(encoding="utf-8"))
        _alld_city.update({k: (v[0], v[1]) for k, v in d.get("city", {}).items()})
        _alld_out.update({(k.split("|")[0], k.split("|")[1] or None): v for k, v in d.get("out", {}).items()})
    except Exception:  # noqa: BLE001 — нет файла или испорчен: просто посчитаем заново
        pass


def alld_save() -> None:
    import json as _json
    try:
        _ALLD_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ALLD_FILE.write_text(_json.dumps({"city": {k: [v[0], v[1]] for k, v in _alld_city.items()},
                                           "out": {f"{k[0]}|{k[1] or ''}": v for k, v in _alld_out.items()}}),
                              encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


_alld_load()


def _care_cache_key(selected: str) -> str:
    return f"ambulatory-v{care.AMBULATORY_REVISION}" if selected == "ambulatory" else selected


def _all_docs_city(slug: str, facilities: list[dict], selected: str = "all") -> dict[str, int]:
    st = F.city_path(slug).stat().st_mtime
    split = selected in ("ambulatory", "hospital")
    key = f"{slug}:{_care_cache_key(selected)}" if split else slug
    hit = _alld_city.get(key)
    if hit and hit[0] == st:
        return hit[1]
    totals = _all_docs_city(slug, facilities) if split else {}
    classified = annotate([dict(f) for f in facilities if f["kind"] == "lpu"])
    cals = {s: calibration(slug, s, facilities=classified) for s in N.SPECIALTIES}
    res = {}
    for f in facilities:
        if f["kind"] != "lpu":
            continue
        actual = care.level(f)
        if split and not care.matches(actual, selected):
            continue
        if split and not care.uses_partial_count(actual, selected):
            res[f["id"]] = totals.get(f["id"], 0)
            continue
        res[f["id"]] = sum(care.count(len(N.doctors_in(f, s, calib=cals[s])), actual, selected)
                           for s in N.SPECIALTIES)
    _alld_city[key] = (st, res)
    return res


def _all_docs_out(f: dict, slug: str | None, selected: str) -> int:
    actual = care.level(f)
    key = (f["id"], f"{slug or ''}:{_care_cache_key(selected)}" if care.uses_partial_count(actual, selected) else slug)
    if key not in _alld_out:
        n = 0
        for s in N.SPECIALTIES:
            cal = None
            if slug:
                c = calibration(slug, s)
                cal = {"counts": {} if c["counts"] is not None else None, "scale": c["scale"]}
            n += care.count(len(N.doctors_in(f, s, calib=cal)), actual, selected)
        _alld_out[key] = n
    return _alld_out[key]


def _selected(q: dict, doctor_specs: tuple[str, ...] | None = None, *, point_context: bool = False):
    """Учреждения с учётом фильтров, по одной строке сводки за раз.

    scope: «city» — только города; «suburbs» — город с пригородом; «region» — область целиком
    (все её города, пригороды и остальная территория)."""
    from . import territory as T
    loaded = {c["slug"]: c for c in F.list_cities()}
    slugs = [s_ for s_ in (q.get("cities") or list(loaded)) if s_ in loaded]
    idx = {r["slug"]: r for r in index()}
    kinds = set(q.get("kinds") or ["lpu", "pharmacy"])
    lpu_own = set(q.get("lpu_ownership") or ["state", "private"])
    ph_own = set(q.get("pharmacy_ownership") or ["state", "private"])
    cats = set(q.get("categories") or CATS_LPU)
    if "polyclinic_adult" in cats:
        cats.add("ambulatory")  # врачебные амбулатории фильтруются вместе с поликлиниками
    spec = q.get("specialty") or ""
    only_target = bool(spec and q.get("only_target"))
    count_specs = list(doctor_specs or ())
    if spec and (doctor_specs is None or only_target) and spec not in count_specs:
        count_specs.append(spec)
    if "lpu" not in kinds:
        count_specs.clear()
    min_turn = q.get("pharmacy_min_turnover") or 0
    ph_nets = set(q["pharmacy_networks"]) if q.get("pharmacy_networks") else None
    scope = q.get("scope") or "city"
    selected = q.get("lpu_care") or "all"

    def doctor_count(f: dict, s: str, actual: str, cals: dict, overrides: dict) -> int:
        estimate = overrides.get(s, {}).get(f["id"])
        if estimate is None:
            estimate = len(N.doctors_in(f, s, calib=cals.get(s)))
        return care.count(estimate, actual, selected)

    def keep(fac: list[dict], cals: dict, overrides: dict) -> list[dict]:
        out = []
        for f in fac:
            if f.get("needs_coords") or f["kind"] not in kinds:
                continue
            if f["kind"] == "lpu":
                if f["category"] not in cats or f["ownership"] not in lpu_own:
                    continue
                if F.excluded_name(f["name"]):
                    continue
                actual = care.level(f)
                if not care.matches(actual, selected):
                    continue
                f = dict(f)
                f["est"] = doctor_count(f, spec, actual, cals, overrides) if spec in count_specs else None
                if only_target and not f["est"]:
                    continue  # целевой список: только ЛПУ, где есть врачи специальности
                if doctor_specs:
                    f["export_doctors"] = [f["est"] if s == spec and f["est"] is not None
                                           else doctor_count(f, s, actual, cals, overrides) for s in doctor_specs]
            elif f["ownership"] not in ph_own or f.get("in_registry") is False:
                continue  # аптеки карты, которых нет в реестре, не выгружаем
            elif min_turn and (f.get("turnover") or 0) < min_turn:
                continue
            elif ph_nets is not None and (f.get("network_name") or "Без сети") not in ph_nets:
                continue
            out.append(f)
        return out

    client = q.get("client_base")
    if client:
        from . import client_bases as CB
        client_slugs = CB.cities_of(client)
        client_city_set = set(client_slugs)
        slugs = [s_ for s_ in (q.get("cities") or client_slugs) if s_ in loaded and s_ in client_city_set]

    nearby_cals = {}

    def city_fac(slug: str) -> list[dict]:
        ds = F.load_city(slug)
        all_doctors = doctor_specs is None and not spec and "lpu" in kinds
        alld = _all_docs_city(slug, ds["facilities"], selected) if all_doctors and not client else {}
        if client:
            classified = annotate([dict(f) for f in ds["facilities"] if f["kind"] == "lpu"]) if count_specs else []
            ds = CB.with_client(ds, client)
        facilities = annotate(ds["facilities"])
        cals = {s: calibration(slug, s, facilities=classified if client else facilities) for s in count_specs}
        if scope != "city":
            nearby_cals[slug] = {s: {"counts": {} if c["counts"] is not None else None, "scale": c["scale"]}
                                 for s, c in cals.items()}
        overrides = {s: CB.doctor_counts(facilities, slug, s, calib=cals[s]) for s in count_specs} if client else {}
        fac = keep(facilities, cals, overrides)
        if point_context:
            for f in fac:
                f["_point_slug"] = slug
        if not all_doctors:
            return fac
        if not client:
            for f in fac:
                if f["kind"] == "lpu":
                    f["est"] = alld.get(f["id"], 0)
        else:
            for f in fac:
                if f["kind"] == "lpu":
                    actual = care.level(f)
                    f["est"] = sum(care.count(n, actual, selected)
                                   for n in (f.get("client_doctors") or {}).values())
        return fac

    def outside_fac(items: list[dict], slug: str | None) -> list[dict]:
        # у пригородов своей статистики врачей нет — коэффициент ближайшего города
        fac = keep(annotate([dict(f) for f in items]), nearby_cals.get(slug, {}), {})
        if point_context:
            for f in fac:
                f["_point_slug"] = slug
        if not spec and doctor_specs is None:
            for f in fac:
                if f["kind"] == "lpu":
                    f["est"] = _all_docs_out(f, slug, selected)
        return fac

    if scope == "region":
        out_all, geo = T.outside()
        sel_regions = q.get("regions") or sorted({idx.get(s_, {}).get("region", "") for s_ in slugs})
        outside_by_area = defaultdict(list)
        for f in out_all:
            outside_by_area[f["region"], f.get("near_city") or None].append(f)
        for reg in sel_regions:
            cs = [c for c in geo if c["region"] == reg]
            fac = []
            for c in cs:
                for f in city_fac(c["slug"]):
                    f["_city"] = c["name"]   # для подписи точки на карте
                    fac.append(f)
            for c in cs:
                fac += outside_fac(outside_by_area.get((reg, c["slug"]), []), c["slug"])
            fac += outside_fac(outside_by_area.get((reg, None), []), cs[0]["slug"] if cs else None)
            if not cs and not fac:
                continue
            ctr = [sum(c["center"][0] for c in cs) / len(cs), sum(c["center"][1] for c in cs) / len(cs)] if cs else None
            _meta[reg] = {"slug": f"region:{reg}", "center": ctr, "population": None,
                          "cities": len(cs), "outside": sum(1 for f in fac if f.get("outside"))}
            yield reg, reg, fac
        return
    for slug in slugs:
        ds_name = loaded[slug]["name"]
        fac = city_fac(slug)
        if scope == "suburbs":
            fac += outside_fac(T.near(slug), slug)
        _meta[ds_name] = {"slug": slug, "center": None, "population": idx.get(slug, {}).get("population"),
                          "outside": sum(1 for f in fac if f.get("outside"))}
        try:
            _meta[ds_name]["center"] = F.load_city(slug).get("center")
        except Exception:
            pass
        yield ds_name, idx.get(slug, {}).get("region", ""), fac


_sum_cache: dict[str, tuple[float, dict]] = {}
_SUM_FILE = F.DATA_DIR / "cache" / "base_summary.json"   # сводки переживают перезапуск сервера
_sum_busy: set[str] = set()


def _key(q: dict) -> str:
    """Ключ кеша: «все типы ЛПУ» списком и без списка — один и тот же запрос."""
    import json as _json
    q = dict(q)
    selected = q.get("lpu_care", "all")
    if selected == "all":
        q.pop("lpu_care", None)
    elif selected == "ambulatory":
        q["lpu_care"] = _care_cache_key(selected)
    if q.get("categories") and set(CATS_LPU) <= set(q["categories"]):
        q["categories"] = None
    for k in ("kinds", "lpu_ownership", "pharmacy_ownership"):
        if q.get(k):
            q[k] = sorted(q[k])
    return _json.dumps(q, sort_keys=True, ensure_ascii=False)


def _sum_disk_load() -> None:
    import json as _json
    try:
        d = _json.loads(_SUM_FILE.read_text(encoding="utf-8"))
        for k, v in d.items():
            _sum_cache.setdefault(k, (v[0], v[1]))
    except Exception:  # noqa: BLE001 — нет файла: посчитаем заново
        pass


def _sum_disk_save() -> None:
    import json as _json
    try:
        items = sorted(_sum_cache.items(), key=lambda kv: -kv[1][0])[:12]
        _SUM_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SUM_FILE.with_suffix(".tmp")
        tmp.write_text(_json.dumps({k: [v[0], v[1]] for k, v in items}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_SUM_FILE)
    except Exception:  # noqa: BLE001
        pass


_sum_disk_load()


def totals(q: dict) -> dict:
    """Итоги для главного экрана — сразу, из последней посчитанной сводки (даже если базы с тех пор
    менялись); устаревшая сводка пересчитывается в фоне."""
    import threading
    from . import territory as _T
    key = _key(q)
    stamp = max((m for _, m in _T._stamp()), default=0.0)
    hit = _sum_cache.get(key)
    fresh = bool(hit and hit[0] == stamp)
    if not fresh and key not in _sum_busy:
        _sum_busy.add(key)

        def run():
            try:
                summary(q)
            finally:
                _sum_busy.discard(key)
        threading.Thread(target=run, daemon=True).start()
    t = hit[1]["total"] if hit else None
    return {"fresh": fresh, "lpu": t.get("lpu", 0) if t else None, "pharmacies": t.get("pharmacies", 0) if t else None,
            "doctors": t.get("doctors_all", t.get("doctors", 0)) if t else None}


_pts_cache: dict[str, tuple[tuple, dict, list[list[tuple]]]] = {}


def points(q: dict) -> dict:
    """Все учреждения по фильтрам для карты России: [широта, долгота, тип] — компактно, с кешем."""
    return _point_snapshot(q)[0]


def _point_snapshot(q: dict) -> tuple[dict, list[list[tuple]]]:
    from . import territory as _T
    stamp = _T._stamp()
    key = _key(q)
    hit = _pts_cache.get(key)
    if hit and hit[0] == stamp:
        return hit[1], hit[2]
    cats = {c: i for i, c in enumerate(CATS_LPU + ["ambulatory"])}
    lpu, ph, m_lpu, m_ph = [], [], [], []
    for city, _, fac in _selected(q, point_context=True):
        for f in fac:
            if f.get("lat") is None:
                continue
            info = (f["name"], f.get("address") or "", f["category"], f.get("settlement") if f.get("outside") else (f.get("_city") or city),
                    f.get("ownership", ""), f.get("network_name") or f.get("brand") or "", int(f.get("est") or 0),
                    f.get("care_level"), f["id"], f.get("_point_slug"), bool(f.get("outside")))
            if f["kind"] == "lpu":
                lpu.append([round(f["lat"], 4), round(f["lon"], 4), cats.get(f["category"], 0), 1 if f.get("outside") else 0,
                            int(f.get("est") or 0)])
                m_lpu.append(info)
            else:
                ph.append([round(f["lat"], 4), round(f["lon"], 4), 1 if f["ownership"] == "state" else 0, 1 if f.get("outside") else 0])
                m_ph.append(info)
    revision = blake2s(repr((key, stamp)).encode(), digest_size=16).hexdigest()
    res = {"lpu": lpu, "pharmacies": ph, "revision": revision}
    meta = [m_lpu, m_ph]
    if len(_pts_cache) > 16:
        _pts_cache.clear()
    _pts_cache[key] = (stamp, res, meta)
    return res, meta


def point_info(q: dict, kind: str, i: int, *, revision: str | None = None, detail: bool = False) -> dict | None:
    """Получить сведения о точке из той же версии карты, которую видит пользователь."""
    snapshot, meta = _point_snapshot(q)
    if revision is not None and snapshot["revision"] != revision:
        return None
    lst = meta[0 if kind == "lpu" else 1]
    if not 0 <= i < len(lst):
        return None
    name, addr, cat, city, own, net, docs, actual, fid, slug, outside = lst[i]
    info = {"name": name, "address": addr, "type": F.CATEGORY_LABELS.get(cat, cat), "city": city or "",
            "ownership": OWN.get(own, ""), "network": net, "doctors": docs if kind == "lpu" else None,
            "specialty": q.get("specialty") or "", "care_level": actual}
    if detail:
        facility = _point_facility(fid, slug, outside, q.get("client_base"))
        if facility is None:
            return None
        facility.update(category=cat, care_level=actual, ownership=own, network_name=net)
        info["facility"] = facility_card(facility, slug) | {"settlement": city or ""}
    return info


def _point_facility(fid: str, slug: str | None, outside: bool, client: str | None) -> dict | None:
    if outside:
        from . import territory
        facilities, _ = territory.outside()
    else:
        if not slug:
            return None
        try:
            ds = F.load_city(slug)
            if client:
                from . import client_bases
                ds = client_bases.with_client(ds, client)
            facilities = ds["facilities"]
        except (KeyError, FileNotFoundError):
            return None
    return next((dict(f) for f in facilities if f["id"] == fid), None)


def facility_card(f: dict, slug: str | None) -> dict:
    """Карточка учреждения из города или регионального справочника."""
    doctors = []
    if f["kind"] == "lpu":
        client_doctors = f.get("client_doctors") or {}
        known = f.get("known_doctors") or {}
        for spec in N.SPECIALTIES:
            n = client_doctors.get(spec)
            if n is None:
                cal = calibration(slug, spec) if slug else None
                counts = (cal or {}).get("counts") or {}
                n = counts.get(f["ours_id"]) if f.get("ours_id") else None
                if n is None:
                    n = len(N.doctors_in(f, spec, calib=cal))
            if n:
                doctors.append({"spec": spec, "n": n, "confirmed": int(known.get(spec, 0))})
    keep = ("id", "name", "kind", "category", "care_level", "address", "lat", "lon", "phone", "hours", "ownership",
            "network_name", "operator", "inn", "turnover", "source")
    return {**{k: f.get(k) for k in keep}, "category_label": F.CATEGORY_LABELS.get(f["category"], ""),
            "doctors": sorted(doctors, key=lambda x: -x["n"])}


def summary(q: dict) -> dict:
    """Сводка по городам; повторный запрос с теми же фильтрами берётся из памяти, пока базы не менялись."""
    import json as _json
    # кеш сбрасывается при любом изменении баз: города, пригороды (грузятся в фоне), аптеки вне городов, полевые данные
    from . import territory as _T
    stamp = max((m for _, m in _T._stamp()), default=0.0)
    key = _key(q)
    hit = _sum_cache.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    res = _summary(q)
    if len(_sum_cache) > 64:
        _sum_cache.clear()
    _sum_cache[key] = (stamp, res)
    _sum_disk_save()
    return res


def _summary(q: dict) -> dict:
    rows, total = [], Counter()
    ph_nets: Counter = Counter()
    places: set[tuple[str, str]] = set()   # населённые пункты вне городов базы
    for city, region, fac in _selected(q):
        for f in fac:
            if f.get("outside") and f.get("settlement"):
                places.add((f.get("region") or region, f["settlement"].lower()))
        c = Counter()
        for f in fac:
            if f["kind"] == "lpu":
                c["lpu"] += 1
                c[f"lpu_{f['ownership']}"] += 1
                c[f["category"]] += 1
                c["doctors"] += f.get("est") or 0
            else:
                c["pharmacies"] += 1
                c[f"ph_{f['ownership']}"] += 1
                if f["network"]:
                    ph_nets[f["network_name"]] += 1
        nets = Counter(f["network_name"] for f in fac if f["kind"] == "pharmacy" and f["network"])
        m = _meta.get(city, {})
        turn = sorted(f["turnover"] for f in fac if f["kind"] == "pharmacy" and f.get("turnover"))
        pop = m.get("population")
        rows.append({"city": city, "region": region, **c, "slug": m.get("slug"), "center": m.get("center"),
                     "outside": m.get("outside", 0), "cities_n": m.get("cities"),
                     "population": pop, "turnover_median": turn[len(turn) // 2] if turn else None,
                     "ph_per_10k": round(c["pharmacies"] / pop * 1e4, 1) if pop else None,
                     "doctors_per_10k": round(c["doctors"] / pop * 1e4, 1) if pop and c["doctors"] else None,
                     "top_networks": [n for n, _ in nets.most_common(3)]})
        total.update(c)
    rows.sort(key=lambda r: -(r.get("lpu", 0) + r.get("pharmacies", 0)))
    total["settlements"] = len(places)
    total["doctors_all"] = 0 if q.get("specialty") else total.get("doctors", 0)
    return {"rows": rows, "total": dict(total), "networks": ph_nets.most_common(15),
            "all_networks": [n for n, _ in ph_nets.most_common(80)]}


SOURCE = {"osm": "OpenStreetMap", "import": "импорт", "registry": "реестр аптек", "dsm": "реестр ЛПУ", "field": "данные визитов"}


def workbook(q: dict) -> bytes:
    head_fill = PatternFill("solid", fgColor="1E2A35")
    head_font = Font(bold=True, color="FFFFFF")
    wb = Workbook(write_only=True)

    def sheet(title, headers, widths, freeze="A2"):
        ws = wb.create_sheet(title)
        ws.freeze_panes = freeze
        ws.row_dimensions[1].height = 34
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
        cells = []
        for label in headers:
            cell = WriteOnlyCell(ws, value=label)
            cell.fill, cell.font = head_fill, head_font
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            cells.append(cell)
        ws.append(cells)
        return ws

    spec = q.get("specialty") or ""
    kinds = q.get("kinds") or ["lpu", "pharmacy"]
    include_lpu, include_ph = "lpu" in kinds, "pharmacy" in kinds
    requested_specs = q.get("doctor_specialties")
    if requested_specs is None:  # прежние запросы наследуют выбранную специальность
        requested_specs = [spec] if spec else []
    specs = tuple(s for s in dict.fromkeys(requested_specs) if s in N.SPECIALTIES) if include_lpu else ()
    doctor_headers = [f"{s}, оценка" for s in specs]
    doctor_widths = [max(18, min(29, len(s) + 2)) for s in specs]
    confirmed_spec = specs[0] if len(specs) == 1 else None
    area = "Область" if q.get("scope") == "region" else "Город"
    cols, widths = [area, "Регион"], [24, 25]
    lpu_keys = ["lpu", "state", "private", *CATS_LPU]
    if include_lpu:
        cols += ["ЛПУ всего", "гос. ЛПУ", "частные ЛПУ", "Поликлиники", "Детские", "Больницы", "Медцентры",
                 "Профильные центры", "Женские консультации", "Стоматологии", *doctor_headers]
        widths += [13] * len(lpu_keys) + doctor_widths
    if include_ph:
        cols += ["Аптек всего", "гос. аптеки", "частные аптеки", "Крупнейшие аптечные сети"]
        widths += [13, 13, 13, 40]
    sm = sheet("Сводка", cols, widths)

    lp_headers = ["Город / населённый пункт", "Регион", "Название", "Тип", "Звено ЛПУ", "Собственность", "Сеть", "Адрес",
                  "Телефон", "Часы работы", "Широта", "Долгота", "Источник", *doctor_headers]
    lp_widths = [24, 25, 44, 24, 20, 17, 28, 42, 20, 22, 13, 13, 19, *doctor_widths]
    if confirmed_spec:
        lp_headers.append("Подтверждённое число по ЛПУ")
        lp_widths.append(25)
    lp = sheet("ЛПУ", lp_headers, lp_widths, "D2") if include_lpu else None
    ph_headers = ["Город / населённый пункт", "Регион", "Название", "Собственность", "Сеть", "Юрлицо", "ИНН", "Адрес", "Телефон",
                  "Часы работы", "Широта", "Долгота", "Источник", "В реестре аптек", "Средний оборот на аптеку, руб./мес"]
    ph = sheet("Аптеки", ph_headers, [24, 25, 36, 17, 24, 32, 16, 42, 20, 22, 13, 13, 19, 16, 25], "D2") if include_ph else None
    nets: Counter = Counter()
    net_cities: dict[str, set] = {}
    total = Counter()
    doctor_total = Counter()
    summary_rows = 0

    def summary_row(city, region, counts, doctors, top=""):
        row = [city, region]
        if include_lpu:
            row += [counts[k] for k in lpu_keys] + [doctors[s] for s in specs]
        if include_ph:
            row += [counts["ph"], counts["ph_state"], counts["ph_private"], top]
        return row

    for city, region, fac in _selected(q, specs):
        c = Counter()
        doctor_counts = Counter()
        area_nets = Counter()
        for f in fac:
            src = SOURCE.get(f["source"], f["source"])
            place = f.get("settlement") or f.get("_city") or city
            if f["kind"] == "lpu":
                c["lpu"] += 1
                c[f["ownership"]] += 1
                c[f["category"]] += 1
                estimates = f.get("export_doctors", [])
                doctor_counts.update(dict(zip(specs, estimates)))
                row = [place, region, f["name"], F.CATEGORY_LABELS.get(f["category"], f["category"]),
                       care.LABELS.get(f.get("care_level"), ""), OWN[f["ownership"]], f["network_name"], f["address"],
                       f.get("phone", ""), f.get("hours", ""), f["lat"], f["lon"], src, *estimates]
                if confirmed_spec:
                    row.append((f.get("known_doctors") or {}).get(confirmed_spec))
                lp.append(row)
            else:
                c["ph"] += 1
                c[f"ph_{f['ownership']}"] += 1
                ph.append([place, region, f["name"], OWN[f["ownership"]], f["network_name"], f.get("operator", ""),
                           f.get("inn", ""), f["address"], f.get("phone", ""), f.get("hours", ""), f["lat"], f["lon"],
                           src, {True: "да", False: "нет"}.get(f.get("in_registry"), ""), f.get("turnover")])
                if f["network"]:
                    network = f["network_name"]
                    area_nets[network] += 1
                    net_cities.setdefault(network, set()).add(place)
        nets.update(area_nets)
        top = ", ".join(f"{n} ({k})" for n, k in area_nets.most_common(3))
        sm.append(summary_row(city, region, c, doctor_counts, top))
        summary_rows += 1
        total.update(c)
        doctor_total.update(doctor_counts)
    totals = [WriteOnlyCell(sm, value=v) for v in summary_row("Итого", "", total, doctor_total)]
    for cell in totals:
        cell.font = Font(bold=True)
    sm.append(totals)
    sm.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{summary_rows + 1}"
    if lp is not None:
        lp.auto_filter.ref = f"A1:{get_column_letter(len(lp_headers))}{total['lpu'] + 1}"
    if ph is not None:
        ph.auto_filter.ref = f"A1:{get_column_letter(len(ph_headers))}{total['ph'] + 1}"
        nt = sheet("Аптечные сети", ["Сеть", "Аптек", "Населённых пунктов", "Населённые пункты"], [32, 12, 20, 75])
        for name, count in nets.most_common():
            places = sorted(net_cities[name])
            nt.append([name, count, len(places), ", ".join(places)])
        nt.auto_filter.ref = f"A1:D{len(nets) + 1}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
