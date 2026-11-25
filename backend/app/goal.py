"""
Обратный расчёт: от цели проекта к числу медпредов и визитов в день.

Цель — «охватить T врачей с кратностью f за срок проекта». Один медпред за N рабочих дней
проходит ⌊N/f⌋ дней в каждом круге визитов, то есть посещает ⌊N/f⌋·d уникальных врачей
при d визитах в день. Отсюда

    медпредов нужно   R = ⌈T / (⌊N/f⌋·d)⌉,
    визитов в день    d = ⌈T / (R·⌊N/f⌋)⌉   при заданных R.

Длина дня для каждого варианта считается по модели, откалиброванной на реальном плане
этого города (минуты на визит к врачу и в аптеку с дорогой, обед и отчёт, коэффициент P80).
"""
from __future__ import annotations

import math

from .advisor import _pl


class DayModel:
    def __init__(self, model: dict):
        self.a = model["per_doctor_min"]
        self.b = model["per_pharmacy_min"]
        self.F = model["fixed_min"]
        self.k = model["p80_factor"]
        self.limit = model["limit_h"]
        self.d_max = model["max_doctor_visits"]

    def hours(self, d: int, p: int) -> float:
        return (self.F + self.a * d + self.b * p) / 60

    def p80(self, d: int, p: int) -> float:
        return self.hours(d, p) * self.k

    def max_d(self, p: int) -> int:
        """Больше всего визитов к врачам в день, при которых день с вероятностью 80% укладывается в норму."""
        return max(0, int((self.limit * 60 / self.k - self.F - self.b * p) // self.a))


def options(plan: dict, target: int | None, auto_d: bool = False) -> dict:
    """Варианты «медпредов × визитов в день» для цели по уже рассчитанному плану города.

    auto_d — визиты в день не заданы пользователем: базой служит наибольшее число визитов,
    которое укладывается в норму дня (его и подбирает инструмент)."""
    s = plan["summary"]
    m = DayModel(plan["advice"]["model"])
    n, f = s["working_days"], s["doctor_frequency"]
    d_user, p = s["doctor_visits_per_day"], s["pharmacy_visits_per_day"]
    if auto_d:
        d_user = max(1, min(m.d_max, 20))
    fp = s["pharmacy_frequency"]
    avail = s["available_doctors"]
    ph_avail = s["available_pharmacies"]
    waves = max(1, n // f)
    T = int(target) if target else avail
    T_eff = min(T, avail)

    def reps_for(d: int) -> int:
        return max(1, math.ceil(T_eff / (waves * max(1, d)))) if T_eff else 1

    r_user = reps_for(d_user)                       # при визитах в день, как в условиях
    d_fit = max(1, min(m.d_max, 20))
    r_fit = reps_for(d_fit)                         # если загрузить день до нормы
    hi = max(r_user, r_fit) + 2
    rows = []
    for r in range(1, min(40, max(hi, 6)) + 1):
        d_need = max(1, math.ceil(T_eff / (r * waves))) if T_eff else 0
        covered = min(T_eff, r * waves * d_user)
        ph_need = math.ceil(n * p / fp) * r if p else 0
        rows.append({
            "reps": r, "d_needed": d_need,
            "hours": round(m.hours(d_need, p), 2), "p80": round(m.p80(d_need, p), 2),
            "fits": d_need <= m.d_max,
            "covered_at_user_d": covered,
            "doctor_visits_at_user_d": covered * f,
            "pharmacies_needed": ph_need, "pharmacies_short": bool(p) and ph_need > ph_avail,
        })
    best = next((x for x in rows if x["fits"]), rows[-1])

    notes = []
    if T > avail:
        notes.append(f"В базе города около {avail} врачей этой специальности (с учётом фильтров) — "
                     f"цель {T} недостижима; расчёт сделан на {avail}.")
    if d_user > m.d_max:
        notes.append(f"{d_user} {_pl(d_user, 'визит', 'визита', 'визитов')} к врачам в день не укладываются в "
                     f"{m.limit:g}-часовой день: реально до {m.d_max}. При {d_user} визитах нужно "
                     f"{r_user} {_pl(r_user, 'медпред', 'медпреда', 'медпредов')}, но дни будут с переработкой.")
    return {
        "target": T, "target_effective": T_eff, "available": avail, "working_days": n, "waves": waves,
        "frequency": f, "pharmacy_visits_per_day": p, "doctor_visits_per_day": d_user,
        "max_doctor_visits": m.d_max, "limit_h": m.limit,
        "reps_at_user_d": r_user, "reps_at_full_day": r_fit, "full_day_d": d_fit,
        "recommended": {"reps": best["reps"], "doctor_visits_per_day": best["d_needed"]},
        "options": rows, "notes": [] if auto_d else notes, "auto_d": auto_d,
        "per_rep_unique": waves * d_user,
    }


def team(plan: dict, target_d: int | None, target_p: int | None, use_d: bool = True, use_p: bool = True) -> dict:
    """Цель охвата врачей и/или аптек → наименьшая команда и визиты в день (подбирает инструмент).

    Медпред за N рабочих дней проходит ⌊N/f⌋ дней в каждом круге визитов к врачам и ⌊N/fp⌋ — к аптекам,
    поэтому при R медпредах нужно d = ⌈Tд / (R·⌊N/f⌋)⌉ визитов к врачам и p = ⌈Tа / (R·⌊N/fp⌋)⌉ в аптеки
    в день. Берём наименьшее R, при котором такой день с вероятностью 80% укладывается в норму."""
    s = plan["summary"]
    m = DayModel(plan["advice"]["model"])
    n = s["working_days"]
    f, fp = s["doctor_frequency"], s["pharmacy_frequency"]
    w_d, w_p = max(1, n // f), max(1, n // fp)
    avail, ph_avail = s["available_doctors"], s["available_pharmacies"]
    T = (int(target_d) if target_d else avail) if use_d else 0
    Tp = (int(target_p) if target_p else ph_avail) if use_p else 0
    T_eff, Tp_eff = min(T, avail), min(Tp, ph_avail)

    def row(r: int) -> dict:
        d = math.ceil(T_eff / (r * w_d)) if T_eff else 0
        p = math.ceil(Tp_eff / (r * w_p)) if Tp_eff else 0
        p80 = m.p80(d, p)
        return {"reps": r, "d_needed": d, "p_needed": p, "hours": round(m.hours(d, p), 2), "p80": round(p80, 2),
                "fits": p80 <= m.limit + 1e-9,
                "covered_at_user_d": min(T_eff, r * w_d * d), "doctor_visits_at_user_d": min(T_eff, r * w_d * d) * f,
                "ph_covered": min(Tp_eff, r * w_p * p), "ph_visits": min(Tp_eff, r * w_p * p) * fp,
                "pharmacies_needed": 0, "pharmacies_short": False}

    best = next((r for r in range(1, 301) if row(r)["fits"]), 300)
    lo = 1 if best <= 8 else best - 4
    rows = [row(r) for r in range(lo, max(lo + 5, best + 2) + 1)]
    rec = row(best)

    notes = []
    if use_d and T > avail:
        notes.append(f"В базе города около {avail} врачей этой специальности (с учётом фильтров) — "
                     f"цель {T} недостижима; расчёт сделан на {avail}.")
    if use_p and Tp > ph_avail:
        notes.append(f"В базе города {ph_avail} аптек (с учётом фильтров) — цель {Tp} недостижима; расчёт сделан на {ph_avail}.")
    if not rec["fits"]:
        notes.append("Даже большая команда не укладывает день в норму — проверьте кратность и нормативы.")
    return {
        "auto_d": True, "use_doctors": use_d, "use_pharmacies": use_p,
        "target": T, "target_effective": T_eff, "available": avail,
        "ph_target": Tp, "ph_target_effective": Tp_eff, "ph_available": ph_avail,
        "working_days": n, "waves": w_d, "ph_waves": w_p, "frequency": f, "ph_frequency": fp,
        "doctor_visits_per_day": rec["d_needed"], "pharmacy_visits_per_day": rec["p_needed"],
        "max_doctor_visits": m.max_d(rec["p_needed"]), "limit_h": m.limit,
        "reps_at_user_d": best, "reps_at_full_day": best, "full_day_d": rec["d_needed"],
        "recommended": {"reps": best, "doctor_visits_per_day": rec["d_needed"], "pharmacy_visits_per_day": rec["p_needed"]},
        "options": rows, "notes": notes, "per_rep_unique": w_d * rec["d_needed"],
    }
