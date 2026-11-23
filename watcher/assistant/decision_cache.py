"""Short-lived in-process cache of LLM *decisions* (split, intent, plan).

Asking the same thing twice must give the same decision (consistency) and must not spend extra
LLM calls (provider rate limits). Only SUCCESSFUL decisions are stored, and only the decision:
watch data is always read live afterwards. The date is part of the key, so "tomorrow" never
outlives the day. (Port of the template's core/decision_cache.py.)
"""

import copy
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import conf, trace

_lock = threading.Lock()
_store = {}


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _key(parts):
    # the IST day, the same clock the planner resolves "tomorrow" on (a UTC server flips 5.5h late)
    return (datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat(),) + tuple(parts)


def get(*parts):
    key = _key(parts)
    with _lock:
        hit = _store.get(key)
        if hit and time.monotonic() - hit[0] < conf.DECISION_CACHE_TTL:
            trace.note_append("cache_hits", str(parts[0]))
            return copy.deepcopy(hit[1])
        _store.pop(key, None)
    return None


def put(*parts, value):
    key = _key(parts)
    with _lock:
        if len(_store) >= conf.DECISION_CACHE_MAX:
            _store.pop(next(iter(_store)))
        _store[key] = (time.monotonic(), copy.deepcopy(value))


def clear():
    with _lock:
        _store.clear()
