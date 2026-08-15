"""Everything the bus domain says: new-low alerts, status and shift reports."""

import datetime as dt

from .config import *


def pretty_date(date_code):
    """'20260820' -> 'Thursday, 20 Aug'."""
    d = dt.datetime.strptime(date_code, "%Y%m%d")
    return "%s, %d %s" % (d.strftime("%A"), d.day, d.strftime("%b"))


def pretty_spec(spec):
    """Human summary of a bus watch, for confirmations and LLM facts."""
    if not spec:
        return "nothing"
    bits = ["%s -> %s" % (spec.get("from_city", "?").title(), spec.get("to_city", "?").title())]
    if spec.get("date"):
        bits.append("on " + pretty_date(spec["date"]))
    if spec.get("ac"):
        bits.append({"ac": "AC", "non_ac": "Non-AC"}[spec["ac"]])
    if spec.get("seat_type"):
        bits.append(spec["seat_type"].title())
    if spec.get("gender"):
        bits.append("%s seat" % spec["gender"])
    if spec.get("target_price"):
        bits.append("target ₹%s" % spec["target_price"])
    return " · ".join(bits)


def _rating_line(hit):
    """Only private, quality-bar-passing operators ever reach here (see
    sources.py) - showing the number is what makes that filtering visible to
    the user instead of an invisible policy they have to take on faith."""
    if hit.get("rating") is not None:
        return "⭐ %.1f (%d ratings)" % (hit["rating"], hit.get("no_of_ratings") or 0)
    return None


def _links(hit):
    """The direct-to-seats deep link only (verified 8/8 across different
    operators - see abhibus.py's docstring for the field mixup that caused
    earlier failures). The plain search-results fallback link was dropped on
    request - a user who gets this alert wants to book THIS bus, not land on
    a results page and have to find it again."""
    if hit.get("seat_url"):
        return ["🔗 Seats: %s" % hit["seat_url"]]
    return []


def alert_text(hit, previous_low, spec):
    """A new session-low fare. `previous_low` is None on the first hit found."""
    lines = [
        "🚌💰 NEW LOWEST FARE FOUND!",
        "",
        "%s -> %s, %s" % (spec["from_city"].title(), spec["to_city"].title(),
                          pretty_date(spec["date"])),
        "🏷️ ₹%s on %s (%s)" % (hit["price"], hit["operator"], hit["source"]),
        "💺 %s · %s -> %s" % (hit.get("seat_type", "seat"), hit.get("depart", "?"),
                              hit.get("arrive", "?")),
    ]
    rating_line = _rating_line(hit)
    if rating_line:
        lines.append(rating_line)
    if hit.get("seat_no"):
        # only present when a gender/seat-type filter matched a specific
        # seat - the price above is already that seat's own fare, not the
        # bus's generic "from ₹X"
        lines.append("🪑 Seat %s" % hit["seat_no"])
    if hit.get("seats_left"):
        lines.append("🎟️ %s seats left" % hit["seats_left"])
    if previous_low:
        lines.append("⬇️ was ₹%s" % previous_low)
    links = _links(hit)
    if links:
        lines += [""] + links
    return "\n".join(lines)


def target_met_text(hit, spec):
    """The one-shot alert when a target price is reached - the goal, watch ends."""
    links = _links(hit)
    rating_line = _rating_line(hit)
    return "\n".join([
        "✅🚌 TARGET PRICE HIT!",
        "",
        "%s -> %s, %s" % (spec["from_city"].title(), spec["to_city"].title(),
                          pretty_date(spec["date"])),
        "🏷️ ₹%s on %s (%s) - at or under your ₹%s target"
        % (hit["price"], hit["operator"], hit["source"], spec["target_price"]),
    ] + ([rating_line] if rating_line else []) + (
        ["🪑 Seat %s" % hit["seat_no"]] if hit.get("seat_no") else []
    ) + [
        "",
    ] + (links + [""] if links else []) + [
        "I'm done watching this route. Tell me what's next.",
    ])


