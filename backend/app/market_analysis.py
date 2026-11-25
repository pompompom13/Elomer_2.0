"""Сверка списков заказчиков и отчёты по наличию Ютеки."""
from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import date, datetime
from statistics import median

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from zipfile import BadZipFile

from . import facilities as F, pharmacy_registry as DSM
from .cities import index


def _text(value):
    return str(value).strip() if value is not None else ""


def _norm(value):
    return " ".join(_text(value).lower().replace("ё", "е").split())


def _code(value):
    return re.sub(r"\.0+$", "", _text(value))


def _address(value):
    text = _norm(value)
    text = re.sub(r"(?<!\w)(?:улица|ул|дом|д)\.?\s+(?=\w)", "", text)
    text = re.sub(r"(?<!\w)(?:проспект|пр-кт|пр-т)\.?\s+", "пр ", text)
    text = re.sub(r"(\d)\s*(?:корпус|корп|к)\.?\s*(?=\d)", r"\1к", text)
    text = re.sub(r"(\d)\s*(?:строение|стр|с)\.?\s*(?=\d)", r"\1с", text)
    return " ".join(re.findall(r"[a-zа-я0-9/]+", text))


def _coordinates(lat, lon):
    try:
        values = [float(_text(v).replace(",", ".")) for v in (lat, lon)]
    except ValueError:
        return None
    return values if -90 <= values[0] <= 90 and -180 <= values[1] <= 180 and all(values) else None


def _read(content, headers, required, preferred=()):
    try:
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, BadZipFile, ValueError) as e:
        raise ValueError("Загрузите таблицу Excel в формате .xlsx.") from e
    try:
        sheets = [book[n] for n in preferred if n in book.sheetnames] or book.worksheets
        for sheet in sheets:
            for i, row in enumerate(sheet.values, 1):
                if i > 10:
                    break
                values = [_norm(v) for v in row]
                positions = {key: next((values.index(v) for v in aliases if v in values), None)
                             for key, aliases in headers.items()}
                if all(positions[k] is not None for k in required):
                    return [
                        (j, {k: r[pos] if pos is not None and pos < len(r) else None for k, pos in positions.items()})
                        for j, r in enumerate(sheet.iter_rows(min_row=i + 1, values_only=True), i + 1)
                        if any(v is not None and v != "" for v in r)
                    ]
    finally:
        book.close()
    raise ValueError("Ожидаются колонки: " + ", ".join(headers[k][0] for k in required))


CRM = {
    "city": ("город", "населенный пункт"), "region": ("регион", "субъект рф"),
    "name": ("организация", "учреждение", "наименование", "название"),
    "kind": ("тип визита", "тип учреждения", "тип", "категория"),
    "address": ("адрес crm", "адрес", "адрес учреждения", "фактический адрес"),
    "code": ("код dsm", "код дсм", "dsm id", "dsm"),
    "lat": ("широта", "gps_широта", "lat", "latitude"),
    "lon": ("долгота", "gps_долгота", "lon", "lng", "longitude"),
}
UTEKA = {
    "date": ("дата",), "city": ("город/регион",), "brand": ("бренд",),
    "sku": ("полное наименование",), "delivery": ("аптек на доставку",),
    "stock": ("аптек с факт наличием",),
}


def _registry():
    data = DSM.load()
    if not data:
        raise ValueError("В ЭЛОМЕРЕ не загружен реестр аптек ДСМ.")
    return data["rows"]


def _city_catalog():
    result = defaultdict(list)
    for item in index():
        result[_norm(item["name"])].append(item)
    return result


def _kind(value):
    s = _norm(value)
    if s.startswith(("аптек", "pharm")):
        return "pharmacy"
    if (s.startswith(("поликлиник", "больниц", "стационар", "амбулатор", "клиник", "медцентр"))
            or s in ("врач", "лпу", "двойной визит", "госпитальный визит", "визит к администратору", "визит к kol", "lpu")):
        return "lpu"
    return None


