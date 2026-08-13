"""The bus domain's one full check per cycle. Mirrors watcher/movies/runner.py's
run_cycle()/boot()/answer() shape, including shift reports - same rhythm as
the movie side, just built from bus facts (route, lowest price, reachability)
instead of showtimes.

The one real difference: nothing to scan, and no shift tally to open, unless a
route is actually being watched - there is no default the way movies default
to Spider-Man.
"""

import datetime as dt

from . import sources, watchspec
from .config import *
from .messages import alert_text, pretty_spec, status_text, target_met_text
from .shifts import maybe_report_shift
from ..telegram import send_telegram, wants_report


def boot():
    """Nothing to re-apply at process start - watchspec.load() is read fresh
    every cycle, unlike the movie domain's module-global push. Kept as a
    function so runner.py callers don't need to know that."""
    return watchspec.load()


def run_cycle_bus(state):
    """One check of the active route, or a no-op if nothing is being watched.

    Returns (hits, broken) so a chat reply right after can reuse this scan
    instead of costing the sites an extra request. `state` carries the shift
    tally across cycles, same role as it plays for the movie domain.
    """
    spec = watchspec.load()
    tally = maybe_report_shift(state, watching=bool(spec))

    if not spec:
        return [], []

    if spec["date"] < dt.datetime.now(IST).strftime("%Y%m%d"):
        watchspec.finish("expired")
        print("bus watch expired (travel date passed), no alert")
        return [], []

    hits, broken = sources.scan(spec["from_city"], spec["to_city"], spec["date"])

    if tally:
        tally["checks"] += 1
        tally["errors"] += len(broken)
        tally["route"] = pretty_spec(spec)
        stamp = dt.datetime.now(IST).strftime("%I:%M %p").lstrip("0")
        tally["first"] = tally["first"] or stamp
        tally["last"] = stamp

    if not hits:
        print("bus: no fares found yet for %s -> %s" % (spec["from_city"], spec["to_city"]))
        return hits, broken

    cheapest = min(hits, key=lambda h: h["price"])
    if tally:
        tally["lowest"] = min(cheapest["price"], tally["lowest"] or cheapest["price"])

    target = spec.get("target_price")
    if target and cheapest["price"] <= target:
        send_telegram(target_met_text(cheapest, spec))
        watchspec.finish("target_reached")
        return hits, broken

    previous_low = spec.get("lowest_seen")
    if previous_low is None or cheapest["price"] < previous_low:
        send_telegram(alert_text(cheapest, previous_low, spec))
        watchspec.note_price(cheapest["price"])
    else:
        print("bus: cheapest right now Rs.%s, no new low" % cheapest["price"])

    return hits, broken


def answer(cmd, hits, broken, tally=None):
    """Reply to one chat message about the bus watch. Mirrors movies.runner.answer()."""
    print("bus command from you: %s" % cmd)
    try:
        from .brain import handle
        reply = handle(cmd, hits, broken, tally)
        if reply:
            send_telegram(reply)
            return
    except Exception as e:                      # never let chat break the watcher
        print("bus brain failed, falling back to keywords: %s: %s" % (type(e).__name__, e))

    if wants_report(cmd):
        send_telegram(status_text(watchspec.load(), hits, broken))
    else:
        send_telegram("🚌 Say something like \"watch bus from Hyderabad to "
                      "Bangalore on 20 Aug\" or \"status\".")
