"""Which shift it is, and filing a report when one ends."""

import datetime as dt

from .config import *
from .messages import shift_report
from .telegram import send_telegram


def maybe_report_shift(state):
    """Send the ended shift's report, then open a tally for the current shift.

    No network unless a shift actually ended, so --serve can call this every few
    seconds and file the report on the boundary rather than up to a scan later.
    """
    now = dt.datetime.now(IST)
    shift = shift_at(now)
    tally = state.get("shift")

    # A shift ended if the tally we carry belongs to a different shift or day.
    if tally and (tally["name"] != (shift[0] if shift else None)
                  or tally["date"] != now.strftime("%Y%m%d")):
        send_telegram(shift_report(tally))
        print("sent %s shift report" % tally["name"])
        tally = None
    if shift and not tally:
        tally = {"name": shift[0], "date": now.strftime("%Y%m%d"), "checks": 0,
                 "first": None, "last": None, "errors": 0, "found": {}, "bookable": None}
    state["shift"] = tally
    return tally
