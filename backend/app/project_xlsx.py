"""Выгрузка проекта в Excel — внутренний отчёт: условия, города, медпреды, месяцы, расписание, базы."""
from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .export_xlsx import monthly_rows
from .calendar_ru import FREQUENCY_LABELS
from .facilities import CATEGORY_LABELS
from .coverage import specialties
from .projects import load_plan

HEAD_FILL = PatternFill("solid", fgColor="1E2A35")
HEAD_FONT = Font(bold=True, color="FFFFFF")
BAD_FILL = PatternFill("solid", fgColor="F8DEDA")
WARN_FILL = PatternFill("solid", fgColor="FBEFD5")
BOLD = Font(bold=True)
MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
STATUS = {"ok": "в норме", "warn": "есть замечания", "bad": "условия не выполнимы"}
OWN = {"state": "гос./муниц.", "private": "частная"}


def _sheet(wb, title, headers, widths):
    ws = wb.create_sheet(title)
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    ws.freeze_panes = "A2"
    row = []
    for h in headers:
        c = WriteOnlyCell(ws, value=h)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(wrap_text=True, vertical="center")
        row.append(c)
    ws.append(row)
    return ws


def _cells(ws, values, fill=None, bold=False, fmt=None):
    out = []
    for v in values:
        c = WriteOnlyCell(ws, value=v)
        if fill:
            c.fill = fill
        if bold:
            c.font = BOLD
        if fmt and isinstance(v, date):
            c.number_format = fmt
        out.append(c)
    ws.append(out)


