"""
Оценка параметров проекта и рекомендации.

По уже построенному плану оцениваются «стоимость» одного визита к врачу и в аптеку в минутах
(ожидание, сам визит, вход в ЛПУ, доля дороги) и постоянная часть дня (обед, отчёт).
Отсюда простая модель:

    длина дня(d, p) = постоянная часть + a·d + b·p,   P80 ≈ длина · k,
    поставленные визиты = Σ по МП min(⌊N/f⌋·d, врачей в зоне) · f,

где d, p — визиты к врачам и в аптеки в день, f — кратность, N — рабочие дни.
Модель мгновенно считает план при других значениях — для рекомендаций и графиков.
"""
from __future__ import annotations

import math

import numpy as np

WORKDAYS_PER_MONTH = 20.6


def benchmarks() -> dict | None:
    """Ориентиры из отчётов реальных проектов (data/benchmarks.json)."""
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "benchmarks.json"
    if not p.exists():
        return None
    b = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in b.items() if k != "projects"}


def _rate(days, nm):
    """Минуты на один визит к врачу (a) и в аптеку (b), постоянная часть дня (F), коэффициент P80 (k)."""
    a_list, b_list, k_list = [], [], []
    for d in days:
        t = d["totals"]
        if not d["stops"]:
            continue
        seg = d["segments"]
        travel = sum(x[2] for x in seg if x[0] == "travel")
        stops = max(1, len(d["stops"]))
        lpu_stops = sum(1 for s in d["stops"] if s["type"] == "lpu")
        ph_stops = stops - lpu_stops
        if t["doctor_visits"]:
            doc_min = sum(x[2] for x in seg if x[0] in ("entry", "visit")) \
                + t["doctor_visits"] * nm["wait"] + travel * lpu_stops / stops
            a_list.append(doc_min / t["doctor_visits"])
        if t["pharmacy_visits"]:
            b_list.append(nm["ph_wait"] + nm["ph_visit"] + travel * ph_stops / stops / t["pharmacy_visits"])
        if t["hours"] > 0 and t["p80_hours"]:
            k_list.append(t["p80_hours"] / t["hours"])
    a = float(np.median(a_list)) if a_list else nm["wait"] + nm["visit"] + nm["entry"] + 15
    b = float(np.median(b_list)) if b_list else nm["ph_wait"] + nm["ph_visit"] + 12
    k = float(np.median(k_list)) if k_list else 1.12
    return a, b, nm["lunch"] + nm["crm"], k