def shift_report(t):
    """End-of-shift summary: proof the bus watcher is alive and what it saw.

    Mirrors watcher/movies/messages.py::shift_report()'s shape and boundary
    math exactly, bus-flavored content. `t["routes"]` is
    {watch_id: {"route", "lowest", "operator"?, "rating"?, "no_of_ratings"?}}
    - one entry per route active during the shift, not just whichever was
    processed last (see runner.py::run_cycle_bus()) - several can be watched
    at once and each gets its own line, same as the movie side lists every
    watched movie. operator/rating are only present when live_report() built
    this dict from a fresh scan; a real end-of-shift tally only ever tracks
    the lowest price, not which specific bus/operator hit it each time.
    """
    name = t["name"]
    lo, hi = next(((a, b) for n, a, b in SHIFTS if n == name), (0, 0))
    nxt = SHIFTS[(([s[0] for s in SHIFTS].index(name)) + 1) % len(SHIFTS)]

    lines = ["🚌 %s SHIFT REPORT" % name.upper(),
             "🕒 %02d:00-%02d:00 IST · %s" % (lo, hi, pretty_date(t["date"])),
             "",
             "🔁 Checks run: %d%s" % (t["checks"],
                                      " (%s → %s)" % (t["first"], t["last"]) if t["checks"] else ""),
             "📡 AbhiBus reachable: %s" % ("yes ✅" if not t["errors"]
                                           else "%d failed check(s) ⚠️" % t["errors"])]
    lines.append("")
    routes = t.get("routes") or {}
    if not routes:
        lines.append("😴 No bus route being watched right now.")
    else:
        for r in routes.values():
            lines.append("🎯 Watching: %s" % r["route"])
            if r.get("lowest_ever"):
                lines.append("  📉 All-time lowest seen: ₹%s" % r["lowest_ever"])
            if r.get("lowest"):
                tail = (" on %s (⭐ %.1f, %d ratings)" % (r["operator"], r["rating"], r.get("no_of_ratings") or 0)
                        if r.get("rating") is not None else "")
                lines.append("  💰 Lowest seen: ₹%s%s" % (r["lowest"], tail))
                for link in _links(r):
                    lines.append("  " + link)
            else:
                lines.append("  🔒 No fares found yet")
    lines += ["", "⏭️ Next up: %s shift (%02d:00-%02d:00). 👀"
              % (nxt[0], nxt[1], nxt[2])]
    return "\n".join(lines)


def live_report(specs, hits, broken):
    """A shift report describing this very moment, from an already-done scan -
    what "status"/"check"/"update" actually replies with, mirroring
    watcher/movies/messages.py::live_report()'s trick of reusing
    shift_report()'s shape for an on-demand check instead of a flat one-off
    format.

    Groups `hits` by each hit's "_watch_id" tag (set in run_cycle_bus()) so
    a route with no fresh data this cycle shows "no fares found yet" instead
    of borrowing another route's price - the same reasoning the old
    status_text() applied when it filtered to active watch ids, folded in
    here instead of kept as a second, differently-shaped reply function.
    """
    if not specs:
        return ("🚌 No bus route is being watched right now. Say something like "
                "\"watch bus from Hyderabad to Bangalore on 20 Aug\".")

    now = dt.datetime.now(IST)
    shift = shift_at(now)
    name = shift[0] if shift else SHIFTS[-1][0]   # 00:00-07:00: report the shift just ended
    stamp = now.strftime("%I:%M %p").lstrip("0")

    routes = {}
    for spec in specs:
        relevant = [h for h in (hits or []) if h.get("_watch_id") == spec["id"]]
        cheapest = min(relevant, key=lambda h: h["price"]) if relevant else None
        routes[spec["id"]] = {
            "route": pretty_spec(spec),
            "lowest": cheapest["price"] if cheapest else None,
            "operator": cheapest["operator"] if cheapest else None,
            "rating": cheapest.get("rating") if cheapest else None,
            "no_of_ratings": cheapest.get("no_of_ratings") if cheapest else None,
            "seat_url": cheapest.get("seat_url") if cheapest else None,
            # the persistent all-time-low this watch has ever alerted on
            # (watchspec.note_price()) - distinct from "lowest" above, which
            # is only this instant's pooled hits and can be None between scans.
            "lowest_ever": spec.get("lowest_seen"),
        }
    return shift_report({
        "name": name, "date": now.strftime("%Y%m%d"), "checks": 1,
        "first": stamp, "last": stamp, "errors": len(broken), "routes": routes,
    })


