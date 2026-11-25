"""
PDF-отчёты для заказчика: проект из нескольких городов и план одного города.

Вёрстка — reportlab (platypus): текст, таблицы и собственные рисунки — карта зон
и графики. Шрифт Golos Text (SIL OFL) лежит в app/fonts.
"""
from __future__ import annotations

import io
import math
import re
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Flowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from .calendar_ru import FREQUENCY_LABELS
from .advisor import _pl

FONT_DIR = Path(__file__).parent / "fonts"
INK = colors.HexColor("#1E2A35")
INK2 = colors.HexColor("#4A5864")
INK3 = colors.HexColor("#7C8890")
LINE = colors.HexColor("#C9D1D3")
BG = colors.HexColor("#F1F4F3")
DOC = colors.HexColor("#2743B5")
PH = colors.HexColor("#1F7A52")
RISK = colors.HexColor("#B8321F")
AMBER = colors.HexColor("#9A6400")
STATUS_COLOR = {"ok": PH, "warn": AMBER, "bad": RISK}
PARAM_STATUS = {"ok": "в норме", "warn": "можно лучше", "bad": "не оптимально"}
STATUS_TEXT = {"ok": "в норме", "warn": "есть замечания", "bad": "не выполнимо"}
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]

_registered = False


def _fonts():
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont("Golos", str(FONT_DIR / "GolosText-Regular.ttf")))
        pdfmetrics.registerFont(TTFont("Golos-SB", str(FONT_DIR / "GolosText-SemiBold.ttf")))
        pdfmetrics.registerFontFamily("Golos", normal="Golos", bold="Golos-SB", italic="Golos", boldItalic="Golos-SB")
        _registered = True


def _styles():
    base = dict(fontName="Golos", textColor=INK, alignment=TA_LEFT)
    return {
        "kicker": ParagraphStyle("kicker", **{**base, "textColor": INK3}, fontSize=8.5, leading=11),
        "h1": ParagraphStyle("h1", **{**base, "fontName": "Golos-SB"}, fontSize=19, leading=23, spaceAfter=2),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Golos-SB"}, fontSize=12.5, leading=16,
                             spaceBefore=10, spaceAfter=5),
        "h3": ParagraphStyle("h3", **{**base, "fontName": "Golos-SB"}, fontSize=10.5, leading=13, spaceAfter=2),
        "body": ParagraphStyle("body", **base, fontSize=9.5, leading=13),
        "small": ParagraphStyle("small", **{**base, "textColor": INK2}, fontSize=8.5, leading=11.5),
        "muted": ParagraphStyle("muted", **{**base, "textColor": INK3}, fontSize=7.5, leading=10),
        "cell": ParagraphStyle("cell", **base, fontSize=8.5, leading=11),
        "fig_v": ParagraphStyle("fig_v", **{**base, "fontName": "Golos-SB"}, fontSize=17, leading=20),
        "fig_l": ParagraphStyle("fig_l", **{**base, "textColor": INK2}, fontSize=8, leading=10.5),
    }


def _n(v, digits=0) -> str:
    if v is None:
        return "—"
    s = f"{v:,.{digits}f}".replace(",", " ").replace(".", ",")
    return s


def _date(s: str) -> str:
    d = date.fromisoformat(s)
    return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}"


def spec_plural(spec: str) -> str:
    """Кардиолог → кардиологи, Педиатр → педиатры, Аллерголог-иммунолог → аллергологи-иммунологи."""
    def one(w):
        return w + ("и" if w[-1] in "гкхжшчщ" else "ы")
    return "-".join(one(w) for w in spec.split("-")).lower().replace("лоры", "ЛОР-врачи")


def _txt(s: str) -> str:
    """Текст расчёта для заказчика: десятичная запятая вместо точки."""
    return _esc(re.sub(r"(\d)\.(\d)", r"\1,\2", str(s)))


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─── Рисунки ─────────────────────────────────────────────────────────────────

