"""Условия проектов и неизменяемые версии городских маршрутов и сводок."""
from __future__ import annotations

import gzip
import json
import math
import shutil
import threading
import time
import uuid
from datetime import date

from . import cities as C
from . import facilities as F
from .calendar_ru import frequency_unit
from .norms import CITY_NORM_DEFAULTS
from .coverage import distribute, required_team, specialties, specialty_targets
from .planner import optimize_plan, plan_project

DIR = F.DATA_DIR / "projects"
DAILY_PLAN_REVISION = 1
DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONDITIONS = {
    "specialty": "Кардиолог", "doctor_visits_per_day": 5, "pharmacy_visits_per_day": 3,
    "doctor_frequency": 1, "pharmacy_frequency": 1, "frequency_unit": "project_month", "start_date": "", "months": 12,
    "transport": "car", "workday_limit_h": 8.0, "coverage": "spread",
    "lpu_ownership": ["state", "private"], "pharmacy_ownership": ["state", "private"], "categories": None,
}

_lock = threading.RLock()
_jobs: dict[str, dict] = {}


# ─── Хранение ────────────────────────────────────────────────────────────────

def _path(pid: str):
    if not pid.replace("-", "").isalnum():
        raise KeyError(pid)
    return DIR / f"{pid}.json"


def list_projects() -> list[dict]:
    out = []
    for p in sorted(DIR.glob("*.json"), key=lambda x: -x.stat().st_mtime):
        try:
            pr = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        t = (pr.get("result") or {}).get("totals") or {}
        out.append({"id": pr["id"], "name": pr["name"], "client": pr.get("client", ""), "status": pr.get("status", "draft"),
                    "updated": pr.get("updated", ""), "cities": len(pr.get("cities", [])),
                    "specialty": pr["conditions"]["specialty"],
                    "specialties": [x["specialty"] for x in specialties(pr["conditions"])], "reps": t.get("reps"),
                    "computed": bool(pr.get("result"))})
    return out


def get(pid: str) -> dict:
    p = _path(pid)
    if not p.exists():
        raise KeyError(pid)
    pr = json.loads(p.read_text(encoding="utf-8"))
    pr.setdefault("goal", {"mode": "none", "total_doctors": None})
    pr["conditions"]["frequency_unit"] = frequency_unit(pr["conditions"].get("frequency_unit"))
    # вариант «суммарно пропорционально» убран: такие проекты считаются «наименьшей командой»
    if (pr.get("goal") or {}).get("mode") == "total":
        pr["goal"]["mode"] = "total_min"
    if result := pr.get("result"):
        _mark_stale(pr, result)
    return pr


