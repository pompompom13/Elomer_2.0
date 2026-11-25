r"""Аудит качества базы ЛПУ и аптек. Запуск из папки ELOMER_web:
    backend\.venv\Scripts\python backend\tools\audit_base.py
"""
import re, statistics, sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from app import facilities as F
from app.cities import index
from app.networks import annotate

OUT = str(ROOT / "examples" / "Аудит_качества_базы.xlsx")

# слова в названии, при которых «ЛПУ» скорее всего не место визита медпреда к врачу
NOT_CLINIC = {
    "косметология/красота": ["красот", "косметолог", "эстетик", "эстетич", "спа ", "spa", "салон", "beauty", "лазерн", "эпиляц", "массаж", "барбер"],
    "стоматология": ["стомат", "зуб", "дентал", "dent", "ортодонт"],
    "лаборатория/диагностика": ["лаборатор", "анализ", "гемотест", "хеликс", "инвитро", "мрт", "кт-центр", "рентген", "узи-кабинет"],
    "ветеринария": ["ветерин", "ветклиник", "зоомагаз", "зооцентр", " vet"],
    "оптика": ["оптик", "очки", "линз"],
    "немедицинское/служебное": ["морг", "судебно", "бюро смэ", "медико-социальн", "мсэ", "роспотребнадзор", "гигиены и эпидем",
                                 "переливания крови", "скорая", "смп", "санпропускник", "здравпункт", "медпункт", "медкабинет",
                                 "медицинский кабинет", "аптека", "склад", "колледж", "училище"],
    "психология/реабилитация вне медицины": ["психолог", "наркологическ", "реабилитационный центр", "хоспис", "дом престарел", "пансионат"],
    "санаторий/отдых": ["санатор", "профилактори", "база отдыха"],
}
SMALL = ["фап", "фельдшерско", "фельдшерский", "врачебная амбулатория", "амбулатория"]
CHILD_WORDS = ["детск", "педиатр"]


def norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[«»\"'.,()№#-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


cities, susp, dups, geo, cats_bad = [], [], [], [], []
check_counts = Counter()
for e in index():
    try:
        ds = F.load_city(e["slug"])
    except Exception:
        continue
    fac = [f for f in annotate(ds["facilities"]) if not (f["kind"] == "lpu" and F.excluded_name(f["name"]))]
    lpu = [f for f in fac if f["kind"] == "lpu"]
    ph = [f for f in fac if f["kind"] == "pharmacy"]
    c0 = ds.get("center") or [statistics.median(f["lat"] for f in fac), statistics.median(f["lon"] for f in fac)]
    # 1. подозрительные по названию
    n_susp = 0
    for f in lpu:
        n = norm(f["name"]) + " "
        for grp, words in NOT_CLINIC.items():
            w = next((w for w in words if w in n), None)
            if w:
                susp.append([ds["name"], f["name"], F.CATEGORY_LABELS.get(f["category"], f["category"]), grp, w, f.get("address", ""), f["source"], f["id"]])
                n_susp += 1
                break
        else:
            if f["category"] == "polyclinic_adult" and any(w in n for w in CHILD_WORDS):
                cats_bad.append([ds["name"], f["name"], "Поликлиника (взрослая)", "в названии детская/педиатрия", f["id"]])
            elif f["category"] in ("polyclinic_adult", "hospital") and any(w in n for w in SMALL) and "амбулатори" not in n:
                cats_bad.append([ds["name"], f["name"], F.CATEGORY_LABELS[f["category"]], "ФАП/амбулатория — мало врачей, оценка завышена", f["id"]])
    # 2. дубликаты: одинаковое название ближе 300 м
    by = defaultdict(list)
    for f in fac:
        by[(f["kind"], norm(f["name"]))].append(f)
    n_dup = 0
    for (kind, nm), lst in by.items():
        if len(lst) < 2 or not nm:
            continue
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                d = F.haversine(lst[i]["lat"], lst[i]["lon"], lst[j]["lat"], lst[j]["lon"])
                same_addr = norm(lst[i].get("address", "")) and norm(lst[i].get("address", "")) == norm(lst[j].get("address", ""))
                if d < 0.03 or (same_addr and d < 0.3):
                    dups.append([ds["name"], "ЛПУ" if kind == "lpu" else "Аптека", lst[i]["name"], lst[i].get("address", ""),
                                 lst[j].get("address", ""), round(d * 1000), lst[i]["source"] + " / " + lst[j]["source"]])
                    n_dup += 1
    # 3. координаты: далеко от центра
    dist = [F.haversine(c0[0], c0[1], f["lat"], f["lon"]) for f in fac]
    lim = max(30, 4 * statistics.median(dist)) if dist else 30
    n_far = 0
    for f, d in zip(fac, dist):
        if d > lim:
            geo.append([ds["name"], f["name"], "ЛПУ" if f["kind"] == "lpu" else "Аптека", round(d, 1), f.get("address", ""), f["source"]])
            n_far += 1
    no_addr = sum(1 for f in fac if not f.get("address"))
    pop = ds.get("population") or e.get("population")
    state_pc = sum(1 for f in lpu if f["category"] in ("polyclinic_adult", "polyclinic_child") and f.get("ownership", "state") != "private")
    ph_reg = [f for f in ph if f.get("in_registry") is not False]
    cities.append({
        "city": ds["name"], "region": e["region"], "pop": pop, "lpu": len(lpu), "ph": len(ph_reg),
        "lpu_100k": round(len(lpu) / pop * 1e5, 1) if pop else None,
        "pc_100k": round(state_pc / pop * 1e5, 1) if pop else None,
        "ph_10k": round(len(ph_reg) / pop * 1e4, 2) if pop else None,
        "susp": n_susp, "dup": n_dup, "far": n_far, "no_addr": no_addr,
        "no_reg": sum(1 for f in ph if f.get("in_registry") is False),
        "no_turn": sum(1 for f in ph_reg if not f.get("turnover")),
        "osm_lpu": sum(1 for f in lpu if f["source"] == "osm"),
    })
    check_counts["cities"] += 1

# оценка полноты: ЛПУ и аптеки на жителя против медианы по городам того же размера
def band(p):
    return "до 100 тыс." if p < 1e5 else "100–250 тыс." if p < 2.5e5 else "250–500 тыс." if p < 5e5 else "500 тыс.–1 млн" if p < 1e6 else "от 1 млн"
med = defaultdict(dict)
for k in ("lpu_100k", "ph_10k", "pc_100k"):
    g = defaultdict(list)
    for c in cities:
        if c["pop"] and c[k] is not None:
            g[band(c["pop"])].append(c[k])
    for b, v in g.items():
        med[k][b] = statistics.median(v)
for c in cities:
    b = band(c["pop"]) if c["pop"] else None
    flags = []
    if b:
        if c["lpu_100k"] < 0.5 * med["lpu_100k"][b]: flags.append("мало ЛПУ на карте")
        if c["pc_100k"] < 0.4 * med["pc_100k"][b]: flags.append("мало гос. поликлиник")
        if c["ph_10k"] < 0.5 * med["ph_10k"][b]: flags.append("мало аптек")
    if c["lpu"] and c["susp"] / c["lpu"] > 0.08: flags.append("много неврачебных объектов")
    if c["far"] > 3: flags.append("точки далеко от города")
    if c["ph"] and c["no_turn"] / c["ph"] > 0.5: flags.append("нет оборота у половины аптек")
    c["band"] = b
    c["flags"] = flags
    c["score"] = max(0, 100 - 25 * len([f for f in flags if f.startswith("мало")]) - 10 * (len(flags) - len([f for f in flags if f.startswith("мало")])))

# ── Excel ──
wb = Workbook()
HF, HFont = PatternFill("solid", fgColor="1E2A35"), Font(bold=True, color="FFFFFF")
def sheet(title, hdr, widths, rows, first=False):
    ws = wb.active if first else wb.create_sheet(title)
    ws.title = title
    ws.append(hdr)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        ws.cell(1, i).fill, ws.cell(1, i).font = HF, HFont
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws

tot_lpu = sum(c["lpu"] for c in cities); tot_ph = sum(c["ph"] for c in cities)
summary = [
    ["Городов проверено", len(cities), ""],
    ["ЛПУ всего", tot_lpu, ""],
    ["ЛПУ с подозрительным названием (не место визита)", len(susp), f"{len(susp) / tot_lpu:.1%} от ЛПУ"],
    ["ЛПУ с вероятно неверным типом", len(cats_bad), f"{len(cats_bad) / tot_lpu:.1%} от ЛПУ"],
    ["Дубликаты (одно название: тот же адрес или ближе 30 м)", len(dups), ""],
    ["Точки далеко от города", len(geo), ""],
    ["Учреждений без адреса", sum(c["no_addr"] for c in cities), ""],
    ["Аптек в плане (есть в реестре)", tot_ph, ""],
    ["Аптек карты, которых нет в реестре (не берутся)", sum(c["no_reg"] for c in cities), ""],
    ["Аптек без оборота", sum(c["no_turn"] for c in cities), f"{sum(c['no_turn'] for c in cities) / tot_ph:.1%} от аптек"],
    ["Городов с замечаниями по полноте", sum(1 for c in cities if any(f.startswith('мало') for f in c['flags'])), ""],
]
ws = sheet("Сводка", ["Проверка", "Найдено", "Доля"], [52, 12, 18], summary, first=True)
sheet("Города", ["Город", "Регион", "Жителей", "Группа", "Оценка качества", "Замечания", "ЛПУ", "ЛПУ на 100 тыс.",
                 "Гос. поликлиник на 100 тыс.", "Аптек", "Аптек на 10 тыс.", "Подозрит. ЛПУ", "Дубликаты", "Далеко",
                 "Без адреса", "Аптек без оборота"],
      [18, 24, 11, 14, 10, 40, 7, 10, 12, 7, 10, 10, 10, 8, 10, 12],
      [[c["city"], c["region"], c["pop"], c["band"], c["score"], "; ".join(c["flags"]), c["lpu"], c["lpu_100k"], c["pc_100k"],
        c["ph"], c["ph_10k"], c["susp"], c["dup"], c["far"], c["no_addr"], c["no_turn"]] for c in sorted(cities, key=lambda x: x["score"])])
sheet("Подозрительные ЛПУ", ["Город", "Название", "Тип в базе", "Похоже на", "Слово", "Адрес", "Источник", "ID"],
      [16, 46, 22, 26, 14, 36, 9, 18], susp)
sheet("Неверный тип", ["Город", "Название", "Тип в базе", "Почему", "ID"], [16, 50, 22, 44, 18], cats_bad)
sheet("Дубликаты", ["Город", "Вид", "Название", "Адрес 1", "Адрес 2", "Расстояние, м", "Источники"], [16, 8, 44, 32, 32, 12, 16], dups)
sheet("Координаты", ["Город", "Название", "Вид", "От центра, км", "Адрес", "Источник"], [16, 44, 8, 12, 40, 9], geo)
wb.save(OUT)
for r in summary:
    print(r)
grp = Counter(r[3] for r in susp)
print(grp.most_common())
print("worst:", [(c["city"], c["score"], c["flags"]) for c in sorted(cities, key=lambda x: x["score"])[:12]])
print("saved", OUT)
