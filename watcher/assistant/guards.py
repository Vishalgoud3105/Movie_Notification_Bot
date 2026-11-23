"""Deterministic safety guards that run BEFORE any LLM call.

Deny-lists for safety only: they never decide what a message MEANS (that is the router and the
planner). The template's action guard is deliberately absent: starting and stopping a watch is this
bot's whole job, so mutation is a capability, scoped to the chat that asked.
"""

import re
import threading
import time
from collections import deque

from . import conf

_I = re.IGNORECASE
INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b", _I),
    re.compile(r"\b(?:what\s+is|reveal|show|print|display|output|tell\s+me|repeat|give\s+me)\s+(?:your\s+)?"
               r"(?:system|initial|developer)?\s*(?:system|initial|developer)?\s*(?:prompt|instructions)\b", _I),
    re.compile(r"\b(?:reveal|show|print|leak|give\s+me)\s+(?:your\s+)?(?:api\s*key|secret|token|password)s?\b", _I),
    re.compile(r"\b(?:dan\s+mode|jailbreak|jailbroken|unrestricted\s+mode|developer\s+mode\s+enabled)\b", _I),
    re.compile(r"\bforget\s+(?:all\s+)?(?:your|the)\s+(?:rules|instructions)\b", _I),
    re.compile(r"\b(?:bypass|disable|disregard|override)\s+(?:all\s+)?(?:safety|security|content)\s+"
               r"(?:filters?|guidelines?|policies|rules?)\b", _I),
]


def sanitize(text):
    """Strip null bytes and control characters (keeps newline/tab), cap the length."""
    if not text:
        return ""
    cleaned = str(text).replace("\x00", "")
    cleaned = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned).strip()
    return cleaned[:conf.MAX_MESSAGE_CHARS]


def is_prompt_injection(text):
    return bool(text) and any(p.search(text) for p in INJECTION_PATTERNS)


_hits = {}                       # chat_id -> deque of timestamps
_lock = threading.Lock()


def allow(chat_id):
    """Per-chat rolling-minute throttle. False = too many messages, do not call any LLM."""
    now = time.monotonic()
    with _lock:
        q = _hits.setdefault(chat_id, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= conf.THROTTLE_PER_MIN:
            return False
        q.append(now)
        return True


def reset_throttle():
    with _lock:
        _hits.clear()