def _city_points(city, pharmacies):
    ds = F.load_city(city["slug"])
    center = ds["center"]
    points = []
    for f in ds["facilities"]:
        if f["kind"] == "lpu" and not f.get("needs_coords") and not F.excluded_name(f["name"]):
            points.append({"id": f["id"], "kind": "lpu", "code": _code(f.get("dsm_id")), "name": f["name"],
                           "address": f.get("address") or "", "coordinates": [f["lat"], f["lon"]]})
    for f in pharmacies:
        if F.haversine(*center, f["lat"], f["lon"]) > 60:
            continue
        points.append({"id": f"reg_{f['code']}", "kind": "pharmacy", "code": _code(f["code"]),
                       "name": f["network"] or f["legal"] or "Аптека", "address": DSM._short_addr(f) or f["address"],
                       "coordinates": [f["lat"], f["lon"]]})
    return points


def _select_point(row, points, codes, addresses):
    if row["code"]:
        candidates = codes.get(row["code"], [])
        if row["kind"]:
            candidates = [p for p in candidates if p["kind"] == row["kind"]]
        if len(candidates) == 1:
            return candidates[0], "matched", "Код ДСМ"
        return None, "review", "Код ДСМ не найден в выбранном городе" if not candidates else "Код ДСМ неоднозначен"
    if not row["kind"] and not row["coordinates"]:
        return None, "review", "Уточните тип учреждения"
    exact = addresses.get((row["kind"], _address(row["address"])), [])
    if len(exact) == 1 and not row["coordinates"]:
        return exact[0], "matched", "Адрес"
    if row["coordinates"]:
        nearby = sorted((F.haversine(*row["coordinates"], *p["coordinates"]), p["id"], p)
                        for p in (exact or points) if not row["kind"] or p["kind"] == row["kind"])
        if nearby and nearby[0][0] <= 0.075:
            if len(nearby) == 1 or nearby[1][0] - nearby[0][0] >= 0.025:
                return nearby[0][2], "matched", f"Координаты, {round(nearby[0][0] * 1000)} м"
            return None, "review", "Рядом несколько учреждений"
        if exact:
            return None, "review", "Адрес и координаты расходятся"
    if len(exact) > 1:
        named = [p for p in exact if _norm(p["name"]) == _norm(row["name"])]
        if len(named) == 1:
            return named[0], "matched", "Адрес и название"
        return None, "review", "На адресе несколько учреждений"
    if not row["coordinates"] and not _address(row["address"]):
        return None, "review", "Нет адреса, кода ДСМ или координат"
    return None, "only_client", "Совпадение не найдено"


