"""One trace line per message: why the bot answered what it answered.

`logs/assistant_trace_<date>.jsonl` carries the request id, chat, the question, the intent, the
capability, the planner's confidence, cache hits, which model answered and the outcome - so a
wrong answer is diagnosed from the trace, not guessed at (template: logging_setup.py). The text
the user typed is truncated; secrets are never passed in here.
"""

import contextvars
import datetime as dt
import json
import os
import threading
import time
import uuid

from . import conf

LOG_DIR = os.environ.get("ASSISTANT_LOG_DIR", "logs")
_current = contextvars.ContextVar("assistant_trace", default=None)
_lock = threading.Lock()
_pruned_on = [None]


def begin(chat_id, query):
    rec = {"request_id": uuid.uuid4().hex[:10], "chat": str(chat_id)[-6:], "query": str(query)[:120],
           "t0": time.perf_counter()}
    return _current.set(rec)


def note(**fields):
    rec = _current.get()
    if rec is not None:
        rec.update(fields)


def note_append(key, value):
    rec = _current.get()
    if rec is not None:
        rec.setdefault(key, []).append(value)


def end(token, **fields):
    rec = _current.get()
    _current.reset(token)
    if rec is None:
        return
    rec.update(fields)
    rec["ms"] = round((time.perf_counter() - rec.pop("t0")) * 1000)
    rec["ts"] = dt.datetime.now().isoformat(timespec="seconds")
    try:
        _write(rec)
    except OSError:
        pass            # the trace is diagnostics; it must never break a reply


def _write(rec):
    os.makedirs(LOG_DIR, exist_ok=True)
    today = dt.date.today()
    path = os.path.join(LOG_DIR, "assistant_trace_%s.jsonl" % today.isoformat())
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if _pruned_on[0] != today:                  # retention: once a day is plenty
            _pruned_on[0] = today
            cutoff = today - dt.timedelta(days=conf.TRACE_RETENTION_DAYS)
            for name in os.listdir(LOG_DIR):
                if name.startswith("assistant_trace_") and name[16:26] < cutoff.isoformat():
                    try:
                        os.remove(os.path.join(LOG_DIR, name))
                    except OSError:
                        pass
