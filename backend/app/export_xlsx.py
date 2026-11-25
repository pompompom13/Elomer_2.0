"""Выгрузка плана проекта в Excel."""
from __future__ import annotations

from bisect import bisect_right
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .calendar_ru import FREQUENCY_LABELS
from .facilities import CATEGORY_LABELS

HEAD_FILL = PatternFill("solid", fgColor="1E2A35")
HEAD_FONT = Font(bold=True, color="FFFFFF")
DOC_FILL = PatternFill("solid", fgColor="E6EAF7")
PH_FILL = PatternFill("solid", fgColor="E3F1EA")
RISK_FILL = PatternFill("solid", fgColor="F8DEDA")
THIN = Side(style="thin", color="C9D1D3")


def _sheet(wb, title, headers, widths):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(row=1, column=i)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"
    return ws


def _d(s: str) -> date:
    return date.fromisoformat(s)


def monthly_rows(plan):
    """Project-month statistics from the fixed calculation, or calendar statistics for older results."""
    periods = plan["summary"].get("month_windows") or []
    starts = [period["start"] for period in periods]
    buckets = {start: [set(), 0, 0, 0.0, 0] for start in starts}
    for day in plan["days"]:
        day_date = day["date"]
        key = starts[bisect_right(starts, day_date) - 1] if starts else day_date[:7]
        bucket = buckets.setdefault(key, [set(), 0, 0, 0.0, 0])
        bucket[0].add(day_date)
        totals = day["totals"]
        bucket[1] += totals["doctor_visits"]
        bucket[2] += totals["pharmacy_visits"]
        if day["stops"]:
            bucket[3] += totals["hours"]
            bucket[4] += 1
    for index, key in enumerate(sorted(buckets)):
        dates, doctors, pharmacies, hours, routes = buckets[key]
        if periods:
            period = periods[index]
            label = f"{index + 1}. {_d(period['start']):%d.%m.%Y}–{_d(period['end']):%d.%m.%Y}"
        else:
            label = key
        yield [label, len(dates), doctors, pharmacies, round(hours / routes, 2) if routes else 0]


