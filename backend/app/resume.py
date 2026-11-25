"""
Фоновые загрузки, которые переживают перезапуск сервера.

Загрузки (пригороды, стоматологии) идут на самом сервере и от компьютера пользователя не
зависят: браузер можно закрыть. Но при перезапуске сервера (обновление программы, перезагрузка
VPS) фоновый поток останавливается. Поэтому запущенная загрузка отмечается здесь, а при старте
сервер сам продолжает отмеченные — с того города, где остановился. Отметка снимается, когда
загрузка прошла все города.
"""
from __future__ import annotations

import json
import threading

from .facilities import DATA_DIR

_FILE = DATA_DIR / "cache" / "resume_jobs.json"
_lock = threading.Lock()


def wanted() -> set[str]:
    try:
        return set(json.loads(_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def mark(name: str, on: bool) -> None:
    with _lock:
        cur = wanted()
        cur = cur | {name} if on else cur - {name}
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(sorted(cur)), encoding="utf-8")


def resume_all() -> None:
    """Вызывается при старте сервера: продолжить загрузки, которые не успели закончиться."""
    jobs = wanted()
    if "dental" in jobs:
        from . import dental
        if dental.todo():
            dental.start()
        else:
            mark("dental", False)
    if "suburbs" in jobs:
        from . import territory
        territory.start_suburbs()
