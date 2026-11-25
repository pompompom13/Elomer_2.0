"""
Планировщик проекта: кто, когда и в каком порядке посещает реальные ЛПУ и аптеки.

Схема:
 1. Рабочие дни проекта по производственному календарю.
 2. Пул целей: врачи нужной специальности в учреждениях города (оценка или загруженный список)
    и аптеки. При нескольких МП город делится на секторы с равным числом врачей.
 3. Каждый МП берёт столько врачей, сколько нужно для плана, начиная с ближайших к точке старта.
 4. При заданной цели по охвату группы врачей повторяются в каждом цикле визитов.
    Без цели объём набирается по числу рабочих дней каждого периода и заданной
    нагрузке, а визиты распределяются по датам с соблюдением кратности.
 5. К каждой группе привязываются ближайшие аптеки, чтобы аптечные визиты были по пути.
 6. Порядок объезда внутри дня — ближайший сосед + 2-opt; время — по нормативам;
    риск переработки — Монте-Карло по каждому дню.
"""
from __future__ import annotations

import math
import re
import hashlib
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta

import numpy as np

from . import norms as N
from . import care
from .calendar_ru import add_months, calendar_is_official, frequency_unit, project_months, working_days
from .facilities import haversine, load_city
from .networks import annotate
from .territories import partition
from .doctor_stats import calibration
from .advisor import advise
from .coverage import distribute, specialties

REP_COLORS = ["#2A52AD", "#C2562E", "#0E8AA3", "#6A4FA3", "#B8862B", "#3D84D6", "#8E3B5E", "#6E7F1F"]  # проверено на различимость, в т.ч. при дальтонизме
MAX_PH_KM = 5.0  # дальше аптека уже не «по пути» к врачам дня
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


# ─── Вспомогательное ─────────────────────────────────────────────────────────

def _hm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def _parse_hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _angle(c, p) -> float:
    return math.atan2(p[0] - c[0], (p[1] - c[1]) * math.cos(math.radians(c[0])))


def _route_order(home, pts: list[tuple[float, float]]) -> list[int]:
    """Открытый маршрут из home через все точки: ближайший сосед + 2-opt."""
    n = len(pts)
    if n <= 1:
        return list(range(n))
    nodes = [home] + pts
    d = [[haversine(a[0], a[1], b[0], b[1]) for b in nodes] for a in nodes]
    order, left, cur = [], set(range(1, n + 1)), 0
    while left:
        nxt = min(left, key=lambda j: d[cur][j])
        order.append(nxt)
        left.remove(nxt)
        cur = nxt
    path = [0] + order
    improved = True
    while improved:
        improved = False
        for i in range(1, len(path) - 1):
            for k in range(i + 1, len(path)):
                a, b = path[i - 1], path[i]
                c = path[k]
                e = path[k + 1] if k + 1 < len(path) else None
                old = d[a][b] + (d[c][e] if e is not None else 0)
                new = d[a][c] + (d[b][e] if e is not None else 0)
                if new < old - 1e-9:
                    path[i:k + 1] = reversed(path[i:k + 1])
                    improved = True
    return [p - 1 for p in path[1:]]


_METHOD_TEXT = {
    "city": "по статистике врачей города",
    "region": "по статистике врачей региона (доля города — по числу и типам его учреждений)",
    "estimate": "оценка по типам учреждений (статистики для этого города нет)",
    "prodoctorov": "по ПроДокторов, приведённым к масштабу официальной статистики",
    "density": "по плотности врачей на жителя в городах с данными ПроДокторов",
}


def _city_for(p: dict) -> dict:
    """База города для расчёта: наша или с ЛПУ из базы заказчика (p["client_base"]).
    Для ЛПУ заказчика число врачей — из его таблицы или как у совпавшего нашего учреждения;
    правки пользователя главнее."""
    ds = load_city(p["city"])
    bid = p.get("client_base")
    if not bid:
        return ds
    from .client_bases import doctor_counts, with_client
    ds = with_client(ds, bid)
    if p.get("project_mix"):
        return ds
    counts = doctor_counts(ds["facilities"], p["city"], p["specialty"])
    p["doctor_overrides"] = {**counts, **(p.get("doctor_overrides") or {})}
    return ds


def select_targets(p: dict, ds: dict, calib: dict | None = None) -> tuple[list[dict], list[dict]]:
    """ЛПУ с врачами нужной специальности и аптеки — с учётом фильтров и правок базы."""
    spec = p["specialty"]
    mixed = p.get("project_mix", False)
    care_filter = p.get("lpu_care") or "all"
    excluded = set(p.get("excluded_ids") or [])
    overrides = p.get("doctor_overrides") or {}
    allowed_cats = set(p.get("categories") or ["polyclinic_adult", "ambulatory", "polyclinic_child", "hospital",
                                                 "private_center", "cardio_center", "womens", "dental"])
    if "polyclinic_adult" in allowed_cats:
        allowed_cats.add("ambulatory")  # врачебные амбулатории фильтруются вместе с поликлиниками
    # фильтры: собственность и сети (None — без ограничения)
    lpu_own = set(p.get("lpu_ownership") or ["state", "private"])
    ph_own = set(p.get("pharmacy_ownership") or ["state", "private"])
    lpu_net = p.get("lpu_networks")
    ph_net = p.get("pharmacy_networks")
    lpu_net = set(lpu_net) if lpu_net is not None else None
    ph_net = set(ph_net) if ph_net is not None else None

    fac = annotate([f for f in ds["facilities"] if not f.get("needs_coords") and f["id"] not in excluded])
    extra_rows = specialties(p) if mixed else (p.get("extra_specialties") or [])
    extras = [x["specialty"] for x in extra_rows if x.get("specialty") and x["specialty"] != spec]
    slug = ds.get("slug")
    xcal = {xs: (calibration(slug, xs) if slug else None) for xs in extras}
    if mixed and not slug:
        xcal = {xs: {"counts": {}, "scale": calibration(p["city"], xs)["scale"]} for xs in extras}
    by_specialty = {}
    if mixed and p.get("client_base") and slug:
        from .client_bases import doctor_counts
        by_specialty = {xs: doctor_counts(ds["facilities"], slug, xs) for xs in [spec, *extras]}
    override_spec = p.get("doctor_override_specialty") or spec
    if mixed:
        by_specialty[override_spec] = {**by_specialty.get(override_spec, {}), **overrides}
    lpu = []
    for f in fac:
        if f["kind"] != "lpu":
            continue
        care_level = care.level(f)
        if not care.matches(care_level, care_filter):
            continue
        forced = (any(f["id"] in by_specialty.get(xs, {}) for xs in [spec, *extras]) if mixed else f["id"] in overrides)
        if not forced:
            if f["category"] not in allowed_cats or f["ownership"] not in lpu_own:
                continue
            if f["ownership"] == "private" and lpu_net is not None and f["network"] not in lpu_net:
                continue
        docs = N.doctors_in(f, spec, by_specialty.get(spec) if mixed else overrides, calib)
        if mixed:
            docs = list(dict.fromkeys(docs)) if int(p.get("doctor_visits_per_day") or 0) > 0 else []
        docs = care.doctors(docs, care_level, care_filter)
        # врачи дополнительных специальностей (терапевт + гинеколог в один день и т. п.)
        docs_x = {}
        for xs in extras:
            nx = N.doctors_in(f, xs, by_specialty.get(xs), xcal.get(xs))
            if mixed:
                nx = list(dict.fromkeys(nx))
            nx = care.doctors(nx, care_level, care_filter)
            if nx:
                docs_x[xs] = nx
        if docs or docs_x:
            lpu.append({**f, "docs": docs, "docs_x": docs_x, "care_level": care_level})
    # аптеки карты, которых нет в реестре аптек, вероятно закрылись или задублированы — в план не берём
    min_turn = p.get("pharmacy_min_turnover") or 0
    pharm = [f for f in fac if f["kind"] == "pharmacy" and f.get("in_registry") is not False and f["ownership"] in ph_own
             and (not min_turn or (f.get("turnover") or 0) >= min_turn)
             and (ph_net is None or f["network"] in ph_net)]
    return lpu, pharm


