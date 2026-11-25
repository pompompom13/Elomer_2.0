"""Классификация звена по данным учреждения и единые правила расчёта долей."""
from functools import lru_cache
import re
from typing import Literal

CareFilter = Literal["all", "ambulatory", "hospital", "mixed", "unknown"]
LABELS = {"ambulatory": "Амбулаторное", "hospital": "Госпитальное",
          "mixed": "Смешанное", "unknown": "Не определено"}

_OUTPATIENT = re.compile(r"поликлиник|амбулатор|женская\s+консультация|стоматолог|общей\s+врачебной\s+практики|фельдшерско")
_OUTPATIENT_UNIT = re.compile(
    r"поликлиническое\s+отделение|амбулаторное\s+отделение|консультативно[- ]диагностическ(?:ий\s+центр|ое\s+отделение)|"
    r"отделение\s+(?:поликлиники|амбулаторн\w+\s+при[её]ма)"
)
_INPATIENT = re.compile(r"больниц|госпитал|стационар|родил\w*\s+дом|роддом")
_INPATIENT_UNIT = re.compile(r"круглосуточн\w*\s+стационар|стационарное\s+отделение")
_MIXED = re.compile(r"смешанн\w*\s+(?:тип|звено)|(?:поликлиник\w*|амбулатор\w*)\s+(?:со|с)\s+стационар|(?:больниц\w*|госпитал\w*)\s+(?:с|со)\s+поликлиник")
_AMBULATORY_CATEGORIES = {"ambulatory", "polyclinic_adult", "polyclinic_child", "dental", "womens"}


@lru_cache(maxsize=100_000)
def _classify(category: str, kind: str, name: str) -> str:
    kind = kind.lower()
    text = f"{name} {kind}".lower()
    text = re.sub(r"\bдневн\w*\s+стационар\w*", "амбулаторное отделение", text)
    # Структурный тип филиала важнее названия юрлица, например «больница — поликлиника».
    if _OUTPATIENT.search(kind) and not _INPATIENT.search(kind) and not _MIXED.search(kind):
        return "ambulatory"
    if _MIXED.search(text):
        return "mixed"
    outpatient_unit = bool(_OUTPATIENT_UNIT.search(text))
    inpatient_unit = bool(_INPATIENT_UNIT.search(text))
    if outpatient_unit and inpatient_unit:
        return "mixed"
    if outpatient_unit or inpatient_unit:
        return "ambulatory" if outpatient_unit else "hospital"
    outpatient = bool(_OUTPATIENT.search(text))
    inpatient = bool(_INPATIENT.search(text))
    if outpatient and inpatient:
        return "mixed"
    if outpatient or inpatient:
        return "ambulatory" if outpatient else "hospital"
    if category == "hospital":
        return "hospital"
    return "ambulatory" if category in _AMBULATORY_CATEGORIES else "unknown"


def level(facility: dict) -> str:
    declared = facility.get("care_level")
    if declared in LABELS:
        return declared
    return _classify(facility.get("category") or "", facility.get("type") or "",
                     facility.get("name") or "")


def matches(actual: str, selected: str) -> bool:
    return selected == "all" or actual == selected or (
        actual == "mixed" and selected in ("ambulatory", "hospital")
    )


def count(total: int, actual: str, selected: str) -> int:
    if not matches(actual, selected):
        return 0
    if actual != "mixed" or selected in ("all", "mixed"):
        return total
    ambulatory = (3 * total + 2) // 5  # 60%, до ближайшего целого по каждой специальности
    return ambulatory if selected == "ambulatory" else total - ambulatory


def doctors(names: list[str], actual: str, selected: str) -> list[str]:
    amount = count(len(names), actual, selected)
    return names[-amount:] if amount and actual == "mixed" and selected == "hospital" else names[:amount]
