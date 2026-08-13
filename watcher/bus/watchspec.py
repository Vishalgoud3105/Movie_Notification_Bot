"""The bus route currently being watched, settable from chat and reset when done.

Mirrors watcher/movies/watchspec.py's shape (load/save/start/finish/describe,
same atomic-write pattern) but simpler: there is no .env-configured default
route to fall back on the way movies falls back to Spider-Man, so "no watch" is
just None, not a _push()/_TARGETS global-reassignment onto a default spec.
Nothing scans for buses unless a spec is active.
"""

import datetime as dt
import json
import os

from .config import *

WATCH_FILE = os.environ.get("BUS_WATCH_FILE", "watch_bus.json")


def load():
    """The active spec, or None if nothing is being watched."""
    if not os.path.exists(WATCH_FILE):
        return None
    try:
        with open(WATCH_FILE) as f:
            spec = json.load(f)
        return spec if isinstance(spec, dict) and spec.get("active") else None
    except (ValueError, OSError):
        return None


def save(spec):
    tmp = WATCH_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(spec, f, indent=1)
    os.replace(tmp, WATCH_FILE)      # atomic, same reason as seen.json


def start(spec):
    """Begin watching a route.

    `lowest_seen` carries over only when this is the same route+date already
    being watched - i.e. a "modify" that tweaks target_price. Without this
    check, brain.py's modify path (which calls start() again to apply merged
    fields) would silently wipe the lowest price already found and alerted
    on, and the next cycle would re-alert on a price the user already knows
    about. A genuinely different route always starts fresh.
    """
    spec = dict(spec)
    spec.setdefault("target_price", None)
    current = load()
    same_route = bool(current) and all(
        current.get(k) == spec.get(k) for k in ("from_city", "to_city", "date"))
    spec["lowest_seen"] = current.get("lowest_seen") if same_route else None
    spec["active"] = True
    spec["started"] = (current["started"] if same_route
                       else dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M"))
    save(spec)
    return spec


def finish(reason="cancelled"):
    """Stop watching. Deactivated rather than deleted, so it stays inspectable."""
    spec = load()
    if spec:
        spec["active"] = False
        spec["ended"] = dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M")
        spec["ended_reason"] = reason
        save(spec)
    return spec


def note_price(price):
    """A new cheapest price seen this watch. Persists it, returns the updated spec."""
    spec = load()
    if spec:
        spec["lowest_seen"] = price
        save(spec)
    return spec


def describe():
    """Facts block for the LLM prompts - only things actually known."""
    from .messages import pretty_spec
    spec = load()
    if not spec:
        return "No bus watch is active right now."
    lines = ["Active watch: %s" % pretty_spec(spec), "Started: %s" % spec.get("started", "?")]
    if spec.get("lowest_seen"):
        lines.append("Lowest price seen so far: ₹%s" % spec["lowest_seen"])
    return "\n".join(lines)