def compare(content):
    raw = _read(content, CRM, ("city",), ("Визиты",))
    if not raw or not any(any(row[k] for k in ("address", "code", "lat")) for _, row in raw):
        raise ValueError("В списке нужны адрес CRM, код ДСМ или широта и долгота.")
    catalog = _city_catalog()
    pharmacies = defaultdict(list)
    for point in _registry():
        pharmacies[point["settlement"]].append(point)
    groups = {}
    issues = []
    for number, row in raw:
        city = _norm(row["city"])
        candidates = catalog.get(city, [])
        if row["region"]:
            candidates = [c for c in candidates if _norm(c["region"]) == _norm(row["region"])]
        if len(candidates) != 1 or not F.city_path(candidates[0]["slug"]).is_file():
            issues.append({"row": number, "text": f"{_text(row['city']) or 'Город не указан'}: город не определён или его база не загружена"})
            continue
        if _norm(row["kind"]).startswith("кс вне лпу"):
            issues.append({"row": number, "text": "Мероприятие вне ЛПУ исключено из сверки"})
            continue
        c = candidates[0]
        group = groups.setdefault(c["slug"], {"city": c, "input": {}})
        kind, code = _kind(row["kind"]), _code(row["code"])
        coords = _coordinates(row["lat"], row["lon"])
        key = (kind, code, _address(row["address"]), _norm(row["name"]), tuple(coords or []))
        group["input"].setdefault(key, {"name": _text(row["name"]) or "Учреждение заказчика",
                                       "address": _text(row["address"]), "coordinates": coords, "kind": kind,
                                       "code": code, "row": number})
    cities = []
    for group in groups.values():
        c, inputs = group["city"], group["input"].values()
        points = _city_points(c, pharmacies[_norm(c["name"])])
        codes, addresses = defaultdict(list), defaultdict(list)
        kinds = {r["kind"] for r in inputs if r["kind"]}
        for p in points:
            if p["code"]:
                codes[p["code"]].append(p)
            if p["address"]:
                addresses[p["kind"], _address(p["address"])].append(p)
        found, unmatched = {}, []
        for row in sorted(inputs, key=lambda r: (not bool(r["code"]), not bool(r["coordinates"]))):
            p, status, method = _select_point(row, points, codes, addresses)
            row = {**row, "kind": p["kind"] if p else row["kind"], "status": status, "method": method, "base": p}
            if p:
                kinds.add(p["kind"])
                found.setdefault(p["id"], row)
            else:
                unmatched.append(row)
        eligible = [p for p in points if p["kind"] in kinds]
        detail = list(found.values()) + unmatched
        detail += [{"status": "only_base", "name": p["name"], "address": p["address"], "kind": p["kind"],
                    "code": p["code"], "coordinates": p["coordinates"], "base": p}
                   for p in eligible if p["id"] not in found]
        cities.append({"city": c["name"], "slug": c["slug"], "points": detail, "base_count": len(eligible),
                       "matched": len(found), "client_count": len(found) + len(unmatched),
                       "review": sum(p["status"] == "review" for p in unmatched)})
    return {"cities": cities, "issues": issues}


SPECIAL_GEOGRAPHIES = {
    "москва": ("москва", ("г. москва",)),
    "московская область": ("москва", ("московская область",)),
    "московская область (без москвы)": ("москва", ("московская область",)),
    "москва и московская область": ("москва", ("г. москва", "московская область")),
    "санкт-петербург": ("санкт-петербург", ("г. санкт-петербург",)),
    "ленинградская область": ("санкт-петербург", ("ленинградская область",)),
    "ленинградская область (без спб)": ("санкт-петербург", ("ленинградская область",)),
    "санкт-петербург и ленинградская область": ("санкт-петербург", ("г. санкт-петербург", "ленинградская область")),
}
REGION_NAMES = {
    "удмуртия": "удмуртская республика", "республика удмуртия": "удмуртская республика",
    "татарстан": "республика татарстан", "чувашия": "чувашская республика",
    "чувашская республика — чувашия": "чувашская республика",
    "башкортостан": "республика башкортостан",
}


def _region(value):
    key = _norm(value)
    if key.startswith("республика ") and key.endswith(("татарстан", "башкортостан")):
        return key
    if "чуваш" in key:
        return "чувашская республика"
    if "удмурт" in key:
        return "удмуртская республика"
    return REGION_NAMES.get(key, key)


def _city_coordinates(city, records):
    if city:
        coords = _coordinates(city.get("lat"), city.get("lon"))
        if coords:
            return coords
        try:
            center = F.load_city(city["slug"]).get("center")
        except KeyError:
            center = None
        if center:
            return center
    if records:
        return [median(r["lat"] for r in records.values()), median(r["lon"] for r in records.values())]
    return None


def _settlement_records(name, catalog, settlements):
    cities = catalog.get(name, [])
    places = settlements.get(name, {})
    if len(cities) == 1:
        return places.get(_region(cities[0]["region"]), {}), cities[0]
    if not cities and len(places) == 1:
        return next(iter(places.values())), None
    return {}, None


