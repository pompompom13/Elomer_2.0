"""Производственный календарь РФ (пятидневка)."""
from datetime import date, timedelta

# Нерабочие праздничные дни с учётом переносов (постановления Правительства РФ)
_HOLIDAYS = {
    2025: ["01-01", "01-02", "01-03", "01-04", "01-05", "01-06", "01-07", "01-08",
           "05-01", "05-02", "05-08", "05-09", "06-12", "06-13", "11-03", "11-04", "12-31"],
    2026: ["01-01", "01-02", "01-03", "01-04", "01-05", "01-06", "01-07", "01-08", "01-09",
           "02-23", "03-09", "05-01", "05-11", "06-12", "11-04", "12-31"],
}
# Рабочие субботы (перенос)
_WORKING_SATURDAYS = {date(2025, 11, 1)}

# Для лет без утверждённого календаря — праздники по ТК РФ с переносом с выходного на понедельник
_STATUTORY = ["02-23", "03-08", "05-01", "05-09", "06-12", "11-04"]


def _holidays(year: int) -> set[date]:
    if year in _HOLIDAYS:
        return {date(year, int(m), int(d)) for m, d in (x.split("-") for x in _HOLIDAYS[year])}
    res = {date(year, 1, d) for d in range(1, 9)}
    for s in _STATUTORY:
        d = date(year, *map(int, s.split("-")))
        res.add(d)
        if d.weekday() >= 5:
            shift = d + timedelta(days=7 - d.weekday())
            while shift in res:
                shift += timedelta(days=1)
            res.add(shift)
    return res


def is_working(d: date) -> bool:
    if d in _WORKING_SATURDAYS:
        return True
    if d.weekday() >= 5:
        return False
    return d not in _holidays(d.year)


def working_days(start: date, end: date, extra_off: set[date] | None = None) -> list[date]:
    out, d = [], start
    extra_off = extra_off or set()
    while d <= end:
        if is_working(d) and d not in extra_off:
            out.append(d)
        d += timedelta(days=1)
    return out


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def project_months(start: date, end: date) -> list[tuple[date, date]]:
    """Months anchored to the original start, with inclusive end dates."""
    periods = []
    i, left = 1, start
    while left <= end:
        next_start = add_months(start, i)
        periods.append((left, min(end, next_start - timedelta(days=1))))
        i, left = i + 1, next_start
    return periods


def frequency_unit(value: str | None) -> str:
    """Map historical monthly options to the current rule for new calculations."""
    return "project" if value == "project" else "project_month"


FREQUENCY_LABELS = {
    "project_month": "За каждый месяц от даты старта",
    "calendar_month": "В каждом календарном месяце",
    "month": "В среднем за месяц",
    "project": "Всего за проект",
}


def calendar_is_official(year: int) -> bool:
    return year in _HOLIDAYS