def save(pr: dict) -> dict:
    pr["updated"] = time.strftime("%Y-%m-%d %H:%M")
    with _lock:
        path = _path(pr["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(pr, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    return pr


def create(data: dict) -> dict:
    pr = {"id": uuid.uuid4().hex[:12], "created": time.strftime("%Y-%m-%d %H:%M"), "result": None,
          "name": data.get("name") or "Новый проект", "client": data.get("client", ""),
          "conditions": {**DEFAULT_CONDITIONS, **(data.get("conditions") or {})},
          "goal": data.get("goal") or {"mode": "none", "total_doctors": None},
          "cities": data.get("cities") or [], "status": data.get("status") or "draft", "plans": {}}
    pr["conditions"]["frequency_unit"] = frequency_unit(pr["conditions"].get("frequency_unit"))
    if not pr["conditions"]["start_date"]:
        pr["conditions"]["start_date"] = date.today().isoformat()
    return save(pr)


def calculation_inputs(pr: dict) -> dict:
    return {
        "conditions": pr["conditions"],
        "goal": {"total_doctors": None, **pr["goal"]},
        "cities": [{"reps": None, "target": None, **city} for city in pr.get("cities", [])],
        "plans": pr.get("plans", {}),
    }


def _mark_stale(pr: dict, result: dict) -> None:
    needs_update = (pr["goal"]["mode"] == "none"
                    and result.get("daily_plan_revision") != DAILY_PLAN_REVISION)
    result["stale"] = needs_update or result.get("inputs") != calculation_inputs(pr)
    result["planner_updated"] = needs_update


def update(pid: str, data: dict) -> dict:
    with _lock:
        pr = get(pid)
        for k in ("name", "client", "conditions", "goal", "cities", "status"):
            if k in data:
                if k == "cities":
                    slugs = {city["slug"] for city in data[k]}
                    pr["plans"] = {slug: plan for slug, plan in pr.get("plans", {}).items() if slug in slugs}
                pr[k] = data[k]
        pr["conditions"]["frequency_unit"] = frequency_unit(pr["conditions"].get("frequency_unit"))
        if result := pr.get("result"):
            _mark_stale(pr, result)
        return save(pr)


def save_city_plan(pid: str, slug: str, req: dict, revision: str | None = None) -> dict:
    if req["city"] != slug or not C.entry(slug):
        raise ValueError("Город в плане должен совпадать с городом проекта.")
    with _lock:
        pr = get(pid)
        if revision and ((pr.get("result") or {}).get("id") != revision or pr["result"]["stale"] or job_status(pid)["running"]):
            raise ValueError("Версия проекта изменилась. Откройте план города заново.")
        plans = pr.setdefault("plans", {})
        c = next((x for x in pr["cities"] if x["slug"] == slug), None)
        if revision and c is None:
            raise ValueError("Город удалён из проекта. Обновите проект.")
        filter_keys = ("categories", "lpu_care", "lpu_ownership", "pharmacy_ownership", "pharmacy_min_turnover")
        filters = {}
        for key in filter_keys:
            actual, common = req.get(key), pr["conditions"].get(key)
            if key == "lpu_care":
                actual, common = actual or "all", common or "all"
            if key == "categories":
                default_categories = set(F.CATEGORY_LABELS) - {"ambulatory", "pharmacy", "excluded"}
                actual = None if not actual or default_categories <= set(actual) else actual
                common = None if not common or default_categories <= set(common) else common
            if key in ("lpu_ownership", "pharmacy_ownership"):
                actual = sorted(actual or ["state", "private"])
                common = sorted(common or ["state", "private"])
            if actual != common:
                filters[key] = actual
        city_norms = {key: req[key] for key, default in CITY_NORM_DEFAULTS.items()
                      if key in req and req[key] != pr["conditions"].get(key, default)}
        plans[slug] = {**req, "city_filters": filters, "city_norms": city_norms}
        if c is None:
            pr["cities"].append({"slug": slug, "reps": req["reps"]})
        else:
            c["reps"] = req["reps"]
        if result := pr.get("result"):
            _mark_stale(pr, result)
        return save(pr)


def delete(pid: str) -> None:
    with _lock:
        if job_status(pid)["running"]:
            raise ValueError("Дождитесь окончания расчёта, чтобы удалить проект.")
        _path(pid).unlink(missing_ok=True)
        folder = DIR / pid
        if folder.is_dir():
            shutil.rmtree(folder)


def _plan_file(pid: str, slug: str, revision: str | None = None):
    _path(pid)
    if not slug or not slug.replace("_", "").replace("-", "").isalnum():
        raise KeyError(slug)
    folder = DIR / pid
    if revision and revision.isalnum():
        folder /= revision
    return folder / f"{slug}.json.gz"


def load_plan(pid: str, slug: str, revision: str | None = None) -> dict | None:
    f = _plan_file(pid, slug, revision)
    if not f.exists():
        return None
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def load_result(pid: str, revision: str) -> dict | None:
    if len(revision) != 32 or not revision.isalnum():
        return None
    path = _path(pid).with_suffix("") / revision / "result.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


# ─── Расчёт ──────────────────────────────────────────────────────────────────

def job_status(pid: str) -> dict:
    return dict(_jobs.get(pid) or {"running": False, "done": 0, "total": 0, "current": "", "error": ""})


def start(pid: str) -> dict:
    pr = get(pid)
    if not pr["cities"]:
        raise ValueError("Добавьте в проект хотя бы один город.")
    with _lock:
        if (_jobs.get(pid) or {}).get("running"):
            return job_status(pid)
        _jobs[pid] = {"running": True, "done": 0, "total": len(pr["cities"]), "current": "", "error": ""}
    threading.Thread(target=_run, args=(pid,), daemon=True).start()
    return job_status(pid)


def _request(cond: dict, slug: str, reps: int, saved: dict | None = None) -> dict:
    p = {**cond, "city": slug, "reps": reps}
    for k in ("lpu_networks", "pharmacy_networks", "territories"):
        p.pop(k, None)
    if saved:
        city_keys = ("doctor_overrides", "excluded_ids", "lpu_networks", "pharmacy_networks", "start_points", "client_base")
        p.update({key: saved[key] for key in city_keys if key in saved})
        p.update(saved.get("city_filters") or {})
        p.update({key: value for key, value in (saved.get("city_norms") or {}).items() if key in CITY_NORM_DEFAULTS})
        p["doctor_override_specialty"] = saved.get("doctor_override_specialty") or saved.get("specialty")
        terr = saved.get("territories")
        if terr and len(terr.get("centers") or []) == reps:
            p["territories"] = terr
    p["start_date"] = cond["start_date"] or date.today().isoformat()
    return p


def _targets_min_team(total: int, avail: dict[str, int], cap: dict[str, int]) -> dict[str, int]:
    """Общая цель → цели городов под целых медпредов: наименьшая команда, без «полупустых» медпредов.

    cap — скольких уникальных врачей охватывает один медпред в городе за срок (⌊N/f⌋ × визиты в день).
    В каждом городе с врачами — хотя бы один медпред; следующие добавляются туда, где больше всего
    врачей остаётся без медпреда (так доли городов близки к пропорциональным). Цель города — сколько
    охватят его медпреды; лишнее над общей целью снимается с последних медпредов крупных городов."""
    open_ = [s for s in avail if avail[s] > 0]
    total = min(total, sum(avail[s] for s in open_))
    reps = {s: 1 for s in open_}

    def covers(s: str) -> int:
        return min(avail[s], reps[s] * max(1, cap[s]))

    while sum(covers(s) for s in open_) < total:
        # следующий медпред — городу с наибольшим числом врачей на одного медпреда (пропорционально врачам)
        free = [x for x in open_ if covers(x) < avail[x]]
        if not free:
            break
        s = max(free, key=lambda x: (avail[x] / reps[x], avail[x]))
        reps[s] += 1
    out = {s: covers(s) for s in open_}
    extra = sum(out.values()) - total
    # излишек снимаем со всех городов пропорционально запасу последнего медпреда (не ниже того,
    # при чём этот медпред ещё нужен — иначе город потерял бы медпреда); остатки — крупнейшим
    if extra > 0:
        slack = {x: max(0, out[x] - ((reps[x] - 1) * max(1, cap[x]) + 1)) for x in open_}
        tot = sum(slack.values())
        if tot:
            raw = {x: extra * slack[x] / tot for x in open_}
            cut = {x: min(slack[x], int(raw[x])) for x in open_}
            left = extra - sum(cut.values())
            for x in sorted(open_, key=lambda y: -(raw[y] - int(raw[y]))):
                if left <= 0:
                    break
                if cut[x] < slack[x]:
                    cut[x] += 1
                    left -= 1
            out = {x: out[x] - cut[x] for x in open_}
    return {**{s: 0 for s in avail}, **out}


def _targets(pr: dict, avail: dict[str, int], cap: dict[str, int] | None = None) -> dict[str, int]:
    """Цель по каждому городу: все врачи базы, доля общей цели, общая цель под целых медпредов или своё число."""
    mode = (pr.get("goal") or {}).get("mode", "all")
    if mode == "total_min" and cap:
        return _targets_min_team(int(pr["goal"].get("total_doctors") or 0), avail, cap)
    if mode == "per_city":
        return {c["slug"]: int(c.get("target") or avail[c["slug"]]) for c in pr["cities"]}
    if mode == "total":
        total = int(pr["goal"].get("total_doctors") or 0)
        # пропорционально числу врачей в городе, но не больше, чем их есть; остаток — дальше
        left, out, open_ = total, {s: 0 for s in avail}, {s for s in avail if avail[s] > 0}
        while left > 0 and open_:
            pool = sum(avail[s] - out[s] for s in open_)
            give = {s: min(avail[s] - out[s], math.floor(left * (avail[s] - out[s]) / pool)) for s in open_}
            if not any(give.values()):  # остаток меньше числа городов — по одному крупнейшим
                for s in sorted(open_, key=lambda x: -(avail[x] - out[x]))[:left]:
                    give[s] = 1
            for s, g in give.items():
                out[s] += g
                left -= g
            open_ = {s for s in open_ if out[s] < avail[s]}
        return out
    return dict(avail)


def _run(pid: str) -> None:
    job = _jobs[pid]
    version = uuid.uuid4().hex
    version_dir = DIR / pid / version
    committed = False
    try:
        pr = get(pid)
        inputs = calculation_inputs(pr)
        cond = pr["conditions"]
        mode = pr["goal"]["mode"]
        has_goal = mode != "none"
        loaded = {c["slug"] for c in F.list_cities()}
        probes = {}
        for city in pr["cities"]:
            slug = city["slug"]
            job["current"] = (C.entry(slug) or {}).get("name", slug)
            if slug not in loaded:
                C.load(slug)
            probes[slug] = plan_project(_request(cond, slug, int(city.get("reps") or 1), pr.get("plans", {}).get(slug))
                                        | {"project_mix": True})
            job["done"] += 0.5
        avail = {slug: plan["summary"]["available_doctors"] for slug, plan in probes.items()}
        cap = {slug: sum(sp["capacity_per_rep"] for sp in plan["summary"]["specialties"])
               for slug, plan in probes.items()}
        total_goal = int(pr["goal"].get("total_doctors") or 0) if mode == "total_min" else None
        if total_goal is not None and total_goal < 1:
            raise ValueError("Укажите общую цель по охвату.")
        targets = _targets(pr, avail, cap)
        if total_goal is not None and sum(targets.values()) != min(total_goal, sum(avail.values())):
            targets = distribute(total_goal, {slug: targets[slug] or cap[slug] or 1 for slug in targets}, avail)

        version_dir.mkdir(parents=True)
        rows = []
        for city in pr["cities"]:
            slug = city["slug"]
            probe = probes.pop(slug)
            job["current"] = probe["summary"]["city"]
            auto = not city.get("reps") and has_goal
            row, plan = _city_result(slug, cond, pr.get("plans", {}).get(slug), city, probe, targets[slug], auto, has_goal)
            with gzip.open(_plan_file(pid, slug, version), "wt", encoding="utf-8") as fh:
                json.dump(plan, fh, ensure_ascii=False)
            rows.append(row)
            job["done"] += 0.5

        with _lock:
            current = get(pid)
            if calculation_inputs(current) != inputs:
                raise ValueError("Условия изменились во время расчёта. Запустите его заново.")
            result = {
                "id": version, "inputs": inputs, "stale": False, "project_mix": True,
                "daily_plan_revision": DAILY_PLAN_REVISION,
                "computed": time.strftime("%Y-%m-%d %H:%M"), "cities": rows,
                **_totals(rows, has_goal, total_goal),
            }
            (version_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            current["result"] = result
            save(current)
            committed = True
    except Exception as ex:
        job["error"] = str(ex)
    finally:
        if not committed and version_dir.is_dir():
            shutil.rmtree(version_dir)
        job["running"] = False
        job["current"] = ""


def _city_result(slug, cond, saved, city, probe, target, auto, has_goal):
    probe_rows = probe["summary"]["specialties"]
    quotas = specialty_targets(probe_rows, target) if has_goal else None
    caps = {r["specialty"]: r["capacity_per_rep"] for r in probe_rows}
    needed = required_team(probe_rows, quotas)
    reps = min(80, needed) if auto else int(city.get("reps") or 1)
    request = {**_request(cond, slug, reps, saved), "project_mix": True, "specialty_targets": quotas}
    plan = plan_project(request) if has_goal else probe
    s = plan["summary"]
    # Границы зон могут оставить неравномерный остаток по отдельной специальности.
    # Сравниваем фактический охват после повторного построения маршрутов.
    if auto and needed < 80:
        missed = [r for r in s["specialties"] if r["unique"] < min(r["available"], r["target"])
                  and caps[r["specialty"]]]
        if missed:
            estimate = max(math.ceil(reps * min(r["available"], r["target"]) / max(1, r["unique"])) for r in missed)
            retry_reps = min(80, max(reps + 1, estimate))
            retry_request = _request(cond, slug, retry_reps, saved) | {"project_mix": True, "specialty_targets": quotas}
            retry = plan_project(retry_request)
            if retry["summary"]["unique_doctors"] > s["unique_doctors"]:
                plan, s, request = retry, retry["summary"], retry_request
    plan = optimize_plan(request, plan, auto_team=auto)
    s = plan["summary"]
    breakdown = s["specialties"]
    issues = []
    fixes = []
    for sp in breakdown:
        if has_goal and sp["unique"] < sp["target"]:
            reason = ("в базе меньше врачей, чем требуется" if sp["available"] < sp["target"]
                      else "в маршрутах не набран охват при заданных зонах, сроке и нагрузке")
            issues.append({"level": "bad", "kind": "coverage",
                           "text": f"{sp['specialty']}: охвачено {sp['unique']} из {sp['target']} — {reason}."})
            if sp["available"] < sp["target"]:
                fixes.append(f"{sp['specialty']}: проверьте фильтры учреждений или дополните базу. "
                             f"Доступно {sp['available']} врачей при распределённой цели {sp['target']}.")
            else:
                fixes.append(f"{sp['specialty']}: проверьте зоны и число медпредов. Цель подбирается с учётом срока и кратности.")
        elif not has_goal and sp["visits"] < sp["planned"]:
            issues.append({"level": "warn", "text": f"{sp['specialty']}: в маршрутах {sp['visits']} из {sp['planned']} визитов."})
    if s["over_limit_days"]:
        issues.append({"level": "bad", "kind": "day_limit", "text": f"Сверх нормы {s['workday_limit_h']:g} ч "
                       f"по длительности или P80: {s['over_limit_days']} человеко-дней. "
                       f"Проверено расписаний: {s['checked_variants']}."})
        fixes.append("Пересчитайте проект с другой численностью команды или дневной нагрузкой и сравните длительность маршрутов.")
    elif s["risky_days"] > s["working_days"] * s["reps"] * 0.1:
        issues.append({"level": "warn", "text": f"Общая нагрузка: дней с риском переработки — {s['risky_days']}."})
    if s["pharmacy_visits"] < s["planned_pharmacy_visits"]:
        issues.append({"level": "warn", "text": f"Аптеки: в маршрутах {s['pharmacy_visits']} из {s['planned_pharmacy_visits']} визитов."})
    status = "bad" if any(i["level"] == "bad" for i in issues) else "warn" if issues else "ok"
    covered = s["unique_doctors"]
    curves = []
    if has_goal:
        for size in range(1, min(80, max(needed, s["reps"]) + 2) + 1):
            counts = [min(r["target"], r["available"], r["capacity_per_rep"] * size) for r in breakdown]
            curves.append({"reps": size, "covered": sum(counts),
                           "visits": sum(count * r["frequency"] for count, r in zip(counts, breakdown))})
    keys = ("city", "reps", "available_doctors", "available_pharmacies", "unique_doctors", "doctor_visits",
            "planned_doctor_visits", "pharmacy_visits", "planned_pharmacy_visits", "unique_pharmacies",
            "unique_lpu", "avg_hours", "avg_p80_hours", "risky_days", "reserve_days", "working_days",
            "avg_km", "total_km", "repeat_gap_days", "over_limit_days", "checked_variants")
    return {
        **{key: s[key] for key in keys}, "slug": slug, "reps_auto": auto,
        "reps_needed": max(needed, s["reps"]) if auto else needed,
        "target": target, "covered_doctors": covered, "coverage_pct": round(100 * covered / target) if target else 100,
        "status": status, "issues": issues, "fixes": fixes, "curve": curves, "specialties": breakdown,
        "goal_visits": sum(r["visits"] for r in breakdown) if has_goal else 0, "max_doctor_visits": None,
        "zones": [{"id": r["id"], "color": r["color"], "polygon": r["polygon"], "doctors": r["doctors"]}
                  for r in plan["reps"]],
        "points": [[round(f["lat"], 5), round(f["lon"], 5), 1 if f["kind"] == "lpu" else 0]
                   for f in plan["facilities"]],
    }, plan


def _totals(rows: list[dict], has_goal: bool = True, total_goal: int | None = None) -> dict:
    key = ("reps", "target", "available_doctors", "covered_doctors", "doctor_visits", "planned_doctor_visits",
           "pharmacy_visits", "planned_pharmacy_visits", "unique_pharmacies", "unique_lpu", "total_km", "over_limit_days")
    t = {k: sum(r[k] for r in rows) for k in key}
    if total_goal is not None:
        t["unallocated_target"] = max(0, total_goal - t["target"])
        t["target"] = total_goal
    t["reps_needed"] = sum(r["reps_needed"] for r in rows)
    t["coverage_pct"] = round(t["covered_doctors"] / t["target"] * 100) if t["target"] else 100
    w = sum(r["reps"] * r["working_days"] for r in rows) or 1
    t["avg_hours"] = round(sum(r["avg_hours"] * r["reps"] * r["working_days"] for r in rows) / w, 2)
    t["avg_p80_hours"] = round(sum(r["avg_p80_hours"] * r["reps"] * r["working_days"] for r in rows) / w, 2)
    t["working_days"] = max((r["working_days"] for r in rows), default=0)
    t["cities_bad"] = sum(1 for r in rows if r["status"] == "bad")
    t["cities_warn"] = sum(1 for r in rows if r["status"] == "warn")
    t["goal_visits"] = sum(r["goal_visits"] for r in rows)
    t["has_goal"] = has_goal
    if not has_goal:   # без цели нечего подбирать: ни вариантов команды, ни кривой охвата
        t["reps_needed"] = t["reps"]
        return {"totals": t, "allocation": [], "variants": []}
    return {"totals": t, "allocation": _allocation(rows), "variants": []}


def _allocation(rows: list[dict]) -> list[dict]:
    """Сколько врачей охватит проект при разном общем числе медпредов.

    Медпреды распределяются по городам там, где каждый следующий даёт больше новых врачей
    (кривые охвата вогнутые, поэтому жадное распределение оптимально).
    """
    if not rows:
        return []
    gain = {r["slug"]: {c["reps"]: min(c["covered"], r["target"]) for c in r["curve"]} for r in rows}
    visits = {r["slug"]: {c["reps"]: c["visits"] for c in r["curve"]} for r in rows}
    alloc = {r["slug"]: 1 for r in rows}

    def cov(slug, k):
        g = gain[slug]
        return g.get(k, g[max(g)])

    out = []
    current = sum(r["reps"] for r in rows)
    needed = sum(r["reps_needed"] for r in rows)
    total_max = min(300, max(current, needed) + 2)
    R = len(rows)
    while True:
        covered = sum(cov(s, alloc[s]) for s in alloc)
        visit_count = sum(visits[s].get(k, visits[s][max(visits[s])]) for s, k in alloc.items())
        out.append({"reps": R, "covered": covered, "visits": visit_count, "split": dict(alloc)})
        if R >= total_max:
            break
        # следующий медпред — туда, где он даст больше всего новых врачей (при равенстве — в крупный город)
        best = max(alloc, key=lambda s: (cov(s, alloc[s] + 1) - cov(s, alloc[s]), gain[s][min(gain[s])]))
        alloc[best] += 1
        R += 1
    return out
