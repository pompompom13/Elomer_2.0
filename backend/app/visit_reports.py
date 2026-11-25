"""
Разбор отчётов о визитах (CRM-выгрузки проектов, АКБ): врачи и аптеки, адреса, время.

Отчёты разные, но устроены похоже: по строке на визит, заголовок в одну или две строки
(«Дата» / «Дата визита», «Месяц»), колонки ищутся по названию. Из отчётов берутся:
 - учреждения с адресами и сколько врачей каждой специальности в них точно есть —
   по уникальным ФИО, а если ФИО нет — по наибольшему числу визитов одной специальности
   в учреждение за день (один медпред не ходит к одному врачу дважды за день);
 - реальные показатели работы: визитов в день, длительность визита, интервал между визитами.
"""
from __future__ import annotations

import io
import re
import statistics
from collections import defaultdict

from .field_data import _norm, map_spec

ALIASES = {
    "city": ["регион/город", "город", "регион", "населенный пункт", "населённый пункт"],
    "rep": ["мед представитель", "мед представитель (ф.и.)", "медпредставитель", "мп", "сотрудник", "медпред"],
    "day": ["дата визита", "дата", "дата визита.день", "факт дата визита"],
    "month": ["месяц", "дата визита.название месяца"],
    "type": ["вид визита"],
    "spec": ["специализация врача", "специальность", "таргет-группа", "специализация"],
    "doc": ["фио", "врач", "фио врача"],
    "doc_id": ["id врача", "id"],
    "lpu": ["лпу", "учреждение"],
    "inst2": ["учреждение"],
    "pharm": ["аптека", "аптека (название)"],
    "net": ["сеть"],
    "addr": ["адрес", "регион/адрес"],
    "start": ["время начала визита", "время начала", "план время начала визита"],
    "end": ["время окончания визита", "время окончания", "план время окончания визита"],
    "dur": ["длительность", "план длительность, мин"],
    "comment": ["комментарий"],
}
MONTHS = {m: i + 1 for i, m in enumerate(["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август",
                                           "сентябр", "октябр", "ноябр", "декабр"])}


def _month(s) -> int | None:
    n = _norm(s)
    for k, v in sorted(MONTHS.items(), key=lambda t: -len(t[0])):
        if n.startswith(k):
            return v
    return None


def _minutes(v) -> float | None:
    """'09:40', '0:10:58', '00:08:00', datetime.time, 22 (минуты) → минуты."""
    if v is None or v != v:
        return None
    if hasattr(v, "hour"):
        return v.hour * 60 + v.minute + getattr(v, "second", 0) / 60
    if isinstance(v, (int, float)):
        return float(v) * 24 * 60 if 0 < v < 1 else float(v)
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", str(v))
    if not m:
        try:
            return float(str(v).replace(",", "."))
        except ValueError:
            return None
    return int(m[1]) * 60 + int(m[2]) + int(m[3] or 0) / 60


def _date_key(day, month) -> str | None:
    """Дата визита как «MM-DD»: год в отчётах часто не указан."""
    if day is None or day != day:
        return None
    if hasattr(day, "month"):
        return f"{day.month:02d}-{day.day:02d}"
    s = str(day).strip()
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./]\d{2,4}", s)
    if m:
        return f"{int(m[2]):02d}-{int(m[1]):02d}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m[2]}-{m[3]}"
    mo = _month(month)
    try:
        d = int(float(s))
    except ValueError:
        return None
    return f"{mo:02d}-{d:02d}" if mo else f"00-{d:02d}"


_CITY_RE = re.compile(r"(?:^|[\s,(])(?:г\.|г\s|город\s)\s*([А-ЯЁ][А-Яа-яёЁ\-]+(?:[\s-][А-ЯЁ][а-яё\-]+)?)")


_known: set[str] | None = None


