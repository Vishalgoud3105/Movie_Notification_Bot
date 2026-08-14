"""The movies currently being watched (plural), settable from chat, each
finished independently when its own goal fires or it's cancelled.

Config used to come only from .env, fixed at import. A watch set by message
has to change at runtime, so the live values live here and `apply()` pushes
one spec's fields into the modules that read them.

Multiple simultaneous watches share that same single-spec-push machinery:
apply(spec) still only ever pushes ONE spec's fields onto the live globals
bms.py/district.py/messages.py read - run_cycle() in runner.py calls it once
per active spec, in a loop, right before scanning that spec, reusing the
existing filter code completely unchanged rather than teaching every source
module to take an explicit spec argument.

ponytail: apply() assigns module attributes rather than introducing a settings
object every call site would have to be rewritten for. The modules already use
`from .config import *`, so their names are module-level and assignable - the
tests have relied on that from the start.
"""

import datetime as dt
import json
import os
import uuid

from . import bms, config, district, messages, runner, sources
from .config import *

WATCH_FILE = os.environ.get("WATCH_FILE", "watch.json")

# Every module that binds these names at import and must see runtime changes.
_TARGETS = (config, bms, district, sources, runner, messages)


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


def load_all():
    """Every movie being watched right now."""
    return [w for w in _load_raw() if isinstance(w, dict) and w.get("active")]


def load():
    """A single active watch, or None - only for callers that just need "is
    anything active at all" (e.g. the idle check in brain.py). Never use this
    to decide WHICH movie to act on when more than one might be active."""
    watches = load_all()
    return watches[0] if watches else None


def find(title):
    """Best-effort match of a spoken title against active watches - substring,
    case-insensitive. Used to figure out which watch a message like "cancel
    spiderman" refers to when several are active."""
    title = (title or "").strip().lower()
    if not title:
        return None
    for w in load_all():
        if title in (w.get("title") or "").lower():
            return w
    return None


def _push(values):
    """Set the live values everywhere they are read - for exactly ONE spec."""
    for mod in _TARGETS:
        mod.DATES = [d.replace("-", "") for d in values["dates"]]
        mod.FORMAT = values["format"] or ""
        mod.LANGUAGE = values["language"] or ""
        mod.VENUES = [v.strip().lower() for v in values["venues"] or []]
        mod.TIME_FROM = values["time_from"]
        mod.TIME_TO = values["time_to"]
        mod.DISTRICT_URL = values["url"]
        mod.MOVIE_NAME = values["title"]


def apply(spec):
    """Make `spec` the live watch - call once per spec, right before scanning it."""
    _push(spec)
    return spec


def start(spec):
    """Begin watching a new movie, or update one already being watched (its
    `id` is in `spec`) rather than creating a duplicate."""
    spec = dict(spec)
    spec["id"] = spec.get("id") or uuid.uuid4().hex[:8]
    spec["active"] = True
    spec["started"] = spec.get("started") or dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M")

    watches = [w for w in _load_raw() if w.get("id") != spec["id"]]
    watches.append(spec)
    _save(watches)
    _forget_seen(spec["id"])
    return spec


def finish(watch_id=None, reason="found"):
    """Goal reached (or cancelled): stop watching one movie (by id) or every
    active one (id=None). Deactivated rather than deleted, so the last watch
    is still inspectable and the alert that ended it can be explained.

    Always returns a LIST of the specs that were stopped (possibly empty),
    even for a single id - callers format "stopped watching: X" from a list
    either way rather than branching on shape.
    """
    watches = _load_raw()
    stopped = []
    for w in watches:
        if w.get("active") and (watch_id is None or w.get("id") == watch_id):
            w = dict(w)
            w["active"] = False
            w["ended"] = dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M")
            w["ended_reason"] = reason
            stopped.append(w)
    stopped_ids = {w["id"] for w in stopped}
    _save([w for w in watches if w.get("id") not in stopped_ids] + stopped)
    for w in stopped:
        _forget_seen(w["id"])
    return stopped


def _forget_seen(watch_id):
    """Drop remembered shows for JUST this watch, so it starts clean.

    Without this, a re-affirmed watch would inherit its own previous show
    keys and treat genuinely new shows as already-alerted. Namespaced by
    watch id (see run_cycle()'s "<id>|<key>" keys) so clearing one watch's
    history never touches another movie's - a flat clear-everything, correct
    for a single watch, would wrongly wipe an unrelated movie's dedup state.
    """
    from .state import load_state, save_state
    state = load_state()
    prefix = watch_id + "|"
    state["seen"] = [k for k in state.get("seen", []) if not k.startswith(prefix)]
    save_state(state)


def describe():
    """Facts block for the LLM prompts - only things actually known."""
    watches = load_all()
    lines = []
    if watches:
        for w in watches:
            lines.append("Active watch: %s (started %s)"
                         % (messages.pretty_spec(w), w.get("started", "?")))
    else:
        lines.append("No movie is being watched right now.")
    lines.append("Source site: %s" % SITE)
    lines.append("Scan interval: every %d minutes" % (SCAN_EVERY // 60))
    return "\n".join(lines)
