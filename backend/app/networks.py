"""
Форма собственности и сеть учреждения.

OpenStreetMap не всегда хранит владельца, поэтому признаки берутся из тегов
(operator:type, operator, brand), а если их нет — из названия:
«ГБУЗ», «городская поликлиника №», «ЦРБ» и т. п. — государственные и муниципальные,
остальное — частные. Сеть — нормализованный бренд или название без номеров
и общих слов («аптека», «клиника», «медицинский центр»).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

_STATE_MARKERS = [
    "гуз", "гбуз", "гауз", "гбу ", "гбу«", "гбу\"", "муз", "мбуз", "мбу ", "фгбу", "фгбоу", "фбуз", "фгуз", "огбуз",
    "огауз", "кгбуз", "кгбу", "огуз", "бу ", "бюджетное", "казённое", "казенное", "государствен", "муниципальн",
    "гкб", "гп №", "гп ", "дгп", "дзм", "городская", "областная", "областной", "краевая", "краевой",
    "республиканск", "районная", "центральная районная", "црб", "поликлиника №", "поликлиника n",
    "больница №", "больница n", "госпиталь", "медсанчасть", "мсч", "фмба", "амбулатория", "фельдшерско",
    "фап", "женская консультация", "детская поликлиника", "родильный дом", "роддом", "перинатальный",
    "станция", "диспансер", "консультативно-диагностическ", "кдц", "кдп", "городской", "нии ", "научно-",
    "федеральн", "национальн", "университет", "ржд", "мвд", "фсб", "фсин", "минобороны", "военн",
]
_STATE_PHARMACY = ["гуп", "муп", "гбу", "фармаци", "госаптек", "государствен", "мособлфарм", "столичные аптеки",
                   "социальная аптека", "областной аптечный", "аптечный склад"]

_GENERIC_WORDS = [
    "аптечный пункт", "аптечный киоск", "аптека", "аптеки", "аптечная сеть", "пункт", "киоск",
    "многопрофильный медицинский центр", "многопрофильная клиника", "медицинский центр", "медицинская клиника",
    "медицинский", "медицинская", "клиника", "клиники", "центр", "мц", "филиал", "ооо", "зао", "пао", "ао", "ип",
    "поликлиника", "детская", "женская консультация", "медпункт", "кабинет", "отделение", "сеть", "на",
]
_GENERIC_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(_GENERIC_WORDS, key=len, reverse=True)) + r")\b")


def ownership(f: dict) -> str:
    """'state' — государственные и муниципальные (включая ведомственные), 'private' — частные."""
    ot = (f.get("operator_type") or "").lower()
    if ot in ("public", "government", "governmental", "state", "municipal"):
        return "state"
    if ot in ("private", "commercial", "business"):
        return "private"
    text = f" {f.get('name', '')} {f.get('operator', '')} ".lower().replace("ё", "е")
    if f["kind"] == "pharmacy":
        return "state" if any(m in text for m in _STATE_PHARMACY) else "private"
    if any(m in text for m in _STATE_MARKERS):
        return "state"
    if f.get("category") in ("polyclinic_adult", "polyclinic_child", "hospital", "womens") and not f.get("brand"):
        # поликлиники и больницы без бренда — почти всегда государственные
        return "state"
    return "private"


def network_key(f: dict) -> str:
    """Ключ сети: бренд или название без номеров и общих слов. Пустая строка — без сети."""
    if f["kind"] == "lpu" and f.get("ownership") == "state":
        return ""
    raw = f.get("brand") or f.get("name") or ""
    s = raw.lower().replace("ё", "е")
    s = re.sub(r"[«»\"'“”„]", " ", s)
    s = re.sub(r"№\s*\d+|n\s*\d+|\d+", " ", s)
    s = re.sub(r"[^\w\s+-]", " ", s)
    s = _GENERIC_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s if len(s) >= 2 else ""


def annotate(facilities: list[dict]) -> list[dict]:
    """Добавляет ownership, network (ключ) и network_name (для показа). Меняет записи на месте."""
    from . import care
    for f in facilities:
        if f["kind"] == "lpu":
            f["care_level"] = care.level(f)
        f["ownership"] = ownership(f)
        if f["kind"] == "lpu" and f["ownership"] == "state" and f.get("category") == "private_center":
            n = f.get("name", "").lower()
            f["category"] = "polyclinic_child" if "детск" in n else "polyclinic_adult"
        if f["kind"] == "lpu" and f.get("category") in ("polyclinic_adult", "private_center", "hospital"):
            from .facilities import is_ambulatory
            if is_ambulatory(f.get("name", "")):
                f["category"] = "ambulatory"
        f["network"] = network_key(f)
    # показываемое имя сети — самое частое исходное написание
    spell: dict[tuple, Counter] = defaultdict(Counter)
    for f in facilities:
        if f["network"]:
            spell[(f["kind"], f["network"])][(f.get("brand") or f["name"]).strip()] += 1
    names = {k: c.most_common(1)[0][0] for k, c in spell.items()}
    for f in facilities:
        f["network_name"] = names.get((f["kind"], f["network"]), "") if f["network"] else ""
    return facilities


def summary(facilities: list[dict]) -> dict:
    """Сети города по видам с числом точек — для фильтров в интерфейсе."""
    from .facilities import excluded_name
    facilities = [f for f in facilities
                  if f["kind"] == "pharmacy" or not excluded_name(f["name"])]
    out = {}
    for kind in ("lpu", "pharmacy"):
        cnt: Counter = Counter()
        names = {}
        for f in facilities:
            if f["kind"] != kind or (kind == "lpu" and f["ownership"] != "private"):
                continue
            if True:
                cnt[f["network"]] += 1
                names[f["network"]] = f["network_name"] or "Без названия сети"
        out[kind] = [{"key": k, "name": names[k], "count": c} for k, c in cnt.most_common()]
    return out