def _geography(label, catalog, settlements, regions):
    key = _norm(label)
    if key in SPECIAL_GEOGRAPHIES:
        city, territories = SPECIAL_GEOGRAPHIES[key]
        records = {}
        for region in territories:
            records.update(regions.get(region, {}))
        c = catalog.get(city, [{}])[0]
        is_city = key in ("москва", "санкт-петербург")
        return len(records), _city_coordinates(c or None, records) if is_city else None, "город" if is_city else "регион"
    records, city = _settlement_records(key, catalog, settlements)
    if city or records:
        return len(records), _city_coordinates(city, records), "город"
    if not catalog.get(key) and _region(key) in regions:
        return len(regions[_region(key)]), None, "регион"
    if "," in key:
        members = [v.strip() for v in key.split(",")]
        matched = [_settlement_records(name, catalog, settlements)[0] for name in members]
        if all(matched):
            return len(set().union(*matched)), None, "группа городов"
    return 0, None, "не определена"


def _date(value):
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    for fmt in ("%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(_text(value), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _quantity(value):
    try:
        n = float(_text(value).replace(" ", "").replace("\xa0", "").replace(",", "."))
        return int(n) if n.is_integer() and n >= 0 else None
    except ValueError:
        return None


def distribution(content):
    raw = _read(content, UTEKA, tuple(UTEKA))
    catalog = _city_catalog()
    regions, settlements = defaultdict(dict), defaultdict(lambda: defaultdict(dict))
    for p in _registry():
        if p["code"] is not None:
            regions[_region(p["region"])][p["code"]] = p
            settlements[p["settlement"]][_region(p["region"])][p["code"]] = p
    geo_cache, entries = {}, {}
    issues = []
    for number, r in raw:
        city, brand, sku = (_text(r[key]) for key in ("city", "brand", "sku"))
        sku = re.sub(r"^купить\s+", "", sku, flags=re.I)
        day = _date(r["date"])
        if not day or not city or not brand or not sku:
            issues.append({"row": number, "text": "Не заполнены дата, город, бренд или позиция"})
            continue
        if _norm(city) not in geo_cache:
            geo_cache[_norm(city)] = _geography(city, catalog, settlements, regions)
        base, coords, scope = geo_cache[_norm(city)]
        stock, delivery = _quantity(r["stock"]), _quantity(r["delivery"])
        key = (day, _norm(city), _norm(brand), _norm(sku))
        if key in entries:
            previous = entries[key]
            if (previous["stock"], previous["delivery"]) != (stock, delivery):
                previous.update(stock=None, delivery=None, nd=None, preorder=None, note="Противоречивые дубли")
            issues.append({"row": number, "text": "Повтор позиции в этой географии и дате: " + sku})
            continue
        warnings = []
        if not base:
            warnings.append("География не сопоставлена с базой ДСМ")
        if stock is None:
            warnings.append("Нет числа аптек с наличием")
        if delivery is None:
            warnings.append("Нет числа аптек на доставку")
        if stock is not None and base and stock > base:
            warnings.append("Наличие превышает базу ДСМ")
        if stock is not None and delivery is not None and delivery < stock:
            warnings.append("Доставка меньше наличия")
        entries[key] = {"date": day, "city": city, "scope": scope, "brand": brand, "sku": sku,
                        "coordinates": coords, "dsm": base, "stock": stock, "delivery": delivery,
                        "nd": stock / base if base and stock is not None and stock <= base else None,
                        "preorder": delivery - stock if delivery is not None and stock is not None and delivery >= stock else None,
                        "note": ". ".join(warnings)}
    if not entries:
        raise ValueError("В файле нет строк с датой, городом, брендом и позицией.")
    return {"rows": sorted(entries.values(), key=lambda r: (r["date"], r["city"], r["brand"], r["sku"])),
            "issues": issues}


STATUS_LABEL = {"matched": "Совпало", "only_base": "Только в ЭЛОМЕРЕ",
                "only_client": "Только у заказчика", "review": "Требует проверки"}


def _excel(book, highlighted=None):
    for sh in book:
        sh.freeze_panes = "A2"
        sh.auto_filter.ref = sh.dimensions
        for cell in sh[1]:
            cell.fill = PatternFill("solid", fgColor="294EA3")
            cell.font = Font(bold=True, color="FFFFFF")
        for col in sh.columns:
            letter = get_column_letter(col[0].column)
            sh.column_dimensions[letter].width = min(65, max(13, max(len(str(c.value or "")) for c in col) + 3))
    if highlighted:
        fill = PatternFill("solid", fgColor="EBF1DE")
        for cell in highlighted:
            cell.fill = fill
        highlighted[0].font = Font(bold=True, color="243517")
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def _append(sh, values):
    sh.append(values)
    for cell in sh[sh.max_row]:
        if cell.data_type == "f":
            cell.data_type = "s"  # строки из загрузки не должны превращаться в формулы


def distribution_xlsx(report):
    book = Workbook()
    summary = book.active
    summary.title = "Уровень ND"
    summary.append(["Город или регион", "Количество аптек ДСМ", "Бренд", "Форма выпуска", "Аптек на доставку",
                    "Факт. наличие", "Нумерическая дистрибуция, %", "Возможность предзаказа", "Дата", "Замечание"])
    raw = book.create_sheet("Исходные данные")
    raw.append(["Город или регион", "Бренд", "Полное наименование", "Аптек на доставку", "Аптек с фактическим наличием", "Дата"])
    dsm = book.create_sheet("Реальная база аптек (DSM)")
    dsm.append(["Город или регион", "Аптек по ДСМ", "Тип территории"])
    for city, base, scope in sorted({(r["city"], r["dsm"], r["scope"]) for r in report["rows"]}):
        _append(dsm, [city, base or None, scope])
    dsm_rows = {shrow[0].value: shrow[0].row for shrow in list(dsm.rows)[1:]}
    for i, r in enumerate(report["rows"], 2):
        _append(raw, [r["city"], r["brand"], r["sku"], r["delivery"], r["stock"], r["date"]])
        _append(summary, [r["city"], r["dsm"] or None, r["brand"], r["sku"], r["delivery"], r["stock"],
                          r["nd"], r["preorder"], r["date"], r["note"]])
        if r["dsm"]:
            summary[f"B{i}"] = f"='Реальная база аптек (DSM)'!B{dsm_rows[r['city']]}"
        summary[f"G{i}"].number_format = "0.00%"
        if r["nd"] is not None:
            summary[f"G{i}"] = f"=F{i}/B{i}"
        if r["preorder"] is not None:
            summary[f"H{i}"] = f"=E{i}-F{i}"
    _issues_sheet(book, report["issues"])
    return _excel(book, highlighted=summary["G"])


def _issues_sheet(book, issues):
    if issues:
        sh = book.create_sheet("Проверка загрузки")
        sh.append(["Строка", "Причина"])
        for issue in issues:
            _append(sh, [issue["row"], issue["text"]])


def compare_xlsx(report):
    book = Workbook()
    summary = book.active
    summary.title = "По городам"
    summary.append(["Город", "Тип", "Точек в базе", "Совпало", "Только в ЭЛОМЕРЕ",
                    "Только у заказчика", "Требует проверки", "Охват базы"])
    detail = book.create_sheet("Адреса")
    detail.append(["Город", "Тип", "Статус", "Точка", "Адрес", "Код ДСМ", "Метод или причина",
                   "Точка ЭЛОМЕРА", "Адрес ЭЛОМЕРА", "Широта", "Долгота"])
    kinds = {"lpu": "ЛПУ", "pharmacy": "Аптека", None: "Не определён"}
    for c in report["cities"]:
        for k in {p["kind"] for p in c["points"]}:
            counts = {status: sum(p["status"] == status and p["kind"] == k for p in c["points"]) for status in STATUS_LABEL}
            base = counts["matched"] + counts["only_base"]
            _append(summary, [c["city"], kinds[k], base, *counts.values(), counts["matched"] / base if base else None])
            summary.cell(summary.max_row, 8).number_format = "0.00%"
        for p in c["points"]:
            base = p.get("base") or {}
            _append(detail, [c["city"], kinds[p["kind"]], STATUS_LABEL[p["status"]], p["name"], p["address"],
                             p["code"] or base.get("code"), p.get("method"), base.get("name"), base.get("address"),
                             *(p["coordinates"] or base.get("coordinates") or [None, None])])
    _issues_sheet(book, report["issues"])
    return _excel(book)