def build_workbook(plan: dict) -> bytes:
    s = plan["summary"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Итоги"
    ws.column_dimensions["A"].width = 46
    ws.column_dimensions["B"].width = 28
    rows = [
        ("Город", s["city"]),
        ("Специальность врачей", s["specialty"]),
        ("Медпредов", s["reps"]),
        ("Период", f"{_d(s['start']).strftime('%d.%m.%Y')} – {_d(s['end']).strftime('%d.%m.%Y')}"),
        ("Рабочих дней", s["working_days"]),
        ("Транспорт", s["transport"]),
        *[(f"{x['specialty']}: в день / на врача за срок", f"{x['per_day']} / {x['frequency']}")
          for x in s.get("specialties", [])],
        ("Аптеки: в день / на аптеку за срок",
         f"{s['pharmacy_visits_per_day']} / {s['pharmacy_frequency']}"),
        ("", ""),
        ("Визитов к врачам: план / в маршрутах", f"{s['planned_doctor_visits']} / {s['doctor_visits']}"),
        ("Визитов в аптеки: план / в маршрутах", f"{s['planned_pharmacy_visits']} / {s['pharmacy_visits']}"),
        ("Уникальных врачей", s["unique_doctors"]),
        *[(f"  {x['specialty']}: визитов план / в маршрутах, врачей", f"{x['planned']} / {x['visits']}, {x['unique']}")
          for x in (s.get("specialties") or []) if len(s.get("specialties") or []) > 1],
        ("Уникальных ЛПУ", s["unique_lpu"]),
        ("Уникальных аптек", s["unique_pharmacies"]),
        ("Средний интервал между визитами к врачу, дней", s["repeat_gap_days"] or "—"),
        ("Средняя длительность дня, ч", s["avg_hours"]),
        ("Среднее P80 по дням, ч", s["avg_p80_hours"]),
        ("Правило кратности", FREQUENCY_LABELS.get(s.get("frequency_unit"), "В среднем за месяц")),
        ("Человеко-дней сверх нормы по длительности или P80", s.get("over_limit_days", 0)),
        ("Дней с риском переработки ≥ 20%", s["risky_days"]),
        ("Средний пробег в день, км", s["avg_km"]),
        ("Пробег за проект, км", s["total_km"]),
        ("Доля времени в контакте с врачом/аптекой, %", s["contact_share"]),
    ]
    for r in rows:
        ws.append(r)
    for c in ws["A"]:
        c.font = Font(bold=bool(c.value))
    if plan["warnings"]:
        ws.append(("", ""))
        ws.append(("Замечания к плану", ""))
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for w in plan["warnings"]:
            ws.append((w["text"],))
            ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=2)
            ws.cell(row=ws.max_row, column=1).alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[ws.max_row].height = 15 * (1 + len(w["text"]) // 80)

    # ── План по дням: одна строка = один визит ──────────────────────────────
    hp = _sheet(wb, "План по дням",
                ["Дата", "День", "МП", "№ в маршруте", "Прибытие", "Начало визита", "Окончание",
                 "Тип", "Специальность", "Врач", "Визит к врачу №", "Учреждение", "Адрес", "Переезд, мин", "Км",
                 "Широта", "Долгота"],
                [11, 5, 5, 8, 9, 9, 9, 9, 15, 22, 9, 40, 34, 9, 7, 10, 10])
    row_number = 1
    for day in plan["days"]:
        for st in day["stops"]:
            base = [_d(day["date"]), day["weekday"], day["rep"], st["seq"], st["arrive"]]
            tail = [st["name"], st["address"], st["travel_min"], st["km"], st["lat"], st["lon"]]
            if st["type"] == "lpu":
                for i, doc in enumerate(st["doctors"]):
                    hp.append(base + [doc["start"], doc["end"], "Врач", doc.get("spec", s["specialty"]), doc["name"],
                                      doc["visit_no"]] + tail)
                    row_number += 1
                    _fill_row(hp, DOC_FILL, row_number, 17)
            else:
                hp.append(base + [st["arrive"], st["leave"], "Аптека", "", "", st.get("visit_no", 1)] + tail)
                row_number += 1
                _fill_row(hp, PH_FILL, row_number, 17)
    _date_col(hp)
    hp.auto_filter.ref = hp.dimensions

    if s.get("month_windows"):
        sheet = _sheet(wb, "По месяцам", ["Месяц от даты старта", "Рабочих дней", "Визитов к врачам",
                        "Визитов в аптеки", "Средний день, ч"], [30, 15, 18, 18, 17])
        for row in monthly_rows(plan):
            sheet.append(row)

    # ── Сводка по дням ───────────────────────────────────────────────────────
    sd = _sheet(wb, "Сводка по дням",
                ["Дата", "День", "МП", "Тип дня", "Врачей", "Аптек", "ЛПУ", "Начало", "Окончание",
                 "Часов", "Часов (80%)", "Риск переработки", "Км", "В пути, мин", "Ожидание, мин"],
                [11, 5, 5, 12, 8, 8, 6, 8, 9, 8, 10, 11, 7, 9, 10])
    kinds = {"work": "рабочий", "reserve": "резерв", "no_doctors": "не хватило врачей", "pharmacy_only": "только аптеки"}
    for row_number, day in enumerate(plan["days"], 2):
        t = day["totals"]
        sd.append([_d(day["date"]), day["weekday"], day["rep"], kinds.get(day["kind"], day["kind"]),
                   t["doctor_visits"], t["pharmacy_visits"], t["lpu"], t["start"], t["end"],
                   t["hours"], t["p80_hours"], t["overtime_risk"], t["km"], t["travel_min"], t["wait_min"]])
        sd.cell(row=row_number, column=12).number_format = "0%"
        if t["overtime_risk"] >= 0.2:
            _fill_row(sd, RISK_FILL, row_number, 15)
    _date_col(sd)
    sd.auto_filter.ref = sd.dimensions

    # ── Территории ──────────────────────────────────────────────────────────
    if len(plan["reps"]) > 1:
        tr = _sheet(wb, "Территории", ["МП", "Врачей в территории", "Аптек в территории", "Врачей в плане",
                                       "ЛПУ в плане", "Аптек в плане", "Средний день, ч", "Средний пробег, км",
                                       "Точка старта (широта)", "Точка старта (долгота)"],
                    [5, 12, 12, 10, 10, 10, 10, 12, 14, 14])
        for r in plan["reps"]:
            dd = [d for d in plan["days"] if d["rep"] == r["id"] and d["stops"]]
            avg = lambda k: round(sum(d["totals"][k] for d in dd) / len(dd), 1) if dd else 0
            tr.append([r["id"], r["territory_doctors"], r["territory_pharmacies"], r["doctors"], r["lpu"],
                       r["pharmacies"], avg("hours"), avg("km"), round(r["home"][0], 5), round(r["home"][1], 5)])

    # ── Учреждения ──────────────────────────────────────────────────────────
    for kind, title in (("lpu", "ЛПУ"), ("pharmacy", "Аптеки")):
        sh = _sheet(wb, title, ["Учреждение", "Тип", "Собственность", "Сеть", "Адрес", "МП",
                                "Врачей в плане" if kind == "lpu" else "—",
                                "Визитов", "Даты визитов", "Широта", "Долгота"],
                    [40, 26, 14, 20, 34, 6, 10, 8, 60, 10, 10])
        for f in sorted((x for x in plan["facilities"] if x["kind"] == kind), key=lambda x: x["name"]):
            dates = sorted({v["date"] for v in f["visits"]})
            n_vis = sum(max(1, len(v["doctors"])) for v in f["visits"])
            own = {"state": "гос./муниц.", "private": "частная"}.get(f.get("ownership", ""), "")
            reps_ = ", ".join(str(r) for r in sorted({v["rep"] for v in f["visits"]}))
            sh.append([f["name"], CATEGORY_LABELS.get(f["category"], f["category"]), own, f.get("network", ""),
                       f["address"], reps_, f["doctors"] if kind == "lpu" else "", n_vis,
                       ", ".join(_d(x).strftime("%d.%m") for x in dates), f["lat"], f["lon"]])
        sh.auto_filter.ref = sh.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _fill_row(ws, fill, row=None, columns=None):
    row = ws.max_row if row is None else row
    cells = next(ws.iter_rows(min_row=row, max_row=row, max_col=columns))
    for c in cells:
        c.fill = fill
        c.border = Border(bottom=THIN)


def _date_col(ws):
    for c in ws["A"][1:]:
        c.number_format = "DD.MM.YYYY"


def route_sheets(plan: dict) -> bytes:
    """Маршрутные листы: лист на медпреда, внутри дни по неделям, порядок объезда, время и адреса."""
    s = plan["summary"]
    wb = Workbook()
    wb.remove(wb.active)
    week_fill = PatternFill("solid", fgColor="D9DEE0")
    for r in plan["reps"]:
        days = [d for d in plan["days"] if d["rep"] == r["id"] and d["stops"]]
        ws = _sheet(wb, f"МП {r['id']}", ["Дата", "День", "№", "Время", "Учреждение", "Адрес", "Кого посетить",
                                          "Переезд, мин", "Отметка о визите"],
                    [11, 5, 4, 12, 40, 40, 34, 9, 18])
        week = None
        for d in days:
            dt = _d(d["date"])
            wk = dt.isocalendar()[1]
            if wk != week:
                week = wk
                mon = dt.fromordinal(dt.toordinal() - dt.weekday())
                ws.append([f"Неделя с {mon.strftime('%d.%m.%Y')}"])
                ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=9)
                ws.cell(ws.max_row, 1).font = Font(bold=True)
                _fill_row(ws, week_fill)
            for st in d["stops"]:
                if st["type"] == "lpu":
                    who = ", ".join(f"{x.get('spec', s['specialty'])} ({x['start']})" for x in st["doctors"])
                    tm = f"{st['doctors'][0]['start']}–{st['doctors'][-1]['end']}" if st["doctors"] else st["arrive"]
                else:
                    who, tm = "Аптека", f"{st['arrive']}–{st['leave']}"
                ws.append([dt, d["weekday"], st["seq"], tm, st["name"], st["address"], who, st["travel_min"], ""])
                ws.cell(ws.max_row, 1).number_format = "DD.MM.YYYY"
                for c in ws[ws.max_row]:
                    c.alignment = Alignment(vertical="top", wrap_text=True)
                    c.border = Border(bottom=THIN)
            t = d["totals"]
            ws.append(["", "", "", f"{t['start']}–{t['end']}", f"Итого за день: врачей {t['doctor_visits']}, аптек {t['pharmacy_visits']}, {t['km']} км"])
            ws.cell(ws.max_row, 5).font = Font(italic=True, color="5B6770")
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToHeight = 0
    if not wb.sheetnames:
        wb.create_sheet("Нет визитов")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
