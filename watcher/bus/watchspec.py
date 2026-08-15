"""The bus routes currently being watched (plural), settable from chat, each
finished independently when its own goal fires or it's cancelled.

WATCH_FILE holds {"watches": [spec, spec, ...]} - multiple routes can be
active at once, each with its own id, scanned and alerted on independently
by bus/runner.py::run_cycle_bus(). A finished watch is dropped from the list
entirely rather than kept inactive-but-present (unlike the movie domain's
history-keeping) - with several routes churning through independently,
accumulating dead entries forever has no payoff and only bloats the file.
"""

import datetime as dt
import json
import os
import uuid

from .config import *

WATCH_FILE = os.environ.get("BUS_WATCH_FILE", "watch_bus.json")


def _load_raw():
    if not os.path.exists(WATCH_FILE):
        return []
    try:
        with open(WATCH_FILE) as f:
            data = json.load(f)
    except (ValueError, OSError):
        return []
    watches = data.get("watches") if isinstance(data, dict) else None
    return watches if isinstance(watches, list) else []


def _save(watches):
    tmp = WATCH_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"watches": watches}, f, indent=1)
    os.replace(tmp, WATCH_FILE)      # atomic, same reason as seen.json


def load_all(chat_id=None):
    """Every route being watched right now - across every chat, unless
    `chat_id` is given, in which case only that chat's own watches (plus any
    legacy watch from before chat scoping existed, which has no chat_id at
    all - treated as visible from anywhere until it's cancelled/replaced,
    rather than orphaned and un-cancelable by everyone).

    Chat-scoping matters here, not just on the reply: a watch started in the
    owner's DM must never show up, alert into, or be cancellable from a group
    the bot is also in, and vice versa - see run_cycle_bus()'s alert send and
    brain.py's cancel/status/modify handling, all of which pass their chat_id
    through to this.
    """
    watches = [w for w in _load_raw() if isinstance(w, dict) and w.get("active")]
    if chat_id is not None:
        watches = [w for w in watches if w.get("chat_id") in (None, chat_id)]
    return watches


def load():
    """A single active watch, or None - only for callers that just need "is
    anything active at all" (e.g. the idle check in brain.py). Never use this
    to decide WHICH route to act on when more than one might be active."""
    watches = load_all()
    return watches[0] if watches else None


def find(chat_id, from_city=None, to_city=None):
    """Best-effort match of a spoken route against `chat_id`'s own active
    watches - substring, case-insensitive. Used to figure out which watch a
    message like "cancel the bangalore one" refers to when several are
    active in the same chat."""
    from_city = (from_city or "").strip().lower()
    to_city = (to_city or "").strip().lower()
    if not (from_city or to_city):
        return None
    for w in load_all(chat_id):
        if ((not from_city or from_city in (w.get("from_city") or ""))
                and (not to_city or to_city in (w.get("to_city") or ""))):
            return w
    return None


def start(spec):
    """Begin watching a route, or update one already being watched (its `id`
    is in `spec`) rather than creating a duplicate.

    `lowest_seen` carries over only when this is the same route+date already
    being watched - i.e. a "modify" that tweaks target_price. Without this
    check, re-affirming an existing watch would silently wipe the lowest
    price already found and alerted on, and the next cycle would re-alert on
    a price the user already knows about. A genuinely different route always
    starts fresh with its own new id.
    """
    spec = dict(spec)
    spec.setdefault("target_price", None)
    spec.setdefault("ac", None)          # "ac" | "non_ac" | None (any)
    spec.setdefault("seat_type", None)   # "sleeper" | "seater" | None (any)
    spec.setdefault("gender", None)      # "male" | "female" | None (any)

    watches = _load_raw()
    current = next((w for w in watches if w.get("id") == spec.get("id")), None) \
        if spec.get("id") else None
    same_route = bool(current) and all(
        current.get(k) == spec.get(k) for k in ("from_city", "to_city", "date"))

    spec["id"] = spec.get("id") or uuid.uuid4().hex[:8]
    spec["lowest_seen"] = current.get("lowest_seen") if same_route else None
    spec["active"] = True
    spec["started"] = (current["started"] if same_route and current
                       else dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M"))

    watches = [w for w in watches if w.get("id") != spec["id"]]
    watches.append(spec)
    _save(watches)
    return spec


def finish(watch_id=None, reason="cancelled", chat_id=None):
    """Stop watching one route (by id) or every active route this chat can
    see (id=None) - "every active route" is scoped by `chat_id` the same way
    load_all() is, so a bare "cancel" typed in a group can never sweep away
    the owner's own DM watch or another group's, only this chat's (plus any
    not-yet-migrated legacy watch with no chat_id at all).

    Always returns a LIST of the specs that were stopped (possibly empty),
    even for a single id - callers format "stopped watching: X" from a list
    either way rather than branching on shape.
    """
    watches = _load_raw()
    stopped = []
    for w in watches:
        if not w.get("active"):
            continue
        if watch_id is not None and w.get("id") != watch_id:
            continue
        if chat_id is not None and w.get("chat_id") not in (None, chat_id):
            continue
        w = dict(w)
        w["active"] = False
        w["ended"] = dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M")
        w["ended_reason"] = reason
        stopped.append(w)
    stopped_ids = {w["id"] for w in stopped}
    remaining = [w for w in watches if w.get("id") not in stopped_ids]
    _save(remaining)
    return stopped


def note_price(watch_id, price):
    """A new cheapest price seen for one watch. Persists it, returns the spec."""
    watches = _load_raw()
    updated = None
    for w in watches:
        if w.get("id") == watch_id:
            w["lowest_seen"] = price
            updated = w
    _save(watches)
    return updated


def describe(chat_id=None):
    """Facts block for the LLM prompts - only things actually known, and only
    this chat's own watches when chat_id is given (onboarding.py calls this
    unscoped, before any chat-specific context exists)."""
    from .messages import pretty_spec
    watches = load_all(chat_id)
    if not watches:
        return "No bus watch is active right now."
    lines = []
    for w in watches:
        lines.append("Active watch: %s (started %s)" % (pretty_spec(w), w.get("started", "?")))
        if w.get("lowest_seen"):
            lines.append("  Lowest price seen so far: ₹%s" % w["lowest_seen"])
    return "\n".join(lines)
