"""The thing currently being watched, settable from chat and reset when done.

Config used to come only from .env, fixed at import. A watch set by message has
to change at runtime, so the live values live here and `apply()` pushes them
into the modules that read them.

ponytail: apply() assigns module attributes rather than introducing a settings
object every call site would have to be rewritten for. The modules already use
`from .config import *`, so their names are module-level and assignable - the
tests have relied on that from the start.
"""

import datetime as dt
import json
import os

from . import bms, config, district, messages, runner, sources
from .config import *

WATCH_FILE = os.environ.get("WATCH_FILE", "watch.json")

# Where .env-configured defaults live, so a cancel/reset can restore them.
_DEFAULTS = {
    "title": MOVIE_NAME,
    "url": DISTRICT_URL,
    "city": None,
    "dates": list(DATES),
    "format": FORMAT,
    "language": LANGUAGE,
    "venues": list(VENUES),
    "time_from": TIME_FROM,
    "time_to": TIME_TO,
}

# Every module that binds these names at import and must see runtime changes.
_TARGETS = (config, bms, district, sources, runner, messages)


def load():
    """The active spec, or None if we are on the .env defaults."""
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


def _push(values):
    """Set the live values everywhere they are read."""
    for mod in _TARGETS:
        mod.DATES = [d.replace("-", "") for d in values["dates"]]
        mod.FORMAT = values["format"] or ""
        mod.LANGUAGE = values["language"] or ""
        mod.VENUES = [v.strip().lower() for v in values["venues"] or []]
        mod.TIME_FROM = values["time_from"]
        mod.TIME_TO = values["time_to"]
        mod.DISTRICT_URL = values["url"]
        mod.MOVIE_NAME = values["title"]


def apply(spec=None):
    """Make `spec` (or the stored one, or the .env defaults) the live watch."""
    spec = spec or load()
    _push(spec if spec else _DEFAULTS)
    return spec


def start(spec):
    """Begin watching a new spec. Clears what was seen for the previous one."""
    spec = dict(spec)
    spec["active"] = True
    spec["started"] = dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M")
    save(spec)
    apply(spec)
    _forget_seen()
    return spec


def finish(reason="found"):
    """Goal reached (or cancelled): stop watching and be ready for a new show.

    Deactivated rather than deleted, so the last watch is still inspectable and
    the alert that ended it can be explained.
    """
    spec = load()
    if spec:
        spec["active"] = False
        spec["ended"] = dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M")
        spec["ended_reason"] = reason
        save(spec)
    apply(None)          # back to the .env defaults
    _forget_seen()
    return spec


def _forget_seen():
    """Drop remembered shows so a new watch starts clean.

    Without this, a new watch inherits the previous show's keys and could treat
    genuinely new shows as already-alerted.
    """
    from .state import load_state, save_state
    state = load_state()
    state["seen"] = []
    state["shift"] = None
    save_state(state)


def describe():
    """Facts block for the LLM prompts - only things actually known."""
    spec = load()
    lines = []
    if spec:
        lines.append("Active watch: %s" % messages.pretty_spec(spec))
        lines.append("Started: %s" % spec.get("started", "?"))
    else:
        lines.append("No movie is being watched right now.")
    lines.append("Source site: %s" % SITE)
    lines.append("Scan interval: every %d minutes" % (SCAN_EVERY // 60))
    return "\n".join(lines)