class ZoneMap(Flowable):
    """Зоны медпредов и учреждения в плане — без подложки, в равнопромежуточной проекции."""

    def __init__(self, zones, points, width, height, labels=True):
        super().__init__()
        self.zones, self.points = zones, points
        self.width, self.height, self.labels = width, height, labels

    def draw(self):
        c = self.canv
        c.setStrokeColor(LINE)
        c.setFillColor(colors.white)
        c.rect(0, 0, self.width, self.height, fill=1, stroke=1)
        pts = [(p[0], p[1]) for z in self.zones for p in (z.get("polygon") or [])] + \
              [(p[0], p[1]) for p in self.points]
        if not pts:
            return
        lat0 = sum(p[0] for p in pts) / len(pts)
        k = math.cos(math.radians(lat0))
        xs = [p[1] * k for p in pts]
        ys = [p[0] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        pad = 6
        sc = min((self.width - 2 * pad) / ((x1 - x0) or 1e-6), (self.height - 2 * pad) / ((y1 - y0) or 1e-6))
        ox = (self.width - (x1 - x0) * sc) / 2
        oy = (self.height - (y1 - y0) * sc) / 2

        def tr(lat, lon):
            return ox + (lon * k - x0) * sc, oy + (lat - y0) * sc

        multi = len(self.zones) > 1
        for z in self.zones:
            poly = z.get("polygon") or []
            if len(poly) < 3:
                continue
            col = colors.HexColor(z["color"])
            path = c.beginPath()
            x, y = tr(*poly[0])
            path.moveTo(x, y)
            for p in poly[1:]:
                path.lineTo(*tr(*p))
            path.close()
            c.setFillColor(col, alpha=0.09 if multi else 0.05)
            c.setStrokeColor(col, alpha=0.9)
            c.setLineWidth(1.1)
            c.drawPath(path, fill=1, stroke=1)
        for lat, lon, is_lpu in self.points:
            x, y = tr(lat, lon)
            if is_lpu:
                c.setFillColor(DOC)
                c.circle(x, y, 1.5, fill=1, stroke=0)
            else:
                c.setFillColor(PH, alpha=0.75)
                c.rect(x - 0.9, y - 0.9, 1.8, 1.8, fill=1, stroke=0)
        if self.labels and multi:
            for z in self.zones:
                poly = z.get("polygon") or []
                if len(poly) < 3:
                    continue
                cx = sum(p[0] for p in poly) / len(poly)
                cy = sum(p[1] for p in poly) / len(poly)
                x, y = tr(cx, cy)
                c.setFillColor(colors.white)
                c.setStrokeColor(colors.HexColor(z["color"]))
                c.setLineWidth(1)
                c.circle(x, y, 6.5, fill=1, stroke=1)
                c.setFillColor(INK)
                c.setFont("Golos-SB", 7)
                c.drawCentredString(x, y - 2.4, str(z["id"]))


class LineChart(Flowable):
    """Одна или две линии по общей оси X, вертикальные отметки «сейчас» и «нужно»."""

    def __init__(self, xs, series, width, height, x_label, y_fmt=lambda v: _n(v), marks=None, hline=None,
                 bars=False):
        super().__init__()
        self.xs, self.series = xs, series     # series: [(values, color, label, dashed)]
        self.width, self.height = width, height
        self.x_label, self.y_fmt = x_label, y_fmt
        self.marks = marks or []              # [(x, color, label)]
        self.hline = hline                    # (y, label)
        self.bars = bars

    def draw(self):
        c = self.canv
        L, R, B, T = 34, 8, 26, 8
        w, h = self.width - L - R, self.height - B - T
        vals = [v for s in self.series for v in s[0] if v is not None]
        if self.hline:
            vals.append(self.hline[0] * 1.15)
        ymax = max(vals + [1]) * 1.06
        step = _nice(ymax / 4)
        n = len(self.xs)

        def X(i):
            return L + (i + 0.5) * w / n if self.bars else L + (i * w / max(1, n - 1))

        def Y(v):
            return B + v / ymax * h
        c.setFont("Golos", 7)
        v = 0.0
        while v <= ymax + 1e-9:
            c.setStrokeColor(LINE)
            c.setLineWidth(0.4)
            c.line(L, Y(v), L + w, Y(v))
            c.setFillColor(INK3)
            c.drawRightString(L - 4, Y(v) - 2.5, self.y_fmt(v))
            v += step
        every = max(1, math.ceil(n / 16))
        for i, x in enumerate(self.xs):
            if i % every == 0 or i == n - 1:
                c.setFillColor(INK3)
                c.drawCentredString(X(i), B - 10, str(x))
        c.setFillColor(INK2)
        c.drawCentredString(L + w / 2, 2, self.x_label)
        for x, col, label in self.marks:
            if x not in self.xs:
                continue
            i = self.xs.index(x)
            c.setStrokeColor(col)
            c.setLineWidth(0.9)
            c.setDash(2, 2)
            c.line(X(i), B, X(i), B + h)
            c.setDash()
            c.setFillColor(col)
            c.setFont("Golos-SB", 7)
            tx = X(i) + 3 if X(i) < L + w * 0.75 else X(i) - 3 - pdfmetrics.stringWidth(label, "Golos-SB", 7)
            c.drawString(tx, B + h - 8 - 9 * self.marks.index((x, col, label)), label)
        for values, col, label, dashed in self.series:
            if self.bars:
                bw = w / n * 0.56
                for i, v in enumerate(values):
                    if v is None:
                        continue
                    over = self.hline and v > self.hline[0]
                    c.setFillColor(RISK if over else col)
                    c.rect(X(i) - bw / 2, B, bw, Y(v) - B, fill=1, stroke=0)
                continue
            c.setStrokeColor(col)
            c.setLineWidth(1.6)
            if dashed:
                c.setDash(3, 2)
            p = c.beginPath()
            first = True
            for i, v in enumerate(values):
                if v is None:
                    continue
                (p.moveTo if first else p.lineTo)(X(i), Y(v))
                first = False
            c.drawPath(p, stroke=1, fill=0)
            c.setDash()
        if self.hline:
            yv, label = self.hline
            c.setStrokeColor(RISK)
            c.setLineWidth(0.9)
            c.line(L, Y(yv), L + w, Y(yv))
            c.setFillColor(RISK)
            c.setFont("Golos-SB", 7)
            c.drawString(L + 3, Y(yv) + 3, label)


def _nice(raw: float) -> float:
    p = 10 ** math.floor(math.log10(raw or 1))
    return next(k * p for k in (1, 2, 2.5, 5, 10) if k * p >= raw)


# ─── Общие блоки ─────────────────────────────────────────────────────────────

def _figures(st, items, width):
    """Ряд ключевых чисел: [(значение, подпись, цвет или None)]."""
    cells = [[Paragraph(f'<font color="{(col or INK).hexval().replace("0x", "#")}">{v}</font>', st["fig_v"]),
              Paragraph(l, st["fig_l"])] for v, l, col in items]
    t = Table([[c for c in cells]], colWidths=[width / len(items)] * len(items))
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LINEABOVE", (0, 0), (-1, 0), 1.2, INK),
    ]))
    return t


