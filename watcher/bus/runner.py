"""The bus domain's one full check per cycle. Mirrors watcher/movies/runner.py's
run_cycle()/boot()/answer() shape, including shift reports - same rhythm as
the movie side, just built from bus facts (route, lowest price, reachability)
instead of showtimes.

Loops over every active route each cycle (watchspec.load_all()) rather than a
single one - multiple routes can be watched at once, each scanned and
alerted on independently. The one real difference from movies: nothing to
scan, and no shift tally to open, unless at least one route is being
watched - there is no default the way movies used to default to Spider-Man.
"""

import datetime as dt

from . import sources, watchspec
from .config import *
from .messages import alert_text, pretty_spec, status_text, target_met_text
from .shifts import maybe_report_shift
from ..telegram import reply_to, send_telegram, wants_report


def boot():
    """Nothing to re-apply at process start - watchspec.load_all() is read
    fresh every cycle, unlike the movie domain's module-global push. Kept as
    a function so runner.py callers don't need to know that."""
    watches = watchspec.load_all()
    for w in watches:
        print("resuming bus watch: %s" % pretty_spec(w))
    return watches


def run_cycle_bus(state):
    """One check of every active route, or a no-op if nothing is watched.

    Returns (hits, broken) - hits from every route pooled into one flat list,
    broken likewise - so a chat reply right after can reuse this scan instead
    of costing the sites an extra request. `state` carries the shift tally
    across cycles, same role as it plays for the movie domain; the tally's
    "route"/"lowest" summary fields reflect whichever route was processed
    last in a given cycle when several are active - a deliberate
    simplification, the shift report stays a rough proof-of-life signal, not
    a full per-route breakdown (status/cancel already list every route by
    name, see messages.status_text()).
    """
    specs = watchspec.load_all()
    tally = maybe_report_shift(state, watching=bool(specs))

    if not specs:
        return [], []

    all_hits, all_broken = [], []
    today = dt.datetime.now(IST).strftime("%Y%m%d")

    for spec in specs:
        if spec["date"] < today:
            watchspec.finish(spec["id"], "expired")
            print("bus watch expired (travel date passed), no alert: %s -> %s"
                  % (spec["from_city"], spec["to_city"]))
            continue

        hits, broken = sources.scan(spec["from_city"], spec["to_city"], spec["date"])
        all_hits += hits
        all_broken += broken

        if tally:
            tally["checks"] += 1
            tally["errors"] += len(broken)
            tally["route"] = pretty_spec(spec)
            stamp = dt.datetime.now(IST).strftime("%I:%M %p").lstrip("0")
            tally["first"] = tally["first"] or stamp
            tally["last"] = stamp

        if not hits:
            print("bus: no fares found yet for %s -> %s" % (spec["from_city"], spec["to_city"]))
            continue

        cheapest = min(hits, key=lambda h: h["price"])
        if tally:
            tally["lowest"] = min(cheapest["price"], tally["lowest"] or cheapest["price"])

        target = spec.get("target_price")
        if target and cheapest["price"] <= target:
            send_telegram(target_met_text(cheapest, spec))
            watchspec.finish(spec["id"], "target_reached")
            continue

        previous_low = spec.get("lowest_seen")
        if previous_low is None or cheapest["price"] < previous_low:
            send_telegram(alert_text(cheapest, previous_low, spec))
            watchspec.note_price(spec["id"], cheapest["price"])
        else:
            print("bus: cheapest right now Rs.%s, no new low (%s -> %s)"
                  % (cheapest["price"], spec["from_city"], spec["to_city"]))

    return all_hits, all_broken


def answer(chat_id, cmd, hits, broken, tally=None):
    """Reply to one chat message about bus watches - to `chat_id` only, never
    broadcast (an alert/shift report is a different, proactive path that
    still uses send_telegram() to reach every known chat)."""
    print("bus command from you: %s" % cmd)
    try:
        from .brain import handle
        reply = handle(cmd, hits, broken, tally)
        if reply:
            reply_to(chat_id, reply)
            return
    except Exception as e:                      # never let chat break the watcher
        print("bus brain failed, falling back to keywords: %s: %s" % (type(e).__name__, e))

    if wants_report(cmd):
        reply_to(chat_id, status_text(watchspec.load_all(), hits, broken))
    else:
        reply_to(chat_id, "🚌 Say something like \"watch bus from Hyderabad to "
                         "Bangalore on 20 Aug\" or \"status\".")
