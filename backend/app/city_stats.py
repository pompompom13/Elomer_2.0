"""Статистика одного города для вкладки «Все города»: ЛПУ, аптеки, сети, врачи по специальностям."""
from __future__ import annotations

import statistics
from collections import Counter

from . import facilities as F
from . import norms as N
from . import care
from .cities import index
from .doctor_stats import calibration
from .networks import annotate

TOP_NETWORKS = 10


def city_stats(slug: str, selected: care.CareFilter = "all", client: str = "") -> dict:
    ds = F.load_city(slug)
    if client:
        from . import client_bases as CB
        ds = CB.with_client(ds, client)
    entry = next((e for e in index() if e["slug"] == slug), {})
    fac = annotate([f for f in ds["facilities"] if not f.get("needs_coords")])
    lpu = [f for f in fac if f["kind"] == "lpu" and not F.excluded_name(f["name"])
           and care.matches(f["care_level"], selected)]
    ph_all = [f for f in fac if f["kind"] == "pharmacy"]
    ph = [f for f in ph_all if f.get("in_registry") is not False]
    pop = entry.get("population") or ds.get("population")

    nets = Counter(f["network_name"] for f in ph if f["network"])
    singles = sum(1 for f in ph if not f["network"])
    top = nets.most_common(TOP_NETWORKS)
    other = sum(nets.values()) - sum(n for _, n in top)
    turnover = [f["turnover"] for f in ph if f.get("turnover")]

    doctors = []
    for spec in N.SPECIALTIES:
        cal = calibration(slug, spec)
        overrides = CB.doctor_counts(ds["facilities"], slug, spec) if client else {}
        confirmed = sum(int((f.get("known_doctors") or {}).get(spec, 0)) for f in lpu
                        if f["care_level"] != "mixed" or selected in ("all", "mixed"))
        estimate = int(cal["target"] or 0) if selected == "all" and not client else sum(
            care.count(len(N.doctors_in(f, spec, overrides, cal)), f["care_level"], selected) for f in lpu
        )
        doctors.append({"spec": spec, "n": estimate, "confirmed": confirmed})
    doctors.sort(key=lambda x: -x["n"])

    return {
        "slug": slug, "name": ds["name"], "region": entry.get("region", ""), "population": pop,
        "updated": ds.get("updated", ""),
        "lpu": {
            "total": len(lpu),
            "state": sum(1 for f in lpu if f["ownership"] == "state"),
            "private": sum(1 for f in lpu if f["ownership"] == "private"),
            "by_category": dict(Counter(f["category"] for f in lpu)),
            "sources": dict(Counter(f["source"] for f in lpu)),
            "confirmed": sum(1 for f in lpu if f.get("known_doctors")),
        },
        "pharmacies": {
            "total": len(ph),
            "state": sum(1 for f in ph if f["ownership"] == "state"),
            "private": sum(1 for f in ph if f["ownership"] == "private"),
            "networks": [{"name": k, "n": v} for k, v in top],
            "other_networks": other, "singles": singles, "network_count": len(nets),
            "sources": {
                "map_and_registry": sum(1 for f in ph if f["source"] == "osm" and f.get("in_registry")),
                "registry_only": sum(1 for f in ph if f["source"] == "registry"),
                "map_only": sum(1 for f in ph if f["source"] == "osm" and f.get("in_registry") is None),
                "not_in_registry": sum(1 for f in ph_all if f.get("in_registry") is False),
            },
            "turnover_median": int(statistics.median(turnover)) if turnover else None,
            "per_10k": round(len(ph) / pop * 10000, 1) if pop else None,
        },
        "doctors": doctors,
        "lpu_per_100k": round(len(lpu) / pop * 100000, 1) if pop else None,
    }