_HEAD = ParagraphStyle("head", fontName="Golos-SB", fontSize=8, leading=10, textColor=INK2)


def _grid(rows, widths, header=True, status_col=None):
    if header:
        rows = [[Paragraph(str(h), _HEAD) if isinstance(h, str) else h for h in rows[0]]] + rows[1:]
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Golos", 8.5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
    ]
    if header:
        style += [("FONT", (0, 0), (-1, 0), "Golos-SB", 8), ("TEXTCOLOR", (0, 0), (-1, 0), INK2),
                  ("LINEBELOW", (0, 0), (-1, 0), 1, INK)]
    t.setStyle(TableStyle(style))
    return t


def _status_cell(st, status):
    col = STATUS_COLOR[status].hexval().replace("0x", "#")
    return Paragraph(f'<font color="{col}">{STATUS_TEXT[status]}</font>', st["cell"])


def _footer(text):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Golos", 7)
        canvas.setFillColor(INK3)
        canvas.drawString(18 * mm, 10 * mm, text)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"стр. {doc.page}")
        canvas.restoreState()
    return draw


SOURCES = ("Источники: учреждения — OpenStreetMap (© участники OpenStreetMap, ODbL); число врачей — статистика "
           "по регионам и городам РФ; рабочие дни — производственный календарь РФ. Длительность дня — по "
           "нормативам визитов, ожидания и дороги с моделированием разброса (80% — день не длиннее указанного "
           "с вероятностью 80%).")


