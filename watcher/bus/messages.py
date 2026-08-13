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
    if spec.get("target_price"):
        bits.append("target ₹%s" % spec["target_price"])
    return " · ".join(bits)


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
    if hit.get("seats_left"):
        lines.append("🎟️ %s seats left" % hit["seats_left"])
    if previous_low:
        lines.append("⬇️ was ₹%s" % previous_low)
    if hit.get("book_url"):
        lines += ["", "🔗 %s" % hit["book_url"]]
    return "\n".join(lines)


def target_met_text(hit, spec):
    """The one-shot alert when a target price is reached - the goal, watch ends."""
    return "\n".join([
        "✅🚌 TARGET PRICE HIT!",
        "",
        "%s -> %s, %s" % (spec["from_city"].title(), spec["to_city"].title(),
                          pretty_date(spec["date"])),
        "🏷️ ₹%s on %s (%s) - at or under your ₹%s target"
        % (hit["price"], hit["operator"], hit["source"], spec["target_price"]),
        "",
    ] + (["🔗 %s" % hit["book_url"], ""] if hit.get("book_url") else []) + [
        "I'm done watching this route. Tell me what's next.",
    ])


def shift_report(t):
    """End-of-shift summary: proof the bus watcher is alive and what it saw.

    Mirrors watcher/movies/messages.py::shift_report()'s shape and boundary
    math exactly, bus-flavored content.
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
    if t.get("route"):
        lines.append("🎯 Watching: %s" % t["route"])
        lines.append("💰 Lowest seen: ₹%s" % t["lowest"] if t.get("lowest")
                     else "🔒 No fares found yet")
    else:
        lines.append("😴 No bus route being watched right now.")
    lines += ["", "⏭️ Next up: %s shift (%02d:00-%02d:00). 👀"
              % (nxt[0], nxt[1], nxt[2])]
    return "\n".join(lines)


def status_text(spec, hits, broken):
    """On-demand report: what's being watched and the cheapest fare right now."""
    if not spec:
        return "🚌 No bus route is being watched right now. Say something like " \
               "\"watch bus from Hyderabad to Bangalore on 20 Aug\"."
    lines = ["🚌 Watching: %s" % pretty_spec(spec)]
    if spec.get("lowest_seen"):
        lines.append("💰 Lowest seen so far: ₹%s" % spec["lowest_seen"])
    if hits:
        cheapest = min(hits, key=lambda h: h["price"])
        lines.append("📊 Cheapest right now: ₹%s on %s (%s)"
                     % (cheapest["price"], cheapest["operator"], cheapest["source"]))
        if cheapest.get("book_url"):
            lines.append("🔗 %s" % cheapest["book_url"])
    if broken:
        lines.append("⚠️ Could not reach: %s" % ", ".join(broken))
    return "\n".join(lines)
