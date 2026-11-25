"""
Нормативы планирования.

Длительности визитов и ожидания — по отчётам реальных проектов (см. data/benchmarks.json).
Число врачей нужной специальности в учреждении: загруженный список врачей, если он есть;
иначе статистика по городу/региону (doctor_stats.py), разложенная по учреждениям
пропорционально весам типов учреждений; если статистики нет — сами веса как оценка.
Все значения можно переопределить в запросе на расчёт.
"""

SPECIALTIES = [
    "Кардиолог", "Терапевт", "Педиатр", "Невролог", "Эндокринолог", "ЛОР",
    "Гастроэнтеролог", "Гинеколог", "Уролог", "Офтальмолог", "Дерматолог", "Пульмонолог",
    "Аллерголог-иммунолог", "Ревматолог", "Хирург", "Онколог",
    # добавлены в ноябре 2026: стоматологи (свой тип учреждений) и узкие специальности больниц
    "Стоматолог", "Травматолог-ортопед", "Нефролог", "Гематолог", "Инфекционист", "Колопроктолог",
    "Сосудистый хирург", "Нейрохирург",
]

# Медиана ожидания врача, мин (из профилей ожидания версии 11.2.1)
WAIT_MEDIAN = {
    "Кардиолог": 25, "Терапевт": 18, "Педиатр": 16, "Невролог": 22, "Эндокринолог": 22,
    "ЛОР": 22, "Гастроэнтеролог": 18, "Гинеколог": 20, "Уролог": 20, "Офтальмолог": 18,
    "Дерматолог": 18, "Пульмонолог": 22, "Аллерголог-иммунолог": 20, "Ревматолог": 22, "Хирург": 20,
    "Онколог": 25,
    "Стоматолог": 15, "Травматолог-ортопед": 20, "Нефролог": 22, "Гематолог": 22, "Инфекционист": 20,
    "Колопроктолог": 20, "Сосудистый хирург": 22, "Нейрохирург": 25,
}

DOCTOR_VISIT_MIN = 11      # медиана длительности визита к врачу в отчётах проектов: 10–13 мин
DOCTOR_VISIT_SD = 3.5
PHARMACY_VISIT_MIN = 12    # медиана визита в аптеку в отчётах: 10–13 мин
PHARMACY_VISIT_SD = 3
PHARMACY_WAIT_MIN = 5      # ожидание первостольника / заведующей
LPU_ENTRY_MIN = 8          # регистратура, пропуск, поиск кабинета
LUNCH_MIN = 40
CRM_END_OF_DAY_MIN = 25
WORKDAY_LIMIT_H = 8.0
DAY_START = "09:00"

CITY_NORM_DEFAULTS = {
    "transport": "car", "day_start": DAY_START, "workday_limit_h": WORKDAY_LIMIT_H,
    "travel_time_factor": 1.0, "travel_detour": None, "travel_overhead_min": None,
    "wait_min": None, "doctor_visit_min": None, "pharmacy_visit_min": None,
    "pharmacy_wait_min": None, "lpu_entry_min": None, "lunch_min": None, "crm_min": None,
}

TRANSPORT = {
    # скорость по прямой с учётом пробок, коэффициент извилистости дорог, парковка/ожидание
    "car":    {"label": "Автомобиль",             "speed_kmh": 24, "detour": 1.35, "overhead_min": 7},
    "public": {"label": "Общественный транспорт", "speed_kmh": 15, "detour": 1.30, "overhead_min": 8},
    "walk":   {"label": "Пешком",                 "speed_kmh": 4.5, "detour": 1.25, "overhead_min": 0},
}

# Оценка числа врачей специальности в учреждении по типу учреждения
# Веса уточнены по 17,6 тыс. врачей, подтверждённых визитами (ноябрь 2026): в частных медцентрах
# врачей больше, чем считалось; во взрослых поликлиниках есть педиатры, в детских — терапевты.
DOCTORS_PER_CATEGORY = {
    "Кардиолог":       {"polyclinic_adult": 2, "polyclinic_child": 1, "hospital": 3, "private_center": 2, "cardio_center": 6},
    "Терапевт":        {"polyclinic_adult": 12, "polyclinic_child": 1, "hospital": 4, "private_center": 3, "ambulatory": 2},
    "Педиатр":         {"polyclinic_child": 10, "polyclinic_adult": 2, "private_center": 3, "ambulatory": 1},
    "Невролог":        {"polyclinic_adult": 3, "polyclinic_child": 2, "hospital": 2, "private_center": 2},
    "Эндокринолог":    {"polyclinic_adult": 1, "hospital": 1, "private_center": 1},
    "ЛОР":             {"polyclinic_adult": 2, "polyclinic_child": 2, "hospital": 1, "private_center": 2},
    "Гастроэнтеролог": {"polyclinic_adult": 1, "hospital": 1, "private_center": 1},
    "Гинеколог":       {"womens": 6, "polyclinic_adult": 1, "hospital": 2, "private_center": 1},
    "Уролог":          {"polyclinic_adult": 1, "hospital": 1, "private_center": 1},
    "Офтальмолог":     {"polyclinic_adult": 2, "polyclinic_child": 1, "hospital": 1, "private_center": 1},
    "Дерматолог":      {"polyclinic_adult": 1, "polyclinic_child": 1, "hospital": 1, "private_center": 1},
    "Пульмонолог":     {"polyclinic_adult": 1, "polyclinic_child": 1, "hospital": 2, "private_center": 1},
    "Аллерголог-иммунолог": {"polyclinic_adult": 1, "polyclinic_child": 1, "hospital": 1, "private_center": 1},
    "Ревматолог":      {"polyclinic_adult": 1, "hospital": 1, "private_center": 1},
    "Хирург":          {"polyclinic_adult": 2, "polyclinic_child": 2, "hospital": 6, "private_center": 1},
    "Онколог":         {"polyclinic_adult": 1, "hospital": 2, "private_center": 1},
    # стоматологи — в основном в стоматологиях; в поликлиниках бывают стоматологические кабинеты
    "Стоматолог":      {"dental": 6, "polyclinic_adult": 1, "polyclinic_child": 1},
    "Травматолог-ортопед": {"polyclinic_adult": 2, "polyclinic_child": 1, "hospital": 3, "private_center": 1},
    "Нефролог":        {"hospital": 2, "polyclinic_adult": 1},
    "Гематолог":       {"hospital": 2, "polyclinic_adult": 1},
    "Инфекционист":    {"polyclinic_adult": 1, "polyclinic_child": 1, "hospital": 2},
    "Колопроктолог":   {"hospital": 1, "polyclinic_adult": 1, "private_center": 1},
    "Сосудистый хирург": {"hospital": 2, "polyclinic_adult": 1, "private_center": 1},
    "Нейрохирург":     {"hospital": 3, "polyclinic_adult": 1},
}