def known_places() -> set[str]:
    """Названия городов справочника и населённых пунктов из реестра аптек."""
    global _known
    if _known is None:
        from . import cities as C
        from .pharmacy_registry import load
        _known = {_norm(e["name"]) for e in C.index()}
        reg = load()
        if reg:
            _known |= {r["settlement"] for r in reg["rows"]}
    return _known


def _place(s: str) -> str:
    return re.sub(r"\s+(г|г\.|город)$", "", str(s or "").strip(), flags=re.I)


def settlement(addr: str, region_city: str) -> str:
    """Населённый пункт визита: «г. Одинцово, …» → Одинцово; иначе первая часть адреса или колонка
    «Регион/Город», если это известный населённый пункт; иначе пусто — город определится по медпреду."""
    known = known_places()
    m = _CITY_RE.search(str(addr or ""))
    if m:
        cap = m[1].strip().replace("Санкт Петербург", "Санкт-Петербург")
        words = cap.split()
        if _norm(cap) in known:
            return cap
        if len(words) > 1 and _norm(words[0]) in known:  # «г. Москва Есенинский б-р» → Москва
            return words[0]
        if _norm(words[0]) not in ("ул", "улица", "пр", "пер"):
            return cap
    first = _place(re.sub(r"^\d{6},?\s*", "", str(addr or "")).split(",")[0])
    rc = _place(str(region_city or "").split(",")[-1])
    rc_ok = _norm(rc) in known
    # первая часть адреса — город, только если это город справочника или колонка города пуста/регион:
    # иначе «Ленинский, 10» (проспект) приняли бы за посёлок Ленинский
    if _norm(first) in known and (not rc_ok or _norm(first) in _cities()):
        return first
    return rc if rc_ok else ""


_city_names: set[str] | None = None


def _cities() -> set[str]:
    global _city_names
    if _city_names is None:
        from . import cities as C
        _city_names = {_norm(e["name"]) for e in C.index()}
    return _city_names


def _clean_addr(addr: str) -> str:
    """Убрать индекс, регион и город — останутся улица и дом."""
    a = re.sub(r"^\d{6},?\s*", "", str(addr or ""))
    parts = [p.strip() for p in a.split(",") if p.strip()]
    keep = [p for p in parts if not re.match(r"^(г\.?|город|.*област|.*край|респ|москва$|.*район)", p.lower())]
    return ", ".join(keep) or a


def _header(df, i):
    """Имена колонок: заголовок может быть в две строки («Дата» / «Дата визита»)."""
    a = [_norm(v) if v == v and v is not None else "" for v in df.iloc[i].tolist()]
    b = [_norm(v) if v == v and v is not None else "" for v in df.iloc[i + 1].tolist()] if i + 1 < len(df) else []
    names = []
    for j, x in enumerate(a):
        y = b[j] if j < len(b) else ""
        names.append((x, y))
    return names


def _find(names, key):
    for al in ALIASES[key]:
        for j, (x, y) in enumerate(names):
            if y == al:
                return j
        for j, (x, y) in enumerate(names):
            if x == al:
                return j
    return None