def suburb_targets(p: dict) -> tuple[list[dict], list[dict]]:
    """Учреждения за городом: пригороды — если зона расширена за город; а для нарисованных областей —
    ещё и учреждения соседних городов (Электросталь, Ногинск…), попавшие внутрь области."""
    terr = p.get("territories") or {}
    ext = terr.get("extensions") or []
    drawn = [d for d in terr.get("drawn") or [] if len(d.get("polygon") or []) >= 3]
    if not any(float(x or 0) > 0 for x in ext) and not drawn:
        return [], []
    from .cities import entry
    from .facilities import fetch_suburbs, load_suburbs
    sub = load_suburbs(p["city"])
    if sub is None:
        e = entry(p["city"])
        sub = fetch_suburbs(e) if e else []
    # аптеки пригорода — из реестра (как и в городе), ЛПУ — из OpenStreetMap
    from .territory import near
    reg_ph = [dict(f) for f in near(p["city"]) if f["kind"] == "pharmacy"]
    if reg_ph:
        sub = [f for f in sub if f["kind"] != "pharmacy"] + reg_ph
    cal = calibration(p["city"], p["specialty"])
    # у пригородов своей статистики нет — берём коэффициент города
    lpu, ph = select_targets(p, {"facilities": sub}, {"counts": {} if cal["counts"] is not None else None,
                                                      "scale": cal["scale"]})
    if drawn:
        n_lpu, n_ph = _neighbour_targets(p, drawn, {f["id"] for f in lpu + ph})
        lpu += n_lpu
        ph += n_ph
    return lpu, ph


def _doctor_names(facility, primary, specialty):
    return facility["docs"] if specialty == primary else facility.get("docs_x", {}).get(specialty, [])


def _mix_weights(lpu, p):
    rows = specialties(p)
    pools = {x["specialty"]: sum(len(_doctor_names(f, p["specialty"], x["specialty"])) for f in lpu) for x in rows}
    return [
        {**f, "balance_weight": sum(
            len(_doctor_names(f, p["specialty"], x["specialty"])) * x["doctor_visits_per_day"]
            / max(1, pools[x["specialty"]]) for x in rows
        )}
        for f in lpu
    ]


def _neighbour_targets(p: dict, drawn: list[dict], seen: set) -> tuple[list[dict], list[dict]]:
    """Учреждения других загруженных городов внутри нарисованных областей (со своей статистикой врачей)."""
    from .facilities import CITIES_DIR, list_cities
    from .territories import Plane, in_polygon
    lat = [pt[0] for d in drawn for pt in d["polygon"]]
    lon = [pt[1] for d in drawn for pt in d["polygon"]]
    box = (min(lat), max(lat), min(lon), max(lon))
    plane = Plane(sum(lat) / len(lat))
    shapes = [plane.to_xy(d["polygon"]) for d in drawn]
    out_l, out_p = [], []
    for c in list_cities():
        if c["slug"] == p["city"]:
            continue
        try:
            ds = load_city(c["slug"])
        except KeyError:
            continue
        cy, cx = ds["center"]
        if not (box[0] - 0.5 <= cy <= box[1] + 0.5 and box[2] - 0.8 <= cx <= box[3] + 0.8):
            continue  # город далеко от нарисованных областей
        lpu, ph = select_targets({**p, "city": c["slug"]}, ds, calibration(c["slug"], p["specialty"]))
        for items, out in ((lpu, out_l), (ph, out_p)):
            items = [f for f in items if f["id"] not in seen]
            if not items:
                continue
            xy = plane.xy(items)
            mask = np.zeros(len(items), dtype=bool)
            for sh in shapes:
                mask |= in_polygon(sh, xy)
            out += [f for f, m in zip(items, mask) if m]
    return out_l, out_p


def preview_territories(p: dict) -> dict:
    """Зоны без построения плана — для перетаскивания границ на карте."""
    ds = _city_for(p)
    lpu, pharm = select_targets(p, ds, calibration(p["city"], p["specialty"]))
    mixed = p.get("project_mix", False)
    if mixed:
        lpu = _mix_weights(lpu, p)
    reps = max(1, int(p.get("reps", 1)))
    s_lpu, s_ph = suburb_targets(p)
    part = partition(lpu, pharm, reps, tuple(ds["center"]), p.get("territories"), s_lpu, s_ph)
    return {
        "zones": [{
            "id": r + 1, "color": REP_COLORS[r % len(REP_COLORS)], "polygon": part["polygons"][r],
            "center": part["centers"][r], "weight": part["weights"][r],
            "extension": part["extensions"][r], "suburb": part["suburb_counts"][r],
            "doctors": sum(len(f["docs"]) + (sum(map(len, f["docs_x"].values())) if mixed else 0)
                           for f in part["lpu"][r]), "lpu": len(part["lpu"][r]),
            "pharmacies": len(part["pharm"][r]),
        } for r in range(reps)],
        "weight_step": part["weight_step"], "manual": part["manual"],
        # к какой зоне сейчас относится каждое учреждение — чтобы перекрасить точки до пересчёта плана
        "assignment": {f["id"]: r for r in range(reps) for f in part["lpu"][r] + part["pharm"][r]},
        # все учреждения зон: [id, широта, долгота, 1 — ЛПУ / 0 — аптека]
        "points": [[f["id"], round(f["lat"], 5), round(f["lon"], 5), 1 if f["kind"] == "lpu" else 0]
                   for r in range(reps) for f in part["lpu"][r] + part["pharm"][r]],
    }


# ─── Основной расчёт ─────────────────────────────────────────────────────────

