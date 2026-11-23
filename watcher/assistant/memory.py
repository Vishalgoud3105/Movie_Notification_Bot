"""Per-chat conversation memory, in process.

This is what the old bot did not have, and why it looped (23 Nov 2026): "Found X. Which dates?"
stored nothing, so the answer was read alone. One thing is remembered per chat: the clarification currently open - which capability, what was
understood so far, what is still missing, and what the bot asked. The planner is shown it with the next
message, and code (planner.merge_pending) merges the reply into it, so the answer completes the request
whether or not the model cooperates. (The template also keeps the last resolved questions for a decomposer
to rewrite follow-ups; that stage was measured and cut - see planner.py.)

A group chat is one conversation shared by everyone in it. It expires (PENDING_TTL) and a restart forgets
it - the worst case is the bot asks again.
"""

import threading
import time
from dataclasses import dataclass, field

from . import conf

_lock = threading.Lock()
_chats = {}                      # chat_id -> _Chat


@dataclass
class Pending:
    capability: str
    args: dict
    missing: tuple
    question: str                # what the bot asked, shown to the decomposer and the router
    asked: int = 1               # how many times THIS question was asked in a row
    ts: float = field(default_factory=time.time)


@dataclass
class _Chat:
    pending: Pending = None
    ts: float = field(default_factory=time.time)


def _chat(chat_id, create=False):
    c = _chats.get(chat_id)
    if c is None and create:
        if len(_chats) >= conf.MAX_CHATS:
            _chats.pop(min(_chats, key=lambda k: _chats[k].ts))      # forget the stalest chat
        c = _chats[chat_id] = _Chat()
    return c


def get_pending(chat_id):
    with _lock:
        c = _chat(chat_id)
        if not c or not c.pending:
            return None
        if time.time() - c.pending.ts > conf.PENDING_TTL:
            c.pending = None
            return None
        return c.pending


def set_pending(chat_id, capability, args, missing, question):
    """Open (or re-open) a clarification. Returns it, with `asked` counting consecutive repeats of
    the SAME question for the same capability - the loop breaker reads that."""
    with _lock:
        c = _chat(chat_id, create=True)
        prev = c.pending
        same = bool(prev and prev.capability == capability and prev.missing == tuple(missing)
                    and time.time() - prev.ts <= conf.PENDING_TTL)
        c.pending = Pending(capability, dict(args), tuple(missing), question,
                            asked=(prev.asked + 1) if same else 1)
        c.ts = time.time()
        return c.pending


def clear_pending(chat_id):
    """-> True if there was one to drop."""
    with _lock:
        c = _chat(chat_id)
        had = bool(c and c.pending)
        if c:
            c.pending = None
        return had


def reset():
    with _lock:
        _chats.clear()