def build(pr: dict) -> bytes:
    res = pr["result"]
    cond = pr["conditions"]
    t = res["totals"]
    mixed = res.get("project_mix", False)
    wb = Workbook(write_only=True)

    # ── Проект ───────────────────────────────────────────────────────────────
    ws = wb.create_sheet("Проект")
    ws.column_dimensions["A"].width = 48
    ws.column_dimensions["B"].width = 40
    goal = pr.get("goal") or {}
    goal_txt = {"all": "все врачи выбранных специальностей в базах городов" if mixed else "все врачи специальности в базах городов",
                "total": f"{goal.get('total_doctors') or 0} врачей суммарно",
                "per_city": "своя цель по каждому городу",
                "total_min": f"{goal.get('total_doctors') or 0} врачей суммарно", "none": "без цели по охвату врачей"}.get(goal.get("mode", "all"), "")
    visit_settings = [(x["specialty"], f"{x['doctor_visits_per_day']} / {x['doctor_frequency']}")
                      for x in specialties(cond)]
    rows = [
        ("Проект", pr["name"]), ("Заказчик", pr.get("client", "")), ("Расчёт от", res["computed"]), ("", ""),
        ("Условия", ""),
        ("Специальности врачей", ", ".join(x[0] for x in visit_settings)),
        ("Начало проекта", cond["start_date"]), ("Срок, месяцев", cond["months"]),
        *[(f"{name} · в день / кратность", rate) for name, rate in visit_settings],
        ("Визитов в аптеки в день / кратность", f"{cond['pharmacy_visits_per_day']} / {cond['pharmacy_frequency']}"),
        ("Цель", goal_txt), ("Городов", len(res["cities"])), ("", ""),
        ("Итоги", ""),
        ("Медпредов в плане", t["reps"]),
        ("Оценка команды при заданных визитах в день", t["reps_needed"]),
        ("Цель, врачей", t["target"]),
        ("Врачей в базах городов · всего" if mixed else f"Врачей в базах · {cond['specialty']}", t["available_doctors"]),
        ("Врачей охвачено планом", t["covered_doctors"]),
        ("Выполнение цели, %", t["coverage_pct"]),
        ("Визитов к врачам: по плану / в маршрутах", f"{t['planned_doctor_visits']} / {t['doctor_visits']}"),
        ("Визитов в аптеки: по плану / в маршрутах", f"{t['planned_pharmacy_visits']} / {t['pharmacy_visits']}"),
        ("Уникальных ЛПУ / аптек", f"{t['unique_lpu']} / {t['unique_pharmacies']}"),
        ("Правило кратности", FREQUENCY_LABELS.get(cond.get("frequency_unit"), "В среднем за месяц")),
        ("Человеко-дней сверх нормы по длительности или P80", t.get("over_limit_days", 0)),
        ("Средний день, ч / среднее P80, ч", f"{t['avg_hours']} / {t['avg_p80_hours']}"),
        ("Пробег за проект, км", t["total_km"]),
        ("Городов с невыполнимыми условиями / с замечаниями", f"{t['cities_bad']} / {t['cities_warn']}"),
    ]
    for a, b in rows:
        _cells(ws, [a, b], bold=a in ("Условия", "Итоги"))
    if t.get("unallocated_target"):
        _cells(ws, ["Не распределено из общей цели", t["unallocated_target"]])

    # ── Города ───────────────────────────────────────────────────────────────
    hc = _sheet(wb, "Города", [
        "Город", "Медпредов", "Подбор", "Оценка команды", "Цель, врачей",
        "Врачей в базе · всего" if mixed else f"Врачей в базе · {cond['specialty']}", "Охвачено",
        "Выполнение, %", "Визитов к врачам (план)", "Визитов к врачам (в маршрутах)", "Визитов в аптеки (план)",
        "Визитов в аптеки (в маршрутах)", "ЛПУ", "Аптек", "Средний день, ч", "День 80%, ч",
        "Дней с риском", "Макс. визитов к врачам в день", "Статус", "Что не так", "Что изменить"],
        [20, 9, 9, 10, 9, 9, 9, 10, 11, 11, 11, 11, 7, 7, 9, 9, 8, 11, 18, 60, 60])
    for c in res["cities"]:
        fill = BAD_FILL if c["status"] == "bad" else WARN_FILL if c["status"] == "warn" else None
        _cells(hc, [c["city"], c["reps"], "авто" if c["reps_auto"] else "вручную", c["reps_needed"], c["target"],
                    c["available_doctors"], c["covered_doctors"], c["coverage_pct"], c["planned_doctor_visits"],
                    c["doctor_visits"], c["planned_pharmacy_visits"], c["pharmacy_visits"], c["unique_lpu"],
                    c["unique_pharmacies"], c["avg_hours"], c["avg_p80_hours"], c["risky_days"],
                    c["max_doctor_visits"], STATUS[c["status"]],
                    " ".join(i["text"] for i in c["issues"]), " ".join(c["fixes"])], fill=fill)

    if mixed:
        hs = _sheet(wb, "По специальностям", ["Город", "Специальность", "Врачей в базе", "Цель", "Охвачено",
                    "Визитов в день на МП", "Визитов на врача за проект", "Визитов по цели", "Визитов в маршрутах"],
                    [23, 27, 15, 11, 13, 16, 16, 16, 16])
        for c in res["cities"]:
            for row in c["specialties"]:
                _cells(hs, [c["city"], row["specialty"], row["available"], row.get("target"), row["unique"],
                            row["per_day"], row["frequency"], row["planned"], row["visits"]],
                       fill=WARN_FILL if row["visits"] < row["planned"] else None)

    # ── Медпреды под разное число ────────────────────────────────────────────
    ha = _sheet(wb, "Медпреды и охват", ["Медпредов всего", "Врачей охвачено", "Визитов к врачам"] +
                [c["city"] for c in res["cities"]], [12, 12, 12] + [12] * len(res["cities"]))
    for a in res["allocation"]:
        cur = a["reps"] == t["reps"]
        _cells(ha, [a["reps"], a["covered"], a["visits"]] + [a["split"].get(c["slug"], "") for c in res["cities"]],
               bold=cur)

    plans = {c["slug"]: load_plan(pr["id"], c["slug"], res.get("id")) for c in res["cities"]}

    # ── По месяцам ───────────────────────────────────────────────────────────
    hm = _sheet(wb, "По месяцам", ["Город", "Месяц / даты периода", "Медпредов", "Рабочих дней", "Визитов к врачам",
                                   "Визитов в аптеки", "Средний день, ч"], [20, 32, 10, 14, 18, 18, 17])
    for c in res["cities"]:
        if plan := plans[c["slug"]]:
            for label, *metrics in monthly_rows(plan):
                _cells(hm, [c["city"], label, c["reps"], *metrics])

    # ── Расписание ───────────────────────────────────────────────────────────
    hs = _sheet(wb, "План по дням", ["Город", "Дата", "День", "МП", "№", "Прибытие", "Начало визита",
                                     "Окончание", "Тип", "Специальность", "Врач", "Визит №", "Учреждение", "Адрес", "Км"],
                [16, 11, 5, 5, 5, 9, 9, 9, 8, 15, 22, 8, 40, 36, 6])
    for c in res["cities"]:
        plan = plans[c["slug"]]
        if not plan:
            continue
        for day in plan["days"]:
            dt = date.fromisoformat(day["date"])
            for st in day["stops"]:
                base = [c["city"], dt, day["weekday"], day["rep"], st["seq"], st["arrive"]]
                tail = [st["name"], st["address"], st["km"]]
                if st["type"] == "lpu":
                    for doc in st["doctors"]:
                        _cells(hs, base + [doc["start"], doc["end"], "Врач", doc.get("spec", cond["specialty"]),
                                           doc["name"], doc["visit_no"]] + tail,
                               fmt="DD.MM.YYYY")
                else:
                    _cells(hs, base + [st["arrive"], st["leave"], "Аптека", "", "", st.get("visit_no", 1)] + tail,
                           fmt="DD.MM.YYYY")

    # ── Базы: учреждения в плане ─────────────────────────────────────────────
    for kind, title in (("lpu", "ЛПУ"), ("pharmacy", "Аптеки")):
        sh = _sheet(wb, title, ["Город", "Учреждение", "Тип", "Собственность", "Сеть", "Адрес", "МП",
                                "Врачей в плане" if kind == "lpu" else "—", "Визитов", "Широта", "Долгота"],
                    [16, 40, 24, 13, 18, 36, 6, 9, 8, 10, 10])
        for c in res["cities"]:
            plan = plans[c["slug"]]
            if not plan:
                continue
            for f in sorted((x for x in plan["facilities"] if x["kind"] == kind), key=lambda x: x["name"]):
                n_vis = sum(max(1, len(v["doctors"])) for v in f["visits"])
                reps = ", ".join(str(r) for r in sorted({v["rep"] for v in f["visits"]}))
                _cells(sh, [c["city"], f["name"], CATEGORY_LABELS.get(f["category"], f["category"]),
                            OWN.get(f.get("ownership", ""), ""), f.get("network", ""), f["address"], reps,
                            f["doctors"] if kind == "lpu" else "", n_vis, f["lat"], f["lon"]])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
