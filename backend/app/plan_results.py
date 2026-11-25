"""Ограниченный кэш построенных городских планов для выгрузки из той же версии."""
import gzip
import json
import time
import uuid
from collections import OrderedDict
from threading import Lock

_results = OrderedDict()
_lock = Lock()
_max_age = 8 * 60 * 60
_max_bytes = 64 * 1024 * 1024
_size = 0


def register(plan):
    global _size
    token = uuid.uuid4().hex
    encoded = gzip.compress(json.dumps(plan, ensure_ascii=False).encode("utf-8"), compresslevel=3)
    now = time.monotonic()
    with _lock:
        while _results and (_size + len(encoded) > _max_bytes or next(iter(_results.values()))[0] <= now):
            _, (_, previous) = _results.popitem(last=False)
            _size -= len(previous)
        _results[token] = (now + _max_age, encoded)
        _size += len(encoded)
    return {**plan, "calculation_id": token}


def get(token):
    with _lock:
        expiry, encoded = _results.get(token, (0, None))
    if encoded is None or expiry <= time.monotonic():
        raise KeyError(token)
    return json.loads(gzip.decompress(encoded))
