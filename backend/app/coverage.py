"""Распределение целочисленной цели с учётом пропорций и доступной базы."""
from math import ceil, floor


def distribute(total, weights, limits):
    counts = dict.fromkeys(weights, 0)
    left = min(max(0, total), sum(limits[k] for k in weights if weights[k] > 0))
    active = [k for k in weights if weights[k] > 0 and limits[k] > 0]
    while left:
        weight = sum(weights[k] for k in active)
        shares = {k: left * weights[k] / weight for k in active}
        capped = [k for k in active if shares[k] >= limits[k]]
        if capped:
            for k in capped:
                counts[k] = limits[k]
                left -= limits[k]
            active = [k for k in active if k not in capped]
            continue
        for k, share in shares.items():
            counts[k] = floor(share)
        remainder = left - sum(counts[k] for k in active)
        for k in sorted(active, key=lambda k: (-(shares[k] - counts[k]), str(k)))[:remainder]:
            counts[k] += 1
        break
    return counts


def specialties(p):
    rows = [{
        "specialty": p["specialty"],
        "doctor_visits_per_day": p["doctor_visits_per_day"],
        "doctor_frequency": p["doctor_frequency"],
    }, *(p.get("extra_specialties") or [])]
    combined = {row["specialty"]: row for row in reversed(rows) if row.get("specialty")}
    return [combined[name] for name in sorted(combined) if int(combined[name].get("doctor_visits_per_day") or 0) > 0]


def specialty_targets(rows, target):
    available = {row["specialty"]: row["available"] for row in rows}
    weights = {row["specialty"]: row["capacity_per_rep"] or row["per_day"] for row in rows}
    quotas = distribute(target, weights, available)
    missing = target - sum(quotas.values())
    if missing > 0:
        extra = distribute(missing, weights, dict.fromkeys(weights, missing))
        quotas = {sp: n + extra[sp] for sp, n in quotas.items()}
    return quotas


def required_team(rows, quotas):
    estimates = [ceil(min(row["available"], quotas[row["specialty"]]) / row["capacity_per_rep"])
                 for row in rows if quotas is not None and row["capacity_per_rep"]]
    return max([1, *estimates])