class Model:
    def __init__(self, days, nm, n_days, reps_avail, ph_avail, limit_h):
        self.a, self.b, self.F, self.k = _rate(days, nm)
        self.n = n_days
        self.avail = list(reps_avail)          # врачей в зоне каждого МП
        self.total = sum(self.avail)
        self.ph_total = ph_avail
        self.limit = limit_h

    def hours(self, d, p):
        return (self.F + self.a * d + self.b * p) / 60

    def p80(self, d, p):
        return self.hours(d, p) * self.k

    def max_d(self, p):
        """Больше всего визитов к врачам, при которых день с вероятностью 80% укладывается в норму."""
        d = int((self.limit * 60 / self.k - self.F - self.b * p) // self.a)
        return max(1, d)

    def placed(self, d, f, reps=None, n=None):
        """Сколько визитов к врачам встанет в маршруты (врачи в зонах ограничены)."""
        n = n or self.n
        avail = self.avail if reps is None or reps == len(self.avail) else [self.total / reps] * reps
        return sum(min((n // f) * d, a) * f for a in avail)

    def evaluate(self, d, f, p, fp, reps=None, n=None):
        reps = reps or len(self.avail)
        n = n or self.n
        planned = n * d * reps
        placed = self.placed(d, f, reps, n)
        ph_need = math.ceil(n * p / fp) * reps if p else 0
        return {
            "hours": round(self.hours(d, p), 2), "p80": round(self.p80(d, p), 2),
            "planned": planned, "placed": int(placed),
            "coverage": round(placed / planned * 100) if planned else 100,
            "doctors_needed": (n // f) * d * reps, "doctors_available": int(self.total),
            "pharmacies_needed": ph_need, "pharmacies_available": self.ph_total,
        }


def _pl(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _months(n_days):
    return max(1, int(n_days / WORKDAYS_PER_MONTH))


def advise(days, nm, ctx) -> dict:
    """ctx: n, reps, dpd, ppd, f_d, f_p, months, limit_h, reps_avail[], ph_avail."""
    n, reps, d, p = ctx["n"], ctx["reps"], ctx["dpd"], ctx["ppd"]
    f, fp, limit = ctx["f_d"], ctx["f_p"], ctx["limit_h"]
    m = Model([x for x in days if x["kind"] == "work"] or days, nm, n, ctx["reps_avail"], ctx["ph_avail"], limit)
    # визиты к врачам дополнительных специальностей — постоянная часть дня при любом числе основных
    m.F += m.a * ctx.get("extra_per_day", 0)
    cur = m.evaluate(d, f, p, fp)
    d_time = m.max_d(p)
    params, scenarios = [], []

    # ── Визиты к врачам в день: укладываются ли они в рабочий день ──────────
    if d and cur["p80"] > limit + 0.05:
        params.append({"key": "doctor_visits_per_day", "label": "Визитов к врачам в день", "current": d,
                       "recommended": d_time, "status": "bad",
                       "why": f"При {d} визитах средний день {cur['hours']:.1f} ч, с вероятностью 80% — до "
                              f"{cur['p80']:.1f} ч, больше нормы {limit:g} ч. Один визит к врачу с ожиданием, "
                              f"регистратурой и дорогой занимает около {m.a:.0f} мин, поэтому в день укладывается "
                              f"не больше {d_time}."})
    elif d and m.p80(d + 1, p) <= limit and cur["hours"] < limit * 0.7:
        params.append({"key": "doctor_visits_per_day", "label": "Визитов к врачам в день", "current": d,
                       "recommended": d_time, "status": "warn",
                       "why": f"День заполнен на {cur['hours'] / limit * 100:.0f}% ({cur['hours']:.1f} ч из {limit:g}). "
                              f"По нормативам медпред успевает до {d_time} визитов к врачам."})
    else:
        params.append({"key": "doctor_visits_per_day", "label": "Визитов к врачам в день", "current": d,
                       "recommended": d, "status": "ok",
                       "why": f"День {cur['hours']:.1f} ч, с вероятностью 80% до {cur['p80']:.1f} ч — в пределах нормы."})

    d_ok = min(d, d_time) if d else 0
    base = m.evaluate(d_ok, f, p, fp)
    # дни-остаток после деления проекта на круги визитов — резерв, а не нехватка врачей
    reachable = (n // f) * f * d_ok * reps
    short = d_ok and base["placed"] < reachable * 0.97

    # ── Кратность, число МП, срок: хватает ли врачей в зонах ───────────────
    if short:
        f_rec = max(f + 1, min(12, math.ceil(n * d_ok / max(1, min(m.avail) if m.avail else m.total))))
        reps_rec = max(1, int(m.total // max(1, (n // f) * d_ok)))
        n_rec = int(m.total / reps / max(1, d_ok)) * f
        months_rec = n_rec / WORKDAYS_PER_MONTH
        d_rec = max(1, int(min(m.avail) * f // n)) if m.avail else 1
        why_base = (f"В зонах около {int(m.total)} врачей этой специальности, а при {d_ok} визитах в день и "
                    f"кратности {f} нужно {base['doctors_needed']} уникальных врачей: встанет только "
                    f"{base['placed']} визитов из {base['planned']} ({base['coverage']}%).")
        params.append({"key": "doctor_frequency", "label": "Кратность визитов к врачу", "current": f,
                       "recommended": f_rec, "status": "bad",
                       "why": why_base + f" Чтобы заполнить все дни, каждого врача нужно посетить {f_rec} {_pl(f_rec, 'раз', 'раза', 'раз')}."})
        params.append({"key": "reps", "label": "Медпредов", "current": reps, "recommended": reps_rec,
                       "status": "bad" if reps_rec < reps else "ok",
                       "why": f"Врачей хватает на {reps_rec} {_pl(reps_rec, 'медпреда', 'медпредов', 'медпредов')} при {d_ok} "
                              f"{_pl(d_ok, 'визите', 'визитах', 'визитах')} в день и кратности {f}."})
        params.append({"key": "months", "label": "Срок проекта", "current": ctx["months"],
                       "recommended": _months(n_rec) if months_rec >= 1 else 1, "status": "bad",
                       "why": f"При тех же медпредах, визитах и кратности врачи заканчиваются через "
                              f"{n_rec} рабочих дней (≈ {months_rec:.1f} мес.)."})
        scenarios += [
            {"title": f"Поднять кратность до {f_rec}", "change": {"doctor_visits_per_day": d_ok, "doctor_frequency": f_rec},
             "note": "Те же медпреды и срок, каждого врача посещают чаще.",
             **m.evaluate(d_ok, f_rec, p, fp)},
            {"title": f"{reps_rec} {_pl(reps_rec, 'медпред', 'медпреда', 'медпредов')} вместо {reps}", "change": {"doctor_visits_per_day": d_ok, "reps": reps_rec},
             "note": "Кратность и срок как в плане, город делится на меньшее число зон.",
             **m.evaluate(d_ok, f, p, fp, reps=reps_rec)},
        ]
        if months_rec >= 1 and _months(n_rec) < ctx["months"]:
            mr = _months(n_rec)
            scenarios.append({"title": f"Срок {mr} мес. вместо {ctx['months']}", "change": {"doctor_visits_per_day": d_ok, "months": mr},
                              "note": "Те же медпреды и кратность, проект короче.",
                              **m.evaluate(d_ok, f, p, fp, n=int(mr * WORKDAYS_PER_MONTH))})
        if d_rec < d_ok:
            scenarios.append({"title": f"{d_rec} {_pl(d_rec, 'визит', 'визита', 'визитов')} к врачам в день", "change": {"doctor_visits_per_day": d_rec},
                              "note": "Всё остальное как в плане; день будет недогружен — можно добавить аптеки.",
                              **m.evaluate(d_rec, f, p, fp)})
    else:
        params.append({"key": "doctor_frequency", "label": "Кратность визитов к врачу", "current": f,
                       "recommended": f, "status": "ok",
                       "why": f"Врачей в зонах ({int(m.total)}) хватает: нужно {base['doctors_needed']} уникальных."})
        params.append({"key": "reps", "label": "Медпредов", "current": reps, "recommended": reps, "status": "ok",
                       "why": "Нагрузка между зонами выровнена, врачей в каждой зоне хватает."})
        if d and d_time > d and cur["hours"] < limit * 0.7:
            scenarios.append({"title": f"{d_time} {_pl(d_time, 'визит', 'визита', 'визитов')} к врачам в день", "change": {"doctor_visits_per_day": d_time},
                              "note": "Полнее использовать рабочий день.", **m.evaluate(d_time, f, p, fp)})

    if d > d_time:
        scenarios.insert(0, {"title": f"{d_time} {_pl(d_time, 'визит', 'визита', 'визитов')} к врачам в день", "change": {"doctor_visits_per_day": d_time},
                             "note": "Оценка при прежней нагрузке по остальным визитам. Проверьте после пересчёта.",
                             **m.evaluate(d_time, f, p, fp)})

    # ── Аптеки ───────────────────────────────────────────────────────────────
    if p:
        need = cur["pharmacies_needed"]
        if need > m.ph_total:
            fp_rec = min(12, math.ceil(n * p * reps / max(1, m.ph_total)))
            params.append({"key": "pharmacy_frequency", "label": "Кратность визитов в аптеку", "current": fp,
                           "recommended": fp_rec, "status": "bad",
                           "why": f"Нужно {need} аптек, в базе с учётом фильтров {m.ph_total}. "
                                  f"При кратности {fp_rec} аптек хватит."})
        else:
            params.append({"key": "pharmacy_frequency", "label": "Кратность визитов в аптеку", "current": fp,
                           "recommended": fp, "status": "ok", "why": f"Аптек хватает: нужно {need} из {m.ph_total}."})

    # ── Кривые «что если» по числу визитов к врачам в день ─────────────────
    f_alt = next((x["recommended"] for x in params if x["key"] == "doctor_frequency" and x["status"] != "ok"), None)
    curve = []
    for dd in range(1, max(15, d + 3) + 1):
        e = m.evaluate(dd, f, p, fp)
        item = {"d": dd, "hours": e["hours"], "p80": e["p80"], "placed": e["placed"], "planned": e["planned"]}
        if f_alt:
            item["placed_alt"] = int(m.placed(dd, f_alt))
        curve.append(item)

    bm = benchmarks()
    if bm:
        r = bm["doctor_visits_per_day"]
        note = (f" В реальных проектах медпред делает {r['low']}–{r['high']} визитов к врачам в день "
                f"(обычно {r['median']:g}), обходя 2–3 ЛПУ.")
        for x in params:
            if x["key"] == "doctor_visits_per_day":
                x["why"] += note
    return {
        "params": params, "scenarios": scenarios[:4], "curve": curve, "alt_frequency": f_alt, "benchmarks": bm,
        "model": {"per_doctor_min": round(m.a), "per_pharmacy_min": round(m.b), "fixed_min": round(m.F),
                  "p80_factor": round(m.k, 2), "max_doctor_visits": d_time, "limit_h": limit},
        "ok": all(x["status"] == "ok" for x in params),
    }