def read(files: list[tuple[str, bytes]], project: str | None = None) -> list[dict]:
    """Все визиты из файлов: kind = lpu | pharmacy."""
    import pandas as pd
    out = []
    for fname, content in files:
        proj = project or re.sub(r"\.\w+$", "", fname.split("\\")[-1].split("/")[-1])
        if fname.lower().endswith(".csv"):
            sheets = {"csv": pd.read_csv(io.BytesIO(content), dtype=object, header=None, sep=None, engine="python")}
        else:
            sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, dtype=object, header=None)
        for sname, df in sheets.items():
            hdr = None
            for i in range(min(12, len(df))):
                row = [_norm(v) for v in df.iloc[i].tolist() if v == v and v is not None]
                if any(x in row for x in ("адрес", "регион/адрес")) and any(
                        x in row for x in ALIASES["lpu"] + ALIASES["pharm"] + ["врач"]):
                    hdr = i
                    break
            if hdr is None:
                continue
            names = _header(df, hdr)
            col = {k: _find(names, k) for k in ALIASES}
            kind = "pharmacy" if col["pharm"] is not None and col["lpu"] is None else "lpu"
            if kind == "lpu" and col["lpu"] is None:
                continue
            start_row = hdr + (2 if any(y for _, y in names) else 1)
            for r in df.iloc[start_row:].itertuples(index=False):
                def g(k):
                    j = col[k]
                    if j is None or j >= len(r):
                        return None
                    v = r[j]
                    return None if v is None or v != v else v
                name = (g("lpu") or g("inst2")) if kind == "lpu" else g("pharm")
                addr = g("addr")
                if not name or not addr or _norm(name) in ("лпу", "аптека", "аптека (название)"):
                    continue
                comment = str(g("comment") or "")
                if "не работает" in comment.lower():
                    continue
                if comment and re.search(r"\d", comment) and "аптек" not in comment.lower():
                    addr = comment
                region_city = str(g("city") or "")
                if kind == "pharmacy" and not region_city and "регион/адрес" in [x for x, _ in names]:
                    region_city = str(addr).split(",")[0]
                start, end = _minutes(g("start")), _minutes(g("end"))
                dur = _minutes(g("dur"))
                if dur is not None and dur > 300:
                    dur = None
                if dur is None and start is not None and end is not None and end > start:
                    dur = end - start
                doc = g("doc")
                doc_key = _norm(doc) if doc and not str(doc).strip().isdigit() else (str(g("doc_id") or "").strip() or None)
                inst = re.sub(r"\s*\(.*$", "", str(name)).strip() or str(name)  # «ДГП 69 (Москва, …)» → «ДГП 69»
                out.append({
                    "project": proj, "kind": kind, "region_city": region_city,
                    "settlement": settlement(str(addr), region_city), "rep": str(g("rep") or "").strip(),
                    "date": _date_key(g("day"), g("month")), "type": _norm(g("type")),
                    "spec": map_spec(g("spec")) if kind == "lpu" else None, "doc": doc_key,
                    "inst": inst, "addr": _clean_addr(str(addr)), "net": str(g("net") or ""),
                    "start": start, "end": end, "dur": dur,
                })
    return out


def rep_cities(visits: list[dict]) -> dict[tuple, str]:
    """Основной город каждого медпреда — по его визитам с известным городом."""
    cnt: dict[tuple, dict] = defaultdict(lambda: defaultdict(int))
    for v in visits:
        if v["settlement"]:
            cnt[(v["project"], v["rep"])][v["settlement"]] += 1
    return {k: max(c, key=c.get) for k, c in cnt.items()}


def facility_groups(visits: list[dict]) -> list[dict]:
    """Учреждения из визитов: сколько врачей каждой специальности в них точно есть."""
    groups: dict[tuple, dict] = {}
    home = rep_cities(visits)
    for v in visits:
        if v["kind"] != "lpu":
            continue
        k = (_norm(v["settlement"]), _norm(v["inst"]), _norm(v["addr"]))
        g = groups.setdefault(k, {"settlement": v["settlement"], "inst": v["inst"], "addr": v["addr"],
                                  "rep_cities": defaultdict(int), "named": defaultdict(set),
                                  "daily": defaultdict(lambda: defaultdict(int)), "visits": 0})
        g["visits"] += 1
        g["rep_cities"][home.get((v["project"], v["rep"]), "")] += 1
        if not v["spec"]:
            continue
        if v["doc"]:
            g["named"][v["spec"]].add(v["doc"])
        else:
            g["daily"][v["spec"]][(v["project"], v["rep"], v["date"])] += 1
    out = []
    for g in groups.values():
        counts = {}
        for s in set(g["named"]) | set(g["daily"]):
            counts[s] = max(len(g["named"].get(s, ())), max(g["daily"][s].values(), default=0))
        out.append({"settlement": g["settlement"], "inst": g["inst"], "addr": g["addr"], "counts": counts,
                    "hint_city": max(g["rep_cities"], key=g["rep_cities"].get) if g["rep_cities"] else "",
                    "visits": g["visits"]})
    return out


