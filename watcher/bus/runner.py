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
from .messages import alert_text, live_report, pretty_spec, target_met_text
from .shifts import maybe_report_shift
from ..telegram import reply_to, send_telegram, wants_report


def _send_to_owning_chat(spec, text):
    """A new-low/target-met alert reaches only the chat that started this
    watch - never every chat the bot is in. This was the actual reported
    leak: watching a bus route from the owner's DM was alerting into every
    group too, because alerts used to go through send_telegram() (broadcast)
    unconditionally. Falls back to a broadcast only for a watch persisted
    before chat scoping existed (no "chat_id" field at all) so an
    already-running pre-migration watch doesn't just go silent."""
    chat_id = spec.get("chat_id")
    if chat_id is not None:
        reply_to(chat_id, text)
    else:
        send_telegram(text)


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
    across cycles, same role as it plays for the movie domain; the tally
    tracks every route active during the shift, keyed by watch id, same as
    the movie side's per-movie breakdown (see messages.shift_report()).

    Each hit is tagged with the watch id it came from ("_watch_id"). Between
    scheduled scans, "status" (messages.live_report()) reuses whatever this
    function last returned - without the tag, cancelling a route and
    starting a new one on a different date would show the OLD route's
    leftover fares as if they belonged to the new watch until the next scan
    overwrote them. live_report() groups hits by that tag for exactly this
    reason.
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

        hits, broken = sources.scan(spec["from_city"], spec["to_city"], spec["date"],
                                    ac=spec.get("ac"), seat_type=spec.get("seat_type"),
                                    gender=spec.get("gender"))
        for h in hits:
            h["_watch_id"] = spec["id"]
        all_hits += hits
        all_broken += broken

        if tally:
            tally["checks"] += 1
            tally["errors"] += len(broken)
            stamp = dt.datetime.now(IST).strftime("%I:%M %p").lstrip("0")
            tally["first"] = tally["first"] or stamp
            tally["last"] = stamp
            entry = tally["routes"].setdefault(spec["id"], {"route": None, "lowest": None})
            entry["route"] = pretty_spec(spec)     # kept fresh - a "modify" mid-shift changes it

        if not hits:
            print("bus: no fares found yet for %s -> %s" % (spec["from_city"], spec["to_city"]))
            continue

        cheapest = min(hits, key=lambda h: h["price"])
        if tally:
            entry = tally["routes"][spec["id"]]
            entry["lowest"] = min(cheapest["price"], entry["lowest"]) if entry["lowest"] else cheapest["price"]

        target = spec.get("target_price")
        if target and cheapest["price"] <= target:
            _send_to_owning_chat(spec, target_met_text(cheapest, spec))
            watchspec.finish(spec["id"], "target_reached")
            continue

        previous_low = spec.get("lowest_seen")
        if previous_low is None or cheapest["price"] < previous_low:
            _send_to_owning_chat(spec, alert_text(cheapest, previous_low, spec))
            watchspec.note_price(spec["id"], cheapest["price"])
        else:
            print("bus: cheapest right now Rs.%s, no new low (%s -> %s)"
                  % (cheapest["price"], spec["from_city"], spec["to_city"]))

    return all_hits, all_broken


def answer(chat_id, cmd, hits, broken, tally=None):
    """Reply to one chat message about bus watches - to `chat_id` only, never
    broadcast. A price alert is likewise scoped to whichever chat started
    that watch (see _send_to_owning_chat()); only the shift report still
    broadcasts to every known chat - it summarizes every route regardless of
    which chat owns it, not any one chat's own state."""
    print("bus command from you: %s" % cmd)
    try:
        from .brain import handle
        reply = handle(cmd, chat_id, hits, broken, tally)
        if reply:
            reply_to(chat_id, reply)
            return
    except Exception as e:                      # never let chat break the watcher
        print("bus brain failed, falling back to keywords: %s: %s" % (type(e).__name__, e))

    if wants_report(cmd):
        reply_to(chat_id, live_report(watchspec.load_all(chat_id), hits, broken))
    else:
        reply_to(chat_id, "🚌 Say something like \"watch bus from Hyderabad to "
                         "Bangalore on 20 Aug\" or \"status\".")
