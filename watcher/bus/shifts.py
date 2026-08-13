"""Which shift it is, and filing a bus report when one ends.

Mirrors watcher/movies/shifts.py's boundary logic and "only send when a shift
actually ended" rule, with one deliberate difference: movies always has a
default watch, so it always opens a fresh tally inside a shift window. Bus has
no default - opening a tally with nothing being watched would just mean an
empty "no route" report every few hours. So `watching` gates whether a NEW
tally opens, but flushing an already-open one never does - call this every
cycle regardless of whether a watch is currently active, or a watch that ends
mid-shift leaves its tally stuck in state forever, never sent and never
cleared.
"""

import datetime as dt

from .config import *
from .messages import shift_report
from ..telegram import send_telegram


def maybe_report_shift(state, watching):
    """Send the ended shift's report, then open a tally if a route is watched."""
    now = dt.datetime.now(IST)
    shift = shift_at(now)
    tally = state.get("shift")

    if tally and (tally["name"] != (shift[0] if shift else None)
                  or tally["date"] != now.strftime("%Y%m%d")):
        send_telegram(shift_report(tally))
        print("sent bus %s shift report" % tally["name"])
        tally = None
    if shift and watching and not tally:
        tally = {"name": shift[0], "date": now.strftime("%Y%m%d"), "checks": 0,
                 "first": None, "last": None, "errors": 0, "route": None, "lowest": None}
    state["shift"] = tally
    return tally