def plan_project(p: dict) -> dict:
    unit = frequency_unit(p.get("frequency_unit", "project"))
    strict_month = unit == "project_month"
    if strict_month and not p.get("project_mix"):
        p = {**p, "project_mix": True}
    ds = _city_for(p)
    spec = p["specialty"]
    dpd = int(p["doctor_visits_per_day"])
    ppd = int(p["pharmacy_visits_per_day"])
    mixed = p.get("project_mix", False)
    targets = p.get("specialty_targets") if mixed else None
    f_d = max(1, int(p["doctor_frequency"]))
    f_p = max(1, int(p["pharmacy_frequency"]))
    reps = max(1, int(p.get("reps", 1)))
    tr = dict(N.TRANSPORT[p.get("transport", "car")])
    tr["speed_kmh"] /= float(p.get("travel_time_factor") or 1)
    for request_key, transport_key in (("travel_detour", "detour"), ("travel_overhead_min", "overhead_min")):
        if p.get(request_key) is not None:
            tr[transport_key] = float(p[request_key])
    limit_min = float(p.get("workday_limit_h", N.WORKDAY_LIMIT_H)) * 60
    extras = [{"spec": x["specialty"], "d": int(x.get("doctor_visits_per_day") or 0),
               "f": max(1, int(x.get("doctor_frequency") or 1))}
              for x in (p.get("extra_specialties") or []) if x.get("specialty") and x["specialty"] != spec]
    extras = [x for x in extras if x["d"] > 0]
    doctor_load = ([(x["specialty"], x["doctor_visits_per_day"], max(1, int(x["doctor_frequency"]))) for x in specialties(p)]
                   if mixed else [(spec, dpd, f_d)] + [(x["spec"], x["d"], x["f"]) for x in extras])
    dpd_all = sum(d for _, d, _ in doctor_load)
    nm = {
        "wait_by": {s_: float(N.WAIT_MEDIAN.get(s_, 20)) for s_ in [spec] + [x["spec"] for x in extras]},
        "wait": float(p["wait_min"] if p.get("wait_min") is not None else N.WAIT_MEDIAN.get(spec, 20)),
        "visit": float(p.get("doctor_visit_min") or N.DOCTOR_VISIT_MIN),
        "ph_visit": float(p.get("pharmacy_visit_min") or N.PHARMACY_VISIT_MIN),
        "ph_wait": float(p.get("pharmacy_wait_min") if p.get("pharmacy_wait_min") is not None else N.PHARMACY_WAIT_MIN),
        "entry": float(p.get("lpu_entry_min") if p.get("lpu_entry_min") is not None else N.LPU_ENTRY_MIN),
        "lunch": float(p.get("lunch_min") if p.get("lunch_min") is not None else N.LUNCH_MIN),
        "crm": float(p.get("crm_min") if p.get("crm_min") is not None else N.CRM_END_OF_DAY_MIN),
        "start": _parse_hm(p.get("day_start", N.DAY_START)),
    }

    start = date.fromisoformat(p["start_date"])
    end = date.fromisoformat(p["end_date"]) if p.get("end_date") else add_months(start, int(p.get("months", 3))) - timedelta(days=1)
    days = working_days(start, end)
    n = len(days)
    warnings: list[dict] = []
    if n == 0:
        raise ValueError("В выбранном периоде нет рабочих дней.")
    periods = project_months(start, end) if strict_month else []
    bounds = [0, *(bisect_right(days, last) for _, last in periods)]
    month_sizes = [right - left for left, right in zip(bounds, bounds[1:])] if strict_month else [n]
    cycles = {sp: _cycle_ranges(month_sizes, frequency) for sp, _, frequency in doctor_load}
    capacities = {sp: min(end - start for start, end in cycles[sp]) * d for sp, d, _ in doctor_load}
    pharmacy_cycles = _cycle_ranges(month_sizes, f_p)
    pharmacy_capacity = min(end - start for start, end in pharmacy_cycles) * ppd
    daily_periods = list(zip(bounds, bounds[1:])) if strict_month else [(0, n)]
    pharmacy_target = p.get("pharmacy_target")
    daily_pharmacies = targets is None and pharmacy_target is None
    pharmacy_pool_capacity = (max(_daily_capacity(last - first, ppd, f_p) for first, last in daily_periods)
                              if daily_pharmacies else pharmacy_capacity)
    phase = p.get("schedule_phase", 0)

    calib = calibration(p["city"], p["specialty"])
    lpu, pharm = select_targets(p, ds, calib)
    if p.get("wait_min") is not None:
        for sp, _, _ in doctor_load if mixed else [(spec, dpd, f_d)]:
            nm["wait_by"][sp] = float(p["wait_min"])
    if not lpu and dpd > 0 and not mixed:
        raise ValueError("Под выбранные фильтры не подошло ни одного ЛПУ с врачами этой специальности. "
                         "Ослабьте фильтры по типу, собственности или сетям.")
    if not pharm and ppd > 0 and not mixed:
        raise ValueError("Под выбранные фильтры не подошло ни одной аптеки. Добавьте сети или уберите фильтр.")
    center = tuple(ds["center"])

    # ── Территории ───────────────────────────────────────────────────────────
    starts = p.get("start_points") or []
    s_lpu, s_ph = suburb_targets(p)
    if mixed:
        lpu = _mix_weights(lpu, p)
    part = partition(lpu, pharm, reps, center, p.get("territories"), s_lpu, s_ph)
    territories = list(zip(part["lpu"], part["pharm"]))
    must_ids = set(part.get("must") or [])
    coverage = p.get("coverage") or "spread"

    result_days, rep_info, fac_use = [], [], defaultdict(list)
    totals = defaultdict(float)
    need_doctors_total = 0
    avail_doctors_total = 0
    need_pharm_total = 0
    avail_pharm_total = 0

    if mixed:
        mix_available = {sp: [sum(len(_doctor_names(f, spec, sp)) for f in t_lpu)
                              for t_lpu, _ in territories] for sp, _, _ in doctor_load}
        daily_doctor_capacity = {
            sp: max((_daily_capacity(last - first, d, f) for first, last in daily_periods), default=0)
            for sp, d, f in doctor_load
        }
        mix_quotas = {}
        for sp, _, _ in doctor_load:
            if targets is None:
                mix_quotas[sp] = {r: min(available, daily_doctor_capacity[sp])
                                  for r, available in enumerate(mix_available[sp])}
                continue
            limits = {r: min(mix_available[sp][r], capacities[sp]) for r in range(reps)}
            mix_quotas[sp] = distribute(targets.get(sp, 0), dict.fromkeys(limits, 1), limits)
        pharmacy_limits = {r: min(len(t_ph), pharmacy_pool_capacity) for r, (_, t_ph) in enumerate(territories)}
        pharmacy_quotas = distribute(pharmacy_target, dict.fromkeys(pharmacy_limits, 1), pharmacy_limits) if pharmacy_target is not None else pharmacy_limits

    for r, (t_lpu, t_ph) in enumerate(territories):
        home = tuple(starts[r]) if r < len(starts) and starts[r] else _centroid(t_lpu or t_ph) or center
        if mixed:
            visits = _mixed_days(t_lpu, home, spec, doctor_load, {sp: q[r] for sp, q in mix_quotas.items()},
                                 cycles, n, coverage, must_ids, phase, daily_periods if targets is None else None)
            ph_counts = defaultdict(int)
            pharmacy_schedule = None
            if ppd and (daily_pharmacies or strict_month or pharmacy_target is not None):
                pharmacy_schedule = _scheduled_pharmacies(
                    t_ph, home, pharmacy_quotas[r], ppd, pharmacy_cycles, n,
                    p.get("pharmacy_priority") == "turnover", phase, daily_periods if daily_pharmacies else None, f_p,
                )
            for i, day_date in enumerate(days):
                docs_today = visits[i]
                center_today = _centroid([v[0] for v in docs_today]) or home
                if pharmacy_schedule is not None:
                    ph_today = pharmacy_schedule[i]
                    for pharmacy in ph_today:
                        ph_counts[pharmacy["id"]] += 1
                else:
                    ph_today = _pharmacy_day(center_today if phase != 2 else home, t_ph, set(), ph_counts, ppd, f_p,
                                         p.get("pharmacy_priority") == "turnover") if ppd else []
                day = _build_day(day_date, r, home, docs_today, ph_today, nm, tr, spec, limit_min)
                if docs_today:
                    day["kind"] = "work"
                elif targets is None and dpd_all:
                    day["kind"] = "no_doctors"
                else:
                    day["kind"] = "reserve" if any(q[r] for q in mix_quotas.values()) else "pharmacy_only"
                result_days.append(day)
            assigned = {sp: mix_available[sp][r] for sp, _, _ in doctor_load}
            avail = sum(assigned.values())
            avail_doctors_total += avail
            avail_pharm_total += len(t_ph)
            need_pharm_total += (pharmacy_pool_capacity if daily_pharmacies or strict_month or pharmacy_target is not None
                                 else math.ceil(n * ppd / f_p))
            picked = {(f["id"], name, sp) for day in visits for f, name, _, sp in day}
            rep_info.append({
                "id": r + 1, "color": REP_COLORS[r % len(REP_COLORS)], "home": list(home),
                "doctors": len(picked), "lpu": len({f_id for f_id, _, _ in picked}),
                "pharmacies": sum(value > 0 for value in ph_counts.values()),
                "territory_doctors": avail, "territory_pharmacies": len(t_ph),
                "groups": sum(bool(day) for day in visits), "extra_available": assigned,
                "polygon": part["polygons"][r], "center": part["centers"][r], "weight": part["weights"][r],
                "extension": part["extensions"][r], "suburb": part["suburb_counts"][r],
            })
            continue
        units = [(f, i) for f in t_lpu for i in range(len(f["docs"]))]
        G_need = max(1, n // f_d)
        need_units = G_need * dpd if dpd > 0 else 0
        need_doctors_total += need_units
        avail_doctors_total += len(units)

        if dpd <= 0:
            groups = []
        elif coverage == "compact":
            # ближайшие к точке старта врачи: меньше дорог, но часть зоны не охвачена
            units.sort(key=lambda u: haversine(home[0], home[1], u[0]["lat"], u[0]["lon"]))
            groups = _make_groups(units[:need_units], dpd, home)
        else:
            # вся зона делится на компактные дневные группы, в план берутся равномерно разбросанные
            # группы с врачами из нарисованных областей — обязательно, остальные равномерно по зоне
            all_groups = _make_groups(units, dpd, home)
            must = [g for g in all_groups if any(u[0]["id"] in must_ids for u in g)]
            rest = [g for g in all_groups if not any(u[0]["id"] in must_ids for u in g)]
            groups = must[:G_need] + _spread_pick(rest, max(0, G_need - len(must)), home)
            groups.sort(key=lambda g: _angle(home, _centroid([u[0] for u in g])))
        chosen = [u for g in groups for u in g]
        G = len(groups)

        # аптеки, привязанные к группам врачей. Сначала каждая группа берёт аптеки «по пути»
        # (до MAX_PH_KM от своих врачей), затем недостающие добираются по удалённости — всегда
        # из своей зоны и начиная с ближайших, поэтому за аптеками не приходится ехать через город.
        ph_pool = list(t_ph)
        used_ph: set[str] = set()
        m_per_group = math.ceil(f_d * ppd / f_p) if ppd > 0 else 0
        g_centers = [_centroid([u[0] for u in g]) for g in groups]
        group_ph: list[list[dict]] = [[] for _ in groups]
        by_turnover = p.get("pharmacy_priority") == "turnover"
        for limit_km in (MAX_PH_KM, None):
            for gi, c in enumerate(g_centers):
                want = m_per_group - len(group_ph[gi])
                if want <= 0:
                    continue
                near = sorted(((haversine(c[0], c[1], x["lat"], x["lon"]), x) for x in ph_pool
                               if x["id"] not in used_ph), key=lambda t: t[0])
                cand = [x for d_, x in near if limit_km is None or d_ <= limit_km]
                if by_turnover and limit_km is not None:
                    # среди аптек «по пути» — сначала с наибольшим оборотом
                    cand.sort(key=lambda x: -(x.get("turnover") or 0))
                cand = cand[:want]
                used_ph.update(x["id"] for x in cand)
                group_ph[gi] += cand
        ph_only_visits: dict[str, int] = defaultdict(int)

        # врачи дополнительных специальностей: к каждой дневной группе — сначала из тех же ЛПУ,
        # затем из ближайших (до MAX_PH_KM), затем дальше по зоне; каждого посещают не чаще кратности
        # Группа основной специальности посещается f_d раз за проект. Если кратность доп. специальности
        # выше (урологи 2 раза в месяц при терапевтах 1 раз), один и тот же врач ходит в дни нескольких
        # соседних групп (группы упорядочены по кругу вокруг дома): r = ⌈f_x / f_d⌉ групп на список.
        # В списке не меньше врачей, чем визитов в день, — иначе день недобирает визиты.
        group_x: list[dict[str, list]] = [{} for _ in groups]
        x_cluster: dict[str, list[int]] = {}
        x_avail: dict[str, int] = {}
        for x in extras:
            pool = [(f, nm_) for f in t_lpu for nm_ in f.get("docs_x", {}).get(x["spec"], [])]
            x_avail[x["spec"]] = len(pool)
            r_x = max(1, math.ceil(x["f"] / max(1, f_d)))
            clusters = [list(range(c, min(len(groups), c + r_x))) for c in range(0, len(groups), r_x)]
            x_cluster[x["spec"]] = [0] * len(groups)
            used_x: set[tuple] = set()
            lists: list[list] = []
            for ci, cl in enumerate(clusters):
                lists.append([])
                for gi in cl:
                    x_cluster[x["spec"]][gi] = ci
                    group_x[gi][x["spec"]] = lists[ci]       # один список на все группы кластера
            for limit_km in (MAX_PH_KM, None):
                for ci, cl in enumerate(clusters):
                    m_x = max(x["d"], math.ceil(len(cl) * f_d * x["d"] / x["f"]))
                    want = m_x - len(lists[ci])
                    if want <= 0:
                        continue
                    ids = {u[0]["id"] for gi in cl for u in groups[gi]}
                    c = (sum(g_centers[gi][0] for gi in cl) / len(cl), sum(g_centers[gi][1] for gi in cl) / len(cl))
                    cand = sorted(((0.0 if f["id"] in ids else haversine(c[0], c[1], f["lat"], f["lon"]), f, nm_)
                                   for f, nm_ in pool if (f["id"], nm_) not in used_x), key=lambda t: t[0])
                    cand = [(f, nm_) for d_, f, nm_ in cand if limit_km is None or d_ <= limit_km][:want]
                    used_x.update((f["id"], nm_) for f, nm_ in cand)
                    lists[ci] += cand
        x_visits: dict[tuple, int] = defaultdict(int)
        x_day_count: dict[tuple, int] = defaultdict(int)   # сколько дней кластер уже брал врачей

        # распределение по дням
        group_visit_count = defaultdict(int)
        rep_days = []
        reserve_ph_slots = 0
        for i, d in enumerate(days):
            wave = i // G if G else f_d
            doc_units, ph_today, kind = [], [], "work"
            if G and wave < f_d:
                gi = i % G
                k = group_visit_count[gi]
                group_visit_count[gi] += 1
                doc_units = [(u[0], u[0]["docs"][u[1]], wave + 1, spec) for u in groups[gi]]
                for x in extras:
                    lst_x = group_x[gi].get(x["spec"]) or []
                    ck = (x["spec"], x_cluster[x["spec"]][gi])
                    kx = x_day_count[ck]
                    x_day_count[ck] += 1
                    picked = []
                    for t_ in range(min(x["d"], len(lst_x))):
                        f_, nm_ = lst_x[(kx * x["d"] + t_) % len(lst_x)]
                        if (f_["id"], nm_) in picked:
                            continue
                        picked.append((f_["id"], nm_))
                        x_visits[(f_["id"], nm_, x["spec"])] += 1
                        doc_units.append((f_, nm_, x_visits[(f_["id"], nm_, x["spec"])], x["spec"]))
                lst = group_ph[gi]
                if lst:
                    ph_today = [lst[(k * ppd + t) % len(lst)] for t in range(min(ppd, len(lst)))]
                    ph_today = list({x["id"]: x for x in ph_today}.values())
            else:
                kind = ("reserve" if G >= G_need else "no_doctors") if G else "pharmacy_only"
                if ppd:
                    ph_today = _pharmacy_day(home, ph_pool, used_ph, ph_only_visits, ppd, f_p)
                    reserve_ph_slots += len(ph_today)
            day = _build_day(d, r, home, doc_units, ph_today, nm, tr, spec, limit_min)
            day["kind"] = kind
            rep_days.append(day)

        need_ph = math.ceil(n * ppd / f_p) if ppd else 0
        need_pharm_total += need_ph
        avail_pharm_total += len(t_ph)
        rep_info.append({
            "id": r + 1, "color": REP_COLORS[r % len(REP_COLORS)], "home": list(home),
            "doctors": len(chosen), "lpu": len({u[0]["id"] for u in chosen}),
            "pharmacies": len(used_ph | set(ph_only_visits)), "territory_doctors": len(units), "territory_pharmacies": len(t_ph),
            "groups": G, "polygon": part["polygons"][r], "center": part["centers"][r],
            "extra_available": x_avail,
            "weight": part["weights"][r], "extension": part["extensions"][r],
            "suburb": part["suburb_counts"][r],
        })
        result_days += rep_days

    # нумерация визитов в аптеки и сводка по учреждениям
    ph_counter = defaultdict(int)
    for day in sorted(result_days, key=lambda x: (x["date"], x["rep"])):
        for s in day["stops"]:
            if s["type"] == "pharmacy":
                ph_counter[s["facility_id"]] += 1
                s["visit_no"] = ph_counter[s["facility_id"]]
            fac_use[s["facility_id"]].append({"date": day["date"], "rep": day["rep"],
                                              "doctors": [x["name"] for x in s.get("doctors", [])]})

    # ── Сводка ───────────────────────────────────────────────────────────────
    work = [d for d in result_days if d["stops"]]
    doc_visits = sum(d["totals"]["doctor_visits"] for d in result_days)
    ph_visits = sum(d["totals"]["pharmacy_visits"] for d in result_days)
    hours = [d["totals"]["hours"] for d in work]
    p80 = [d["totals"]["p80_hours"] for d in work]
    risky = [d for d in work if d["totals"]["overtime_risk"] >= 0.2]
    km = [d["totals"]["km"] for d in work]
    face = sum(d["totals"]["contact_min"] for d in work)
    total_min = sum(d["totals"]["hours"] * 60 for d in work)
    planned_doc = n * dpd_all * reps
    by_spec = []
    for sp, d_, f_ in doctor_load:
        vis = [(s_["facility_id"], doc["name"]) for d0 in result_days for s_ in d0["stops"]
               for doc in s_.get("doctors", []) if doc.get("spec", spec) == sp]
        if sp == spec and not mixed:
            avail = sum(r_["territory_doctors"] for r_ in rep_info)
        else:
            avail = sum(r_["extra_available"].get(sp, 0) for r_ in rep_info)
        frequency = len(cycles[sp]) if mixed else f_
        row = {"specialty": sp, "per_day": d_, "frequency": frequency, "planned": n * d_ * reps,
               "visits": len(vis), "unique": len(set(vis)), "available": avail}
        if mixed:
            row["capacity_per_rep"] = capacities[sp]
            if targets is not None:
                row["target"] = targets.get(sp, 0)
                row["planned"] = row["target"] * frequency
        by_spec.append(row)
    if mixed:
        planned_doc = sum(row["planned"] for row in by_spec)
        need_doctors_total = (sum(targets.values()) if targets is not None
                              else sum(daily_doctor_capacity.values()) * reps)
    planned_ph = n * ppd * reps
    if mixed and p.get("pharmacy_target") is not None:
        planned_ph = p["pharmacy_target"] * len(pharmacy_cycles)
    elif strict_month and targets is not None:
        planned_ph = pharmacy_capacity * len(pharmacy_cycles) * reps

    used_lpu = {s["facility_id"] for d in result_days for s in d["stops"] if s["type"] == "lpu"}
    used_ph_all = {s["facility_id"] for d in result_days for s in d["stops"] if s["type"] == "pharmacy"}
    uniq_doctors = sum(r_["doctors"] for r_ in rep_info)

    # интервал между визитами к одному врачу (календарные дни)
    gaps = []
    per_doc = defaultdict(list)
    for d in result_days:
        for s in d["stops"]:
            for doc in s.get("doctors", []):
                key = (s["facility_id"], doc["name"], doc.get("spec")) if mixed else (s["facility_id"], doc["name"])
                per_doc[key].append(date.fromisoformat(d["date"]))
    for v in per_doc.values():
        v.sort()
        gaps += [(b - a).days for a, b in zip(v, v[1:])]

    rec = None if mixed else _recommend_dpd(work, nm, limit_min)

    summary = {
        "city": ds["name"], "specialty": spec, "reps": reps,
        "period": [days[0].isoformat(), days[-1].isoformat()], "start": start.isoformat(), "end": end.isoformat(),
        "working_days": n, "reserve_days": sum(1 for d in result_days if d["kind"] == "reserve") // max(1, reps),
        "no_doctor_days": sum(1 for d in result_days if d["kind"] in ("no_doctors", "pharmacy_only")),
        "planned_doctor_visits": planned_doc, "planned_pharmacy_visits": planned_ph,
        "doctor_visits": doc_visits, "pharmacy_visits": ph_visits,
        "unique_doctors": uniq_doctors, "unique_lpu": len(used_lpu), "unique_pharmacies": len(used_ph_all),
        "available_doctors": avail_doctors_total, "available_pharmacies": avail_pharm_total,
        "need_doctors": need_doctors_total, "need_pharmacies": need_pharm_total,
        "avg_hours": round(float(np.mean(hours)), 2) if hours else 0,
        "avg_p80_hours": round(float(np.mean(p80)), 2) if p80 else 0,
        "max_hours": round(max(hours), 2) if hours else 0,
        "risky_days": len(risky), "workday_limit_h": limit_min / 60,
        # ожидаемое число дней с переработкой: сумма вероятностей по всем рабочим дням
        "expected_overtime_days": round(sum(d["totals"]["overtime_risk"] for d in work)),
        "avg_km": round(float(np.mean(km)), 1) if km else 0, "total_km": round(sum(km), 0),
        "contact_share": round(face / total_min * 100, 1) if total_min else 0,
        "repeat_gap_days": round(float(np.mean(gaps)), 1) if gaps else None,
        "recommended_doctor_visits": rec,
        "doctors_source": calib["method"], "doctors_in_city": calib["target"],
        "transport": tr["label"],
        "doctor_frequency": f_d, "pharmacy_frequency": f_p,
        "frequency_unit": unit,
        "doctor_visits_per_day": dpd, "pharmacy_visits_per_day": ppd,
        "specialties": by_spec, "doctor_visits_per_day_total": dpd_all,
    }
    if mixed:
        summary["project_mix"] = True
        summary["pharmacy_capacity_per_rep"] = pharmacy_capacity
        summary["doctor_target"] = sum(targets.values()) if targets is not None else None
        summary["pharmacy_target"] = p.get("pharmacy_target")
    if strict_month:
        summary.update(doctor_frequency=f_d * len(periods), pharmacy_frequency=f_p * len(periods),
                       month_windows=[{"start": first.isoformat(), "end": last.isoformat(), "working_days": size}
                                      for (first, last), size in zip(periods, month_sizes)])
    summary["over_limit_days"] = sum(max(d["totals"]["hours"], d["totals"]["p80_hours"]) > limit_min / 60 for d in work)
    summary["over_limit_hours"] = round(sum(max(0, max(d["totals"]["hours"], d["totals"]["p80_hours"]) - limit_min / 60) for d in work), 2)

    _add_warnings(warnings, summary, n, dpd, ppd, f_d, f_p, spec, reps, ds, days, mixed=mixed)

    facilities_out = []
    for f in lpu + pharm + s_lpu + s_ph:  # с учреждениями пригородов и соседних городов
        if f["id"] in fac_use:
            facilities_out.append({
                "id": f["id"], "name": f["name"], "address": f["address"], "kind": f["kind"],
                "category": f["category"], "care_level": f.get("care_level"), "lat": f["lat"], "lon": f["lon"],
                "doctors": len(f.get("docs", [])) + (sum(map(len, f.get("docs_x", {}).values())) if mixed else 0),
                "visits": fac_use[f["id"]],
                "ownership": f.get("ownership", ""), "network": f.get("network_name", ""),
            })

    advice = None if mixed else advise(result_days, nm, {
        "n": n, "reps": reps, "dpd": dpd, "ppd": ppd, "f_d": f_d, "f_p": f_p,
        "months": int(p.get("months", 3)), "limit_h": limit_min / 60,
        "extra_per_day": sum(x["d"] for x in extras),
        "reps_avail": [r_["territory_doctors"] for r_ in rep_info], "ph_avail": avail_pharm_total,
    })

    return {
        "summary": summary, "warnings": warnings, "reps": rep_info, "advice": advice,
        "territories": {"weight_step": part["weight_step"], "manual": part["manual"], "coverage": coverage},
        "days": sorted(result_days, key=lambda x: (x["date"], x["rep"])),
        "facilities": facilities_out,
    }


# ─── Территории и группы ─────────────────────────────────────────────────────

def optimize_plan(request, baseline=None, auto_team=False):
    """Сравнить построенные расписания при тех же целях и кратности."""
    best = baseline if baseline is not None else plan_project(request)
    checked = 1
    solution_request = request

    def score(plan):
        s = plan["summary"]
        return (sum(max(0, row.get("target", 0) - row["unique"]) for row in s["specialties"]
                    if row.get("target") is not None),
                s["planned_doctor_visits"] - s["doctor_visits"],
                s["planned_pharmacy_visits"] - s["pharmacy_visits"],
                s["over_limit_days"], s["over_limit_hours"], s["total_km"], s["reps"])

    def compare(candidate_request):
        nonlocal best, checked, solution_request
        candidate = plan_project(candidate_request)
        checked += 1
        if score(candidate) < score(best):
            best, solution_request = candidate, candidate_request

    if request.get("project_mix") and best["summary"]["over_limit_days"]:
        for offset in (1, 2):
            compare(request | {"schedule_phase": offset})
            if score(best)[:4] == (0, 0, 0, 0):
                break
        if auto_team and best["summary"]["over_limit_days"] and request["reps"] < 80:
            s = best["summary"]
            estimate = math.ceil(request["reps"] * s["avg_p80_hours"] / s["workday_limit_h"])
            size = min(80, request["reps"] + max(1, min(estimate - request["reps"], 4)))
            compare(request | {"reps": size, "territories": None})
    best["summary"]["checked_variants"] = checked
    best["request"] = {key: value for key, value in solution_request.items() if key != "schedule_phase"}
    best["request"]["frequency_unit"] = best["summary"]["frequency_unit"]
    return best


def _centroid(items):
    if not items:
        return None
    return (sum(x["lat"] for x in items) / len(items), sum(x["lon"] for x in items) / len(items))


def _make_groups(units, size, home):
    """Компактные дневные группы: начинаем с самого дальнего врача и добираем ближайших."""
    if not units:
        return []
    lat0 = home[0]
    k = math.cos(math.radians(lat0))
    xy = np.array([[u[0]["lon"] * k * 111.32, u[0]["lat"] * 110.57] for u in units])
    h = np.array([home[1] * k * 111.32, home[0] * 110.57])
    left = np.ones(len(units), dtype=bool)
    dist_home = ((xy - h) ** 2).sum(1)
    groups = []
    while left.any():
        idx = np.flatnonzero(left)
        seed = idx[dist_home[idx].argmax()]
        left[seed] = False
        g = [seed]
        c = xy[seed].copy()
        while len(g) < size and left.any():
            idx = np.flatnonzero(left)
            nxt = idx[((xy[idx] - c) ** 2).sum(1).argmin()]
            left[nxt] = False
            g.append(nxt)
            c = xy[g].mean(0)
        groups.append([units[i] for i in g])
    groups.sort(key=lambda g: _angle(home, _centroid([u[0] for u in g])))
    return groups


def _hilbert(x: int, y: int, n: int = 64) -> int:
    d, s = 0, n // 2
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        s //= 2
    return d


def _spread_pick(groups, need, home):
    """Равномерная выборка need групп по всей зоне: обход кривой Гильберта и шаг через равные интервалы."""
    if need <= 0:
        return []
    if len(groups) <= need:
        return groups
    cs = [_centroid([u[0] for u in g]) for g in groups]
    la = [c[0] for c in cs]
    lo = [c[1] for c in cs]
    def cell(v, a, b):
        return min(63, int((v - a) / ((b - a) or 1) * 64))
    order = sorted(range(len(groups)), key=lambda i: _hilbert(cell(lo[i], min(lo), max(lo)),
                                                              cell(la[i], min(la), max(la))))
    step = len(groups) / need
    picked = [groups[order[int(i * step + step / 2)]] for i in range(need)]
    picked.sort(key=lambda g: _angle(home, _centroid([u[0] for u in g])))
    return picked


def _cycle_ranges(periods, frequency):
    offset = 0
    ranges = []
    for size in periods:
        ranges.extend((offset + i * size // frequency, offset + (i + 1) * size // frequency)
                      for i in range(frequency))
        offset += size
    return ranges


def _group_days(groups, cycles, offset):
    for cycle, (start, end) in enumerate(cycles, 1):
        span = end - start
        for i, group in enumerate(groups):
            day = start + (i * span // len(groups) + offset) % span
            yield day, cycle, group


def _daily_capacity(days, per_day, frequency):
    return days * per_day // frequency if frequency <= days else 0


def _daily_batches(units, periods, per_day, frequency, offset):
    """Распределить полные серии визитов по рабочим дням каждого периода."""
    for first, last in periods:
        days = last - first
        size = min(len(units), _daily_capacity(days, per_day, frequency))
        if not size:
            continue
        total = size * frequency
        for i in range(days):
            left, right = i * total // days, (i + 1) * total // days
            yield first + (i + offset) % days, [units[index % size] for index in range(left, right)]


def _scheduled_pharmacies(pool, home, quota, per_day, cycles, n, by_turnover, phase,
                          daily_periods=None, frequency=1):
    if by_turnover:
        key = lambda f: (-(f.get("turnover") or 0), haversine(*home, f["lat"], f["lon"]))
    else:
        key = lambda f: haversine(*home, f["lat"], f["lon"])
    selected = sorted(pool, key=key)[:quota]
    groups = _make_groups([(f, None) for f in selected], per_day, home)
    schedule = [[] for _ in range(n)]
    if daily_periods is not None:
        units = [f for group in groups for f, _ in group]
        for day, batch in _daily_batches(units, daily_periods, per_day, frequency, phase):
            schedule[day] = batch
        return schedule
    for day, _, group in _group_days(groups, cycles, phase):
        schedule[day] = [f for f, _ in group]
    return schedule


def _mixed_days(lpu, home, primary, doctor_load, quotas, cycles, n, coverage, must_ids, phase,
                daily_periods=None):
    """У каждой специальности свои группы и повторные циклы в общих зонах."""
    schedule = [[] for _ in range(n)]
    for i, (sp, per_day, frequency) in enumerate(doctor_load):
        quota = quotas[sp]
        if not quota:
            continue
        doctors = {f["id"]: _doctor_names(f, primary, sp) for f in lpu}
        units = [(f, i) for f in lpu for i in range(len(doctors[f["id"]]))]
        if coverage == "compact":
            units.sort(key=lambda u: haversine(*home, u[0]["lat"], u[0]["lon"]))
            groups = _make_groups(units[:quota], per_day, home)
        else:
            groups = _make_groups(units, per_day, home)
            forced = [g for g in groups if any(u[0]["id"] in must_ids for u in g)]
            other = [g for g in groups if all(u[0]["id"] not in must_ids for u in g)]
            # Сначала полные группы: частичную можно выбрать только под остаток квоты.
            preferred = forced + _spread_pick([g for g in other if len(g) == per_day],
                                               max(0, math.ceil(quota / per_day) - len(forced)), home)
            selected = [u for g in preferred for u in g]
            if len(selected) < quota:
                chosen = {(f["id"], i) for f, i in selected}
                selected.extend((f, i) for f, i in units if (f["id"], i) not in chosen)
            groups = _make_groups(selected[:quota], per_day, home)
        if daily_periods is not None:
            units = [u for group in groups for u in group]
            visit_numbers = defaultdict(int)
            batches = _daily_batches(units, daily_periods, per_day, frequency, phase * i)
            for day, batch in sorted(batches):
                for f, j in batch:
                    key = (f["id"], j)
                    visit_numbers[key] += 1
                    schedule[day].append((f, doctors[f["id"]][j], visit_numbers[key], sp))
            continue
        for day, cycle, group in _group_days(groups, cycles[sp], phase * i):
            schedule[day].extend((f, doctors[f["id"]][j], cycle, sp) for f, j in group)
    return schedule


def _pharmacy_day(home, pool, group_used: set, visits: dict, ppd: int, f_p: int, by_turnover=False) -> list[dict]:
    """День только по аптекам: компактная группа из аптек зоны, ещё не привязанных к врачам.

    Первая аптека — ближайшая к точке старта из тех, у кого остались визиты (не больше кратности),
    следующие — ближайшие к уже выбранным. Так день остаётся в одной части зоны, а дальние аптеки
    тоже получают визиты, когда ближние закончатся.
    """
    free = [x for x in pool if x["id"] not in group_used and visits[x["id"]] < f_p]
    if not free:
        return []
    # меньше визитов — раньше: сначала каждая аптека по разу, потом повторные
    least = min(visits[x["id"]] for x in free)
    free = [x for x in free if visits[x["id"]] == least]
    def priority(x):
        km = haversine(*home, x["lat"], x["lon"])
        return (-(x.get("turnover") or 0) if by_turnover and km <= MAX_PH_KM else 0, km)

    seed = min(free, key=priority)
    day, c = [seed], (seed["lat"], seed["lon"])
    rest = [x for x in free if x is not seed]
    while len(day) < ppd and rest:
        nxt = min(rest, key=lambda x: haversine(c[0], c[1], x["lat"], x["lon"]))
        rest.remove(nxt)
        day.append(nxt)
        c = (sum(x["lat"] for x in day) / len(day), sum(x["lon"] for x in day) / len(day))
    for x in day:
        visits[x["id"]] += 1
    return day


# ─── День ────────────────────────────────────────────────────────────────────

def _travel(a, b, tr):
    km = haversine(a[0], a[1], b[0], b[1]) * tr["detour"]
    if km < 0.4:
        return km, max(2.0, km / 4.5 * 60)
    return km, km / tr["speed_kmh"] * 60 + tr["overhead_min"]


def _build_day(d: date, r: int, home, doc_units, pharmacies, nm, tr, spec, limit_min):
    # учреждения дня
    stops_raw: dict[str, dict] = {}
    for f, name, visit_no, sp in doc_units:
        s = stops_raw.setdefault(f["id"], {"f": f, "type": "lpu", "doctors": []})
        s["doctors"].append({"name": name, "visit_no": visit_no, "spec": sp})
    for ph in pharmacies:
        stops_raw.setdefault(ph["id"], {"f": ph, "type": "pharmacy", "doctors": []})
    raw = list(stops_raw.values())
    order = _route_order(home, [(s["f"]["lat"], s["f"]["lon"]) for s in raw])

    waits: list[float] = []
    t = nm["start"]
    pos = home
    lunch_done = False
    segs, stops = [], []
    travel_list, n_doc, n_ph, n_lpu = [], 0, 0, 0
    km_total, contact = 0.0, 0.0
    for seq, oi in enumerate(order, 1):
        s = raw[oi]
        f = s["f"]
        if not lunch_done and t >= 13 * 60 and seq > 1:
            segs.append(["lunch", t, nm["lunch"]])
            t += nm["lunch"]
            lunch_done = True
        km, tm = _travel(pos, (f["lat"], f["lon"]), tr)
        segs.append(["travel", t, tm])
        travel_list.append(tm)
        t += tm
        km_total += km
        arrive = t
        if s["type"] == "lpu":
            n_lpu += 1
            segs.append(["entry", t, nm["entry"]])
            t += nm["entry"]
            docs_out = []
            for doc in s["doctors"]:
                wait = nm.get("wait_by", {}).get(doc["spec"], nm["wait"])
                waits.append(wait)
                segs.append(["wait", t, wait])
                t += wait
                segs.append(["visit", t, nm["visit"]])
                docs_out.append({**doc, "start": _hm(t), "end": _hm(t + nm["visit"])})
                t += nm["visit"]
                contact += nm["visit"]
                n_doc += 1
        else:
            segs.append(["wait", t, nm["ph_wait"]])
            t += nm["ph_wait"]
            segs.append(["pharmacy", t, nm["ph_visit"]])
            t += nm["ph_visit"]
            contact += nm["ph_visit"]
            n_ph += 1
            docs_out = []
        stops.append({
            "seq": seq, "type": s["type"], "facility_id": f["id"], "name": f["name"],
            "address": f["address"], "lat": f["lat"], "lon": f["lon"], "category": f["category"],
            "arrive": _hm(arrive), "leave": _hm(t), "travel_min": round(tm), "km": round(km, 1),
            "doctors": docs_out,
        })
        pos = (f["lat"], f["lon"])
    if stops and not lunch_done and t > 13 * 60:
        segs.append(["lunch", t, nm["lunch"]])
        t += nm["lunch"]
        lunch_done = True
    if stops:
        segs.append(["crm", t, nm["crm"]])
        t += nm["crm"]
    hours = (t - nm["start"]) / 60 if stops else 0
    nm_day = {**nm, "wait": float(np.mean(waits))} if waits else nm  # среднее ожидание врачей этого дня
    p50, p80, risk = _simulate(travel_list, n_doc, n_lpu, n_ph, nm_day, lunch_done, limit_min) if stops else (0, 0, 0)
    return {
        "date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "rep": r + 1,
        "home": list(home), "stops": stops,
        "segments": [[a, round(b, 1), round(c, 1)] for a, b, c in segs],
        "totals": {
            "doctor_visits": n_doc, "pharmacy_visits": n_ph, "lpu": n_lpu,
            "start": _hm(nm["start"]), "end": _hm(t) if stops else "",
            "hours": round(hours, 2), "km": round(km_total, 1), "contact_min": round(contact),
            "travel_min": round(sum(travel_list)),
            "wait_min": round(sum(waits) + n_ph * nm["ph_wait"]),
            "p50_hours": round(p50, 2), "p80_hours": round(p80, 2), "overtime_risk": round(risk, 3),
        },
    }


def _simulate(travel, n_doc, n_lpu, n_ph, nm, lunch, limit_min, iters=400):
    """Монте-Карло дня: ожидание и дорога — логнормальные, визиты — нормальные."""
    seed_data = repr((travel, n_doc, n_lpu, n_ph, lunch,
                      tuple(nm[k] for k in ("wait", "visit", "entry", "ph_wait", "ph_visit", "lunch", "crm"))))
    seed = int.from_bytes(hashlib.blake2b(seed_data.encode(), digest_size=8).digest(), "little")
    rng = np.random.default_rng(seed)
    tot = np.zeros(iters)
    if travel:
        tot += (np.array(travel)[None, :] * rng.lognormal(0, 0.25, (iters, len(travel)))).sum(1)
    if n_doc:
        if nm["wait"]:
            tot += rng.lognormal(math.log(nm["wait"]), 0.55, (iters, n_doc)).sum(1)
        tot += np.clip(rng.normal(nm["visit"], N.DOCTOR_VISIT_SD, (iters, n_doc)), min(3, nm["visit"]), None).sum(1)
    if n_lpu and nm["entry"]:
        tot += np.clip(rng.normal(nm["entry"], 3, (iters, n_lpu)), 1, None).sum(1)
    if n_ph:
        if nm["ph_wait"]:
            tot += rng.lognormal(math.log(nm["ph_wait"]), 0.5, (iters, n_ph)).sum(1)
        tot += np.clip(rng.normal(nm["ph_visit"], N.PHARMACY_VISIT_SD, (iters, n_ph)), min(4, nm["ph_visit"]), None).sum(1)
    tot += nm["crm"] + (nm["lunch"] if lunch else 0)
    return (float(np.percentile(tot, 50)) / 60, float(np.percentile(tot, 80)) / 60,
            float((tot > limit_min).mean()))


def _recommend_dpd(work_days, nm, limit_min):
    """Оценка: сколько визитов к врачам укладывается в норму дня при том же числе аптек.

    День раскладывается на постоянную часть (обед, CRM), аптечную часть (визиты в аптеки
    и их доля дороги) и врачебную часть; врачебная часть делится на число визитов к врачам.
    """
    fixed = nm["lunch"] + nm["crm"]
    est = []
    for d in work_days:
        t = d["totals"]
        if not t["doctor_visits"]:
            continue
        stops = max(1, t["lpu"] + t["pharmacy_visits"])
        ph_part = t["pharmacy_visits"] * (nm["ph_wait"] + nm["ph_visit"] + t["travel_min"] / stops)
        doc_part = t["hours"] * 60 - fixed - ph_part
        per_doc = doc_part / t["doctor_visits"]
        if per_doc > 0:
            est.append((limit_min - fixed - ph_part) / per_doc)
    if not est:
        return None
    return max(1, int(np.median(est)))


# ─── Предупреждения ──────────────────────────────────────────────────────────

def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _add_warnings(w, s, n, dpd, ppd, f_d, f_p, spec, reps, ds, days, mixed=False):
    if mixed:
        for row in s["specialties"]:
            if row["visits"] < row["planned"]:
                w.append({"level": "warning", "text": f"{row['specialty']}: в маршрутах {row['visits']} из "
                          f"{row['planned']} визитов, охвачено {row['unique']} врачей из {row['available']} доступных."})
    elif s["need_doctors"] > s["available_doctors"]:
        short = s["need_doctors"] - s["available_doctors"]
        lost = s["planned_doctor_visits"] - s["doctor_visits"]
        alt_f = math.ceil(n * dpd * reps / max(1, s["available_doctors"]))
        alt_d = max(1, s["available_doctors"] * f_d // max(1, n * reps))
        w.append({"level": "critical", "text":
                  f"Не хватает врачей. Для {dpd} визитов в день с кратностью {f_d} нужно "
                  f"{s['need_doctors']} уникальных врачей ({spec.lower()}), в базе города около "
                  f"{s['available_doctors']}. Без визитов останется {lost} слотов. "
                  f"Варианты: кратность {alt_f}, или {alt_d} {_plural(alt_d, 'визит', 'визита', 'визитов')} "
                  f"к врачам в день, или загрузить свой список врачей."})
    if ppd and s["need_pharmacies"] > s["available_pharmacies"]:
        alt = math.ceil(n * ppd * reps / max(1, s["available_pharmacies"]))
        w.append({"level": "critical", "text":
                  f"Не хватает аптек. При кратности {f_p} нужно {s['need_pharmacies']} аптек, "
                  f"в базе {s['available_pharmacies']}. Поставьте кратность {alt} или дополните базу."})
    elif ppd and s["pharmacy_visits"] < s["planned_pharmacy_visits"]:
        lost = s["planned_pharmacy_visits"] - s["pharmacy_visits"]
        w.append({"level": "warning", "text":
                  f"{lost} визитов в аптеки не поставлены: в зонах медпредов закончились аптеки выбранных сетей "
                  f"(каждую можно посетить {f_p} {_plural(f_p, 'раз', 'раза', 'раз')}). Добавьте сети, поднимите "
                  f"кратность визитов в аптеки или снизьте число аптек в день."})
    elif ppd and s["need_pharmacies"] > 0.85 * s["available_pharmacies"]:
        w.append({"level": "warning", "text":
                  f"План задействует {s['need_pharmacies']} из {s['available_pharmacies']} аптек в базе — "
                  f"почти все, включая удалённые. Маршруты к концу проекта станут длиннее."})
    if s["risky_days"]:
        e = s["expected_overtime_days"]
        w.append({"level": "warning", "text":
                  f"В {s['risky_days']} {_plural(s['risky_days'], 'дне', 'днях', 'днях')} из {n * reps} вероятность, "
                  f"что день выйдет дольше {s['workday_limit_h']:g} ч, — 20% и выше. Всего за проект ожидается около "
                  f"{e} {_plural(e, 'дня', 'дней', 'дней')} с переработкой ({round(e / max(1, n * reps) * 100)}% рабочих дней)."})
    rec = s["recommended_doctor_visits"]
    if rec and rec < dpd:
        w.append({"level": "warning", "text":
                  f"По нормативам в {s['workday_limit_h']:g}-часовой день укладывается около {rec} "
                  f"{_plural(rec, 'визита', 'визитов', 'визитов')} к врачам при {ppd} аптеках."})
    if s["reserve_days"] and not mixed:
        w.append({"level": "info", "text":
                  f"{s['reserve_days']} {_plural(s['reserve_days'], 'день', 'дня', 'дней')} без визитов к врачам "
                  f"(остаток после деления на волны) — резерв на переносы и больничные, заняты аптеками."})
    years = {d.year for d in days}
    unofficial = [y for y in years if not calendar_is_official(y)]
    if unofficial:
        w.append({"level": "info", "text":
                  f"Производственный календарь на {', '.join(map(str, unofficial))} ещё не утверждён — "
                  f"праздники взяты по Трудовому кодексу."})
    no_addr = sum(1 for f in ds["facilities"] if not f["address"])
    if no_addr:
        w.append({"level": "info", "text": f"У {no_addr} учреждений в базе пока нет адреса — он дозаполняется."})