# Профиль частного центра по названию: узкий центр подходит только своей специальности
PROFILE_KEYWORDS = {
    "Кардиолог": ["кардио", "сердц"],
    "Невролог": ["невро", "неврол"],
    "Эндокринолог": ["эндокрин", "диабет"],
    "ЛОР": ["лор", "отоларинг", "ухо"],
    "Гастроэнтеролог": ["гастро"],
    "Гинеколог": ["гинек", "женск", "мать и дитя", "репродук", "эко ", "эко-", "маммол"],
    "Уролог": ["урол", "андрол"],
    "Офтальмолог": ["глаз", "офтальм", "зрени", "микрохирургия глаза"],
    "Педиатр": ["детск", "педиатр", "мать и дитя"],
    "Дерматолог": ["дерма", "кожн", "косметол"],
    "Пульмонолог": ["пульмон"],
    "Аллерголог-иммунолог": ["аллерг", "иммунол"],
    "Ревматолог": ["ревмат"],
    "Онколог": ["онколог"],
    "Травматолог-ортопед": ["ортопед", "сустав", "позвоноч"],
    "Нефролог": ["нефрол"],
    "Гематолог": ["гематол"],
    "Колопроктолог": ["проктол"],
    "Сосудистый хирург": ["флебол", "сосудист", "варикоз"],
}


def _profile_specs(name: str) -> set[str]:
    n = name.lower()
    return {s for s, kws in PROFILE_KEYWORDS.items() if any(k in n for k in kws)}


def base_weight(facility: dict, spec: str) -> int:
    """Вес учреждения для специальности по его типу и профилю (без статистики и правок)."""
    from .facilities import excluded_name  # правила отбора могли обновиться после загрузки города
    low = facility["name"].lower()
    if spec == "Онколог" and "онколог" in low:
        return 6
    if excluded_name(low):
        return 0
    n = DOCTORS_PER_CATEGORY.get(spec, {}).get(facility["category"], 0)
    if facility["category"] == "private_center":
        prof = _profile_specs(facility["name"])
        if prof and spec not in prof:
            n = 0       # узкий центр другого профиля
        elif spec in prof:
            n = max(n, 2)
    return n


def doctors_in(facility: dict, spec: str, overrides: dict | None = None, calib: dict | None = None) -> list[str]:
    """Список врачей специальности в учреждении (имена, если известны).

    calib — результат doctor_stats.calibration: число врачей по статистике для учреждений города
    и коэффициент для учреждений, которых нет в карте (пригороды).
    known_doctors — сколько врачей специальности точно работает в учреждении (по данным визитов):
    оценка не опускается ниже этого числа.
    """
    if overrides and facility["id"] in overrides:
        n = int(overrides[facility["id"]])
        return [f"{spec} {i + 1}" for i in range(max(0, n))]
    named = [d for d in facility.get("doctors", []) if d.get("spec", "").lower().startswith(spec.lower()[:5])]
    if named:
        return [d["name"] or f"{spec} {i + 1}" for i, d in enumerate(named)]
    if facility["source"] == "import" and facility.get("doctors"):
        return []  # у импортированного учреждения указаны врачи других специальностей
    if calib and calib.get("counts") is not None:
        if facility["id"] in calib["counts"]:
            n = calib["counts"][facility["id"]]
        else:
            n = int(round(base_weight(facility, spec) * calib.get("scale", 1.0)))
    else:
        n = base_weight(facility, spec)
    n = max(n, int((facility.get("known_doctors") or {}).get(spec, 0)))
    return [f"{spec} {i + 1}" for i in range(n)]


def defaults() -> dict:
    return {
        "specialties": SPECIALTIES,
        "wait_median": WAIT_MEDIAN,
        "doctor_visit_min": DOCTOR_VISIT_MIN,
        "pharmacy_visit_min": PHARMACY_VISIT_MIN,
        "pharmacy_wait_min": PHARMACY_WAIT_MIN,
        "lpu_entry_min": LPU_ENTRY_MIN,
        "lunch_min": LUNCH_MIN,
        "crm_min": CRM_END_OF_DAY_MIN,
        "workday_limit_h": WORKDAY_LIMIT_H,
        "day_start": DAY_START,
        "transport": TRANSPORT,
        "doctors_per_category": DOCTORS_PER_CATEGORY,
    }