def _q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))]


def work_stats(visits: list[dict]) -> dict:
    """Реальная работа медпредов по проектам: визитов в день, длительность, интервалы."""
    by_proj = defaultdict(list)
    for v in visits:
        by_proj[v["project"]].append(v)
    res = {}
    for proj, vs in by_proj.items():
        days = defaultdict(list)
        for v in vs:
            if v["rep"] and v["date"]:
                days[(v["rep"], v["date"])].append(v)
        doc_day = [sum(1 for x in d if x["kind"] == "lpu") for d in days.values()]
        ph_day = [sum(1 for x in d if x["kind"] == "pharmacy") for d in days.values()]
        mixed = [d for d in days.values() if any(x["kind"] == "lpu" for x in d)]
        gaps, spans, starts, ends, lpu_per_day = [], [], [], [], []
        for d in days.values():
            t = sorted((x for x in d if x["start"] is not None), key=lambda x: x["start"])
            if len(t) >= 2:
                for a, b in zip(t, t[1:]):
                    if a["end"] is not None and b["start"] >= a["end"]:
                        gaps.append(b["start"] - a["end"])
                spans.append((max(x["end"] or x["start"] for x in t) - t[0]["start"]) / 60)
            if t:
                starts.append(t[0]["start"])
                ends.append(max(x["end"] or x["start"] for x in t))
            lpu_per_day.append(len({x["inst"] for x in d if x["kind"] == "lpu"}))
        doc_dur = [x["dur"] for x in vs if x["kind"] == "lpu" and x["dur"] and 1 <= x["dur"] <= 60]
        ph_dur = [x["dur"] for x in vs if x["kind"] == "pharmacy" and x["dur"] and 1 <= x["dur"] <= 90]
        types = defaultdict(int)
        for x in vs:
            if x["kind"] == "lpu" and x["type"]:
                types[x["type"]] += 1
        specs = defaultdict(int)
        for x in vs:
            if x["spec"]:
                specs[x["spec"]] += 1
        per_doc = defaultdict(int)
        for x in vs:
            if x["kind"] == "lpu" and x["doc"]:
                per_doc[(x["inst"], x["doc"])] += 1
        res[proj] = {
            "doctor_visits": sum(1 for x in vs if x["kind"] == "lpu"),
            "pharmacy_visits": sum(1 for x in vs if x["kind"] == "pharmacy"),
            "reps": len({x["rep"] for x in vs if x["rep"]}),
            "rep_days": len(days),
            "cities": len({x["region_city"] for x in vs}),
            "doctor_visits_per_day": {"median": _q([x for x in doc_day if x], .5), "p75": _q([x for x in doc_day if x], .75),
                                      "mean": round(statistics.mean([x for x in doc_day if x]), 1) if any(doc_day) else None},
            "pharmacy_visits_per_day": {"median": _q([x for x in ph_day if x], .5),
                                        "mean": round(statistics.mean([x for x in ph_day if x]), 1) if any(ph_day) else None},
            "lpu_per_day": _q([x for x in lpu_per_day if x], .5),
            "doctor_visit_min": _q(doc_dur, .5), "pharmacy_visit_min": _q(ph_dur, .5),
            "gap_min": _q(gaps, .5), "gap_p75": _q(gaps, .75),
            "day_span_h": round(_q(spans, .5), 2) if spans else None,
            "day_start": _q(starts, .5), "day_end": _q(ends, .5),
            "visit_types": dict(types), "specialties": dict(sorted(specs.items(), key=lambda t: -t[1])[:8]),
            "visits_per_doctor": round(statistics.mean(per_doc.values()), 2) if per_doc else None,
            "mixed_days": len(mixed),
        }
    return res