# ─── Проект ──────────────────────────────────────────────────────────────────

def project_pdf(pr: dict) -> bytes:
    _fonts()
    st = _styles()
    res, cond, t = pr["result"], pr["conditions"], pr["result"]["totals"]
    mixed = res.get("project_mix", False)
    W = A4[0] - 36 * mm
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title=pr["name"], author="ЭЛОМЕР")
    story = []
    d, f = cond["doctor_visits_per_day"], cond["doctor_frequency"]
    p, fp = cond["pharmacy_visits_per_day"], cond["pharmacy_frequency"]
    story.append(Paragraph(f"План полевого проекта · {_date(res['computed'][:10])}", st["kicker"]))
    story.append(Paragraph(_esc(pr["name"]), st["h1"]))
    if pr.get("client"):
        story.append(Paragraph(f"Заказчик: {_esc(pr['client'])}", st["body"]))
    story.append(Spacer(1, 4))
    goal = pr.get("goal") or {}
    goal_txt = {"all": "охватить всех врачей выбранных специальностей в городах проекта",
                "total": f"охватить {_n(goal.get('total_doctors') or 0)} врачей суммарно",
                "per_city": "своя цель по каждому городу",
                "total_min": f"охватить {_n(goal.get('total_doctors') or 0)} врачей суммарно",
                "none": "без цели по охвату — по заданным медпредам и визитам в день"}.get(goal.get("mode", "all"), "")
    specs = [cond["specialty"]] + [x["specialty"] for x in cond.get("extra_specialties") or []]
    cond_txt = (f"{' и '.join(spec_plural(x) for x in specs).capitalize()}, {len(res['cities'])} {_pl(len(res['cities']), 'город', 'города', 'городов')}, "
                f"{cond['months']} мес. с {_date(cond['start_date'])}. В день у медпреда {d} "
                f"{_pl(d, 'визит', 'визита', 'визитов')} к врачам и {p} в аптеки; каждого врача посещают "
                f"{f} {_pl(f, 'раз', 'раза', 'раз')}, аптеку — {fp}. Цель: {goal_txt}.")
    if mixed:
        settings = [{"specialty": cond["specialty"], "doctor_visits_per_day": d, "doctor_frequency": f},
                    *(cond.get("extra_specialties") or [])]
        cadence = FREQUENCY_LABELS.get(cond.get("frequency_unit"), "В среднем за месяц").lower()
        loads = ", ".join(f"{_esc(x['specialty'])} — {x['doctor_visits_per_day']} в день, "
                          f"кратность {x['doctor_frequency']} {cadence}" for x in settings)
        cond_txt = (f"{cond['months']} мес. с {_date(cond['start_date'])}. "
                    f"Визиты одного медпреда: {loads}. Аптеки — {p} в день, кратность {fp} {cadence}. Цель: {goal_txt}.")
    story.append(Paragraph(cond_txt, st["small"]))
    story.append(Spacer(1, 10))

    cov_col = RISK if t["coverage_pct"] < 90 else None
    story.append(_figures(st, [
        (f"{t['reps']}", "медпредов в маршрутах", None),
        (f"{_n(t['covered_doctors'])}", (f"врачей охвачено из {_n(t['target'])} ({t['coverage_pct']}% цели)" if t.get("has_goal") else "уникальных врачей в маршрутах"), cov_col),
        (f"{_n(t['doctor_visits'])}", "визитов к врачам за проект", None),
        (f"{_n(t['pharmacy_visits'])}", f"визитов в {_n(t['unique_pharmacies'])} аптек", None),
        (f"{_n(t['avg_hours'], 1)} ч", f"средний день, среднее P80 {_n(t['avg_p80_hours'], 1)} ч",
         RISK if t.get("over_limit_days") else None),
    ], W))

    # вывод
    bad = [c for c in res["cities"] if c["status"] == "bad"]
    warn = [c for c in res["cities"] if c["status"] == "warn"]
    story.append(Paragraph("Вывод", st["h2"]))
    if t.get("over_limit_days"):
        story.append(Paragraph(f"Сверх нормы {_n(cond['workday_limit_h'], 1)} ч по длительности или P80: "
                               f"{_n(t['over_limit_days'])} человеко-дней.", st["body"]))
    if t.get("unallocated_target"):
        story.append(Paragraph(f"Доступная база меньше общей цели. Не распределено {_n(t['unallocated_target'])} врачей.", st["body"]))
    if not bad and not warn and not t.get("unallocated_target"):
        story.append(Paragraph("Условия плана выполнены во всех городах.", st["body"]))
    elif bad or warn:
        parts = []
        if bad:
            parts.append(f"в {len(bad)} {_pl(len(bad), 'городе', 'городах', 'городах')} условия не выполнимы "
                         f"({', '.join(c['city'] for c in bad[:6])}{'…' if len(bad) > 6 else ''})")
        if warn:
            parts.append(f"в {len(warn)} {_pl(len(warn), 'городе', 'городах', 'городах')} есть замечания "
                         f"({', '.join(c['city'] for c in warn[:6])}{'…' if len(warn) > 6 else ''})")
        story.append(Paragraph("При заданных условиях " + "; ".join(parts) +
                               ". Подробности и что изменить — ниже.", st["body"]))
    if t["reps_needed"] != t["reps"] and not mixed:
        story.append(Paragraph(f"Чтобы выполнить цель при {d} {_pl(d, 'визите', 'визитах', 'визитах')} к врачам в день, "
                               f"нужно {t['reps_needed']} {_pl(t['reps_needed'], 'медпред', 'медпреда', 'медпредов')} "
                               f"(в плане {t['reps']}).", st["body"]))

    # график охвата
    alloc = res.get("allocation") or []
    if len(alloc) > 1:
        story.append(Paragraph("Сколько врачей охватит проект при разном числе медпредов", st["h2"]))
        xs = [a["reps"] for a in alloc]
        marks = [(t["reps"], INK, f"в плане: {t['reps']}")]
        if t["reps_needed"] != t["reps"] and t["reps_needed"] in xs:
            marks.append((t["reps_needed"], PH, f"нужно: {t['reps_needed']}"))
        story.append(LineChart(xs, [([a["covered"] for a in alloc], DOC, "охват", False)], W, 150,
                               "медпредов во всех городах", marks=marks,
                               hline=(t["target"], f"цель {_n(t['target'])}")))
        story.append(Paragraph("График показывает расчётную ёмкость команды по всем специальностям. Длительность дня проверяется по маршрутам."
                               if mixed else "Медпреды распределяются по городам так, чтобы каждый следующий давал больше всего "
                               "новых врачей. Где кривая выходит на цель, дополнительные медпреды не нужны.",
                               st["muted"]))

    # таблица городов
    story.append(Paragraph("Города", st["h2"]))
    rows = [["Город", "Медпредов", "Оценка команды" if mixed else "Нужно под цель", "Цель, врачей", "Охвачено", "Визитов к врачам", "Визитов в аптеки", "День, ч", "Статус"]]
    for c in res["cities"]:
        rows.append([Paragraph(_esc(c["city"]), st["cell"]), c["reps"], c["reps_needed"], _n(c["target"]),
                     f"{_n(c['covered_doctors'])} ({c['coverage_pct']}%)", _n(c["doctor_visits"]),
                     _n(c["pharmacy_visits"]), _n(c["avg_hours"], 1), _status_cell(st, c["status"])])
    rows.append(["Итого", t["reps"], t["reps_needed"], _n(t["target"]),
                 f"{_n(t['covered_doctors'])} ({t['coverage_pct']}%)", _n(t["doctor_visits"]),
                 _n(t["pharmacy_visits"]), _n(t["avg_hours"], 1), ""])
    g = _grid(rows, [W * x for x in (0.17, 0.06, 0.08, 0.08, 0.13, 0.12, 0.12, 0.08, 0.16)])
    g.setStyle(TableStyle([("FONT", (0, -1), (-1, -1), "Golos-SB", 8.5), ("LINEABOVE", (0, -1), (-1, -1), 1, INK),
                           ("ALIGN", (1, 1), (-2, -1), "RIGHT")]))
    story.append(g)

    if mixed:
        story.append(Paragraph("Охват по специальностям", st["h2"]))
        breakdown = [["Город", "Специальность", "В базе", "Цель", "Охвачено", "Визитов в маршрутах / план"]]
        for c in res["cities"]:
            for row in c["specialties"]:
                breakdown.append([Paragraph(_esc(c["city"]), st["cell"]),
                                  Paragraph(_esc(row["specialty"]), st["cell"]), _n(row["available"]),
                                  _n(row["target"]) if "target" in row else "—", _n(row["unique"]),
                                  f"{_n(row['visits'])} / {_n(row['planned'])}"])
        story.append(_grid(breakdown, [W * x for x in (.20, .23, .12, .11, .13, .21)]))

    # проблемы и решения
    problem = [c for c in res["cities"] if c["issues"]]
    if problem:
        story.append(Paragraph("Что не получается при заданных условиях и как это исправить", st["h2"]))
        for c in problem:
            block = [Paragraph(f'{_esc(c["city"])} <font color="{STATUS_COLOR[c["status"]].hexval().replace("0x", "#")}">'
                               f'· {STATUS_TEXT[c["status"]]}</font>', st["h3"])]
            for i in c["issues"]:
                block.append(Paragraph(_txt(i["text"]), st["body"]))
            for x in c["fixes"]:
                block.append(Paragraph(f"→ {_esc(x)}", st["small"]))
            block.append(Spacer(1, 6))
            story.append(KeepTogether(block))

    # карты городов
    story.append(Paragraph("Зоны медпредов по городам", st["h2"]))
    story.append(Paragraph("Цветные области — зоны медпредов (номер — медпред). Синие точки — ЛПУ в плане, "
                           "зелёные — аптеки.", st["muted"]))
    story.append(Spacer(1, 4))
    cw = (W - 8) / 2
    cards = []
    for c in res["cities"]:
        info = (f"<b>{_esc(c['city'])}</b><br/>{c['reps']} {_pl(c['reps'], 'медпред', 'медпреда', 'медпредов')}, "
                f"охват {_n(c['covered_doctors'])} из {_n(c['target'])} врачей, {_n(c['doctor_visits'])} визитов "
                f"к врачам и {_n(c['pharmacy_visits'])} в аптеки")
        cards.append([ZoneMap(c["zones"], c["points"], cw, cw * 0.7), Paragraph(info, st["small"])])
    pairs = []
    for i in range(0, len(cards), 2):
        a = cards[i]
        b = cards[i + 1] if i + 1 < len(cards) else ["", ""]
        pairs.append([a[0], b[0]])
        pairs.append([a[1], b[1]])
    if pairs:
        mt = Table(pairs, colWidths=[cw + 4, cw + 4])
        mt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story.append(mt)

    story.append(Spacer(1, 10))
    story.append(Paragraph(SOURCES, st["muted"]))
    foot = _footer(f"ЭЛОМЕР · {pr['name']}")
    doc.build(story, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue()


# ─── План одного города ──────────────────────────────────────────────────────

def plan_pdf(plan: dict, client: str = "") -> bytes:
    _fonts()
    st = _styles()
    s, adv = plan["summary"], plan.get("advice")
    W = A4[0] - 36 * mm
    buf = io.BytesIO()
    specialty_rows = s.get("specialties", [])
    specs = [row["specialty"] for row in specialty_rows]
    title = f"{s['city']}: {' и '.join(spec_plural(x) for x in specs) if specs else 'план визитов'}"
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title=title, author="ЭЛОМЕР")
    story = [Paragraph(f"План полевого проекта · {_date(date.today().isoformat())}", st["kicker"]),
             Paragraph(_esc(title), st["h1"])]
    if s.get("over_limit_days"):
        story.append(Paragraph(f"Сверх нормы {_n(s['workday_limit_h'], 1)} ч по длительности или P80: "
                               f"{_n(s['over_limit_days'])} человеко-дней.", st["body"]))
    if client:
        story.append(Paragraph(f"Заказчик: {_esc(client)}", st["body"]))
    d = s["doctor_visits_per_day"]
    story.append(Paragraph(
        f"{s['reps']} {_pl(s['reps'], 'медпред', 'медпреда', 'медпредов')}, {_date(s['start'])} — {_date(s['end'])} "
        f"({s['working_days']} рабочих {_pl(s['working_days'], 'день', 'дня', 'дней')}), {s['transport'].lower()}. "
        f"Дневная норма: {s.get('doctor_visits_per_day_total', d)} визитов к врачам, "
        f"{s['pharmacy_visits_per_day']} в аптеки. Повторов на аптеку за срок: {s['pharmacy_frequency']}.", st["small"]))
    cadence = FREQUENCY_LABELS.get(s.get("frequency_unit"), "В среднем за месяц").lower()
    story.append(Paragraph(f"Правило кратности: {cadence}.", st["small"]))
    story.append(Spacer(1, 10))
    short = s["doctor_visits"] < s["planned_doctor_visits"] * 0.97
    story.append(_figures(st, [
        (_n(s["doctor_visits"]), f"визитов к врачам из {_n(s['planned_doctor_visits'])} по плану", RISK if short else None),
        (_n(s["unique_doctors"]), f"врачей в {_n(s['unique_lpu'])} ЛПУ; в городе около {_n(s['available_doctors'])}", None),
        (_n(s["pharmacy_visits"]), f"визитов в {_n(s['unique_pharmacies'])} аптек", None),
        (f"{_n(s['avg_hours'], 1)} ч", f"средний день, среднее P80 {_n(s['avg_p80_hours'], 1)} ч",
         RISK if s.get("over_limit_days") else None),
    ], W))
    story.append(Spacer(1, 10))

    if specialty_rows:
        rows = [["Специальность", "В день / за срок на врача", "В базе", "Цель", "Охвачено", "Визиты / план"]]
        for row in specialty_rows:
            rows.append([Paragraph(_esc(row["specialty"]), st["cell"]), f"{row['per_day']} / {row['frequency']}",
                         _n(row["available"]), _n(row["target"]) if row.get("target") is not None else "—",
                         _n(row["unique"]), f"{_n(row['visits'])} / {_n(row['planned'])}"])
        story.append(_grid(rows, [W * .24, W * .20, W * .12, W * .12, W * .12, W * .20]))
        story.append(Spacer(1, 10))

    zones = [{"id": r["id"], "color": r["color"], "polygon": r["polygon"]} for r in plan["reps"]]
    points = [[f["lat"], f["lon"], 1 if f["kind"] == "lpu" else 0] for f in plan["facilities"]]
    mw = W * 0.5
    right = []
    if adv:
        cur = adv["curve"]
        xs = [x["d"] for x in cur]
        right.append(Paragraph("Длина дня при разном числе визитов к врачам", st["h3"]))
        right.append(LineChart(xs, [([x["hours"] for x in cur], DOC, "день", False)], W - mw - 8, 120,
                               "визитов к врачам в день", y_fmt=lambda v: f"{_n(v, 0)} ч", bars=True,
                               marks=[(d, INK, f"сейчас: {d}")] +
                                     ([(adv["model"]["max_doctor_visits"], PH, f"норма: до {adv['model']['max_doctor_visits']}")]
                                      if adv["model"]["max_doctor_visits"] != d else []),
                               hline=(s["workday_limit_h"], f"норма {_n(s['workday_limit_h'], 0)} ч")))
        right.append(Spacer(1, 6))
        right.append(Paragraph("Визиты к врачам, которые встанут в маршруты", st["h3"]))
        series = [([x["placed"] for x in cur], DOC, "в маршрутах", False),
                  ([x["planned"] for x in cur], INK3, "по плану", True)]
        right.append(LineChart(xs, series, W - mw - 8, 110, "визитов к врачам в день", marks=[(d, INK, f"сейчас: {d}")]))
    t = Table([[ZoneMap(zones, points, mw, mw * 0.95), right]], colWidths=[mw + 8, W - mw - 8])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(t)
    story.append(Paragraph("Синие точки показывают ЛПУ в плане, зелёные — аптеки. Цвет области обозначает зону МП.", st["muted"]))

    if adv:
        story.append(Paragraph("Оценка условий проекта", st["h2"]))
        rows = [["Параметр", "Сейчас", "Лучше", "Почему"]]
        for x in adv["params"]:
            rows.append([_status_param(st, x), str(x["current"]),
                         str(x["recommended"]) if x["recommended"] != x["current"] else "—",
                         Paragraph(_txt(x["why"]), st["cell"])])
        story.append(_grid(rows, [W * 0.24, W * 0.08, W * 0.08, W * 0.6]))
        if adv["scenarios"]:
            rows = [["Вариант", "Визитов к врачам", "Выполнение", "День, ч"]]
            for sc in adv["scenarios"]:
                rows.append([Paragraph(f"<b>{_esc(sc['title'])}</b><br/>{_esc(sc['note'])}", st["cell"]),
                             f"{_n(sc['placed'])} из {_n(sc['planned'])}", f"{sc['coverage']}%",
                             f"{_n(sc['hours'], 1)} (до {_n(sc['p80'], 1)})"])
            story.append(KeepTogether([Paragraph("Как можно изменить условия", st["h2"]),
                                       _grid(rows, [W * 0.5, W * 0.18, W * 0.14, W * 0.18])]))
    # нехватку врачей и длину дня уже разобрала оценка условий — не повторяем
    dupe = ("Не хватает врачей", "По нормативам") if adv else ()
    notes = [w for w in plan.get("warnings", []) if w["level"] != "info" and not w["text"].startswith(dupe)]
    if notes:
        story.append(Paragraph("Замечания", st["h2"]))
        for w in notes:
            story.append(Paragraph(f"— {_txt(w['text'])}", st["body"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(SOURCES, st["muted"]))
    foot = _footer(f"ЭЛОМЕР · {title}")
    doc.build(story, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue()


def _status_param(st, x):
    col = STATUS_COLOR[x["status"]].hexval().replace("0x", "#")
    return Paragraph(f'{_esc(x["label"])}<br/><font color="{col}" size="7.5">{PARAM_STATUS[x["status"]]}</font>', st["cell"])
