"""Which shift it is, and filing a report when one ends."""

import datetime as dt

from .config import *
from .messages import shift_report
from ..telegram import send_telegram


def maybe_report_shift(state, watching):
    """Send the ended shift's report, then open a tally if a movie is watched.

    No network unless a shift actually ended, so --serve can call this every few
    seconds and file the report on the boundary rather than up to a scan later.

    `watching` gates whether a NEW tally opens - there is no standing default
    movie to report on anymore (see run_cycle()), so nothing should silently
    "watch" the last .env-configured film just because a shift is in progress.
    Flushing an already-open tally never depends on `watching`: a watch that
    finishes mid-shift must still get its final report sent, not have it stuck
    forever - same reasoning as watcher/bus/shifts.py's identical parameter.
    """
    now = dt.datetime.now(IST)
    shift = shift_at(now)
    tally = state.get("shift")

    # A shift ended if the tally we carry belongs to a different shift or day.
    if tally and (tally["name"] != (shift[0] if shift else None)
                  or tally["date"] != now.strftime("%Y%m%d")):
        # A watch cancelled (or its goal reached) partway through the shift
        # stays in tally["watches"] until now - run_cycle() only ever
        # adds/updates entries for watches still in watchspec.load_all() each
        # cycle, it never removes one that dropped out. Without this prune,
        # the report at shift-end would still list it as if still watched.
        # Imported here, not at module level: watchspec imports runner,
        # which imports this module - a top-level import here would cycle.
        from . import watchspec
        still_active = {w["id"] for w in watchspec.load_all()}
        tally["watches"] = {wid: w for wid, w in tally["watches"].items() if wid in still_active}
        send_telegram(shift_report(tally))
        print("sent %s shift report" % tally["name"])
        tally = None
    if shift and watching and not tally:
        # "watches" is keyed by watch id -> {"title", "found": {date_code: count}},
        # built fresh each cycle in run_cycle() - the shift report lists every
        # movie active during the shift, not just one.
        tally = {"name": shift[0], "date": now.strftime("%Y%m%d"), "checks": 0,
                 "first": None, "last": None, "errors": 0, "watches": {}, "bookable": None}
    state["shift"] = tally
    return tally
