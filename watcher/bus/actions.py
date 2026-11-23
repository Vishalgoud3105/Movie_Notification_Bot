"""What the assistant DOES for bus watches: start/change, status, stop.

Executors behind `watch_bus`, `show_status` and `stop_watch` (watcher/assistant/capabilities.py).
The business rules that used to live in brain.py - refuse an unbookable city up front, refuse a
date in the past, de-duplicate the same route+date - with the routing removed: which capability
runs is the assistant pipeline's decision, and the chat id always comes from the server.
"""

import datetime as dt
import re

from . import watchspec
from .config import *
from .messages import live_report, pretty_date, pretty_spec
from ..assistant.outcomes import Outcome
from ..assistant.registry import ExecResult

_EDITABLE = ("date", "target_price", "ac", "seat_type", "gender")


def got_text(args):
    parts = []
    if args.get("from_city") and args.get("to_city"):
        parts.append("the route (%s -> %s)" % (args["from_city"].title(), args["to_city"].title()))
    if args.get("date"):
        parts.append("the date (%s)" % args["date"])
    if args.get("target_price"):
        parts.append("a target of ₹%s" % args["target_price"])
    return "Got %s. " % " and ".join(parts) if parts else ""


def refuse_unresolvable(from_city, to_city):
    """None if the route looks watchable, else a reply explaining why not.

    Checked against AbhiBus's real city data before a watch starts - without this, an unbookable
    city (or a state name typed as a city) only surfaces 10 minutes later during a scan,
    misreported as "AbhiBus unreachable" instead of the actual problem.
    """
    from . import abhibus
    for city in (from_city, to_city):
        _id, note = abhibus.resolve(city)
        if note == "no-direct-hub":
            return "AbhiBus doesn't sell direct tickets from \"%s\" - try a nearby larger city instead." % city
        if note == "not-found":
            return "I couldn't find \"%s\" as a city. Check the spelling, or it may not be a served location." % city
        # note is None (found, or a transient lookup failure) - don't block on a network hiccup
        # here; the real scan will just retry it later.
    return None


def _route_matches(w, text):
    """'bangalore', 'hyderabad to bangalore' and 'hyd bangalore bus' all name the route hyd -> bangalore."""
    words = [t for t in re.findall(r"[a-z]+", (text or "").lower()) if t not in ("to", "from", "bus", "the", "one", "route", "watch")]
    return bool(words) and all(t in (w.get("from_city") or "") or t in (w.get("to_city") or "") for t in words)


def _change_target(chat_id, args):
    active = watchspec.load_all(chat_id)
    named = [w for w in active
             if args.get("from_city") and args.get("to_city")
             and w.get("from_city") == args["from_city"].lower() and w.get("to_city") == args["to_city"].lower()]
    if named:
        return named[0], None
    if len(active) == 1 and not (args.get("from_city") and args.get("to_city")):
        return active[0], None
    if active and (len(active) > 1 or args.get("from_city")):
        routes = "; ".join("%s -> %s" % (w["from_city"].title(), w["to_city"].title()) for w in active)
        return None, ExecResult(Outcome.NEEDS_CLARIFICATION,
                                "Which route should I change? You're watching: %s." % routes,
                                pending={"args": {k: v for k, v in args.items() if v}, "missing": ["from_city", "to_city"]})
    return None, None


def watch(chat_id, args):
    """Start watching a bus route+date, or change one already being watched."""
    if args.get("change_existing"):
        current, ask = _change_target(chat_id, args)
        if ask:
            return ask
        if current:
            updated = dict(current)
            for k in _EDITABLE:
                if args.get(k) not in (None, ""):
                    updated[k] = args[k].replace("-", "") if k == "date" else args[k]
            watchspec.start(updated)
            return ExecResult(Outcome.SUCCESS, "✏️ Updated: %s\n\nStill checking AbhiBus every %d minutes."
                              % (pretty_spec(updated), SCAN_EVERY // 60))

    from_city = (args.get("from_city") or "").strip()
    to_city = (args.get("to_city") or "").strip()
    date = args.get("date")
    if not (from_city and to_city):
        return ExecResult(Outcome.NEEDS_CLARIFICATION,
                          "%sWhich route should I watch? Give me a \"from\" and \"to\" city." % got_text(args),
                          pending={"args": args, "missing": ["from_city", "to_city"]})
    if not date:
        return ExecResult(Outcome.NEEDS_CLARIFICATION,
                          "Found the route (%s -> %s). Which date should I watch?" % (from_city.title(), to_city.title()),
                          pending={"args": args, "missing": ["date"]})
    if from_city.lower() == to_city.lower():
        return ExecResult(Outcome.NEEDS_CLARIFICATION,
                          "That's the same city twice - where are you actually travelling to?",
                          pending={"args": {k: v for k, v in args.items() if v and k != "to_city"}, "missing": ["to_city"]})

    date_code = date.replace("-", "")
    if date_code < dt.datetime.now(IST).strftime("%Y%m%d"):
        # Caught here, not left to run_cycle_bus's own expiry check - a watch that starts
        # already-expired would confirm "Watching..." and then silently vanish at the next scan.
        try:
            when = pretty_date(date_code)
        except ValueError:
            when = date
        return ExecResult(Outcome.NEEDS_CLARIFICATION, "%s has already passed - what date did you actually mean?" % when,
                          pending={"args": {k: v for k, v in args.items() if v and k != "date"}, "missing": ["date"]})

    target_price = args.get("target_price")
    if target_price is not None:
        try:
            target_price = float(target_price)
        except (TypeError, ValueError):
            # an LLM value is a trust boundary: a malformed price must not wedge run_cycle_bus's
            # `cheapest["price"] <= target` comparison every cycle forever
            return ExecResult(Outcome.NEEDS_CLARIFICATION,
                              "I didn't understand \"%s\" as a price - send just a number." % target_price,
                              pending={"args": {k: v for k, v in args.items() if v and k != "target_price"}, "missing": []})

    ac = args.get("ac") if args.get("ac") in ("ac", "non_ac") else None
    seat_type = args.get("seat_type") if args.get("seat_type") in ("sleeper", "seater") else None
    gender = args.get("gender") if args.get("gender") in ("male", "female") else None

    refusal = refuse_unresolvable(from_city, to_city)
    if refusal:
        return ExecResult(Outcome.NEEDS_CLARIFICATION, refusal,
                          pending={"args": {k: v for k, v in args.items() if v and k not in ("from_city", "to_city")},
                                   "missing": ["from_city", "to_city"]})

    new = {"from_city": from_city.lower(), "to_city": to_city.lower(), "date": date_code,
           "target_price": target_price, "ac": ac, "seat_type": seat_type, "gender": gender, "chat_id": chat_id}
    # the same route+date again reinforces the existing entry (also covers "same route, new seat
    # filter"), scoped to THIS chat's own watches
    existing = next((w for w in watchspec.load_all(chat_id)
                     if w.get("from_city") == new["from_city"] and w.get("to_city") == new["to_city"]
                     and w.get("date") == new["date"]), None)
    if existing:
        new["id"] = existing["id"]
    watchspec.start(new)

    goal = ("I'll alert once a fare hits ₹%s or under, then stop." % new["target_price"] if new["target_price"]
            else "I'll alert every time I see a new lowest price.")
    filters = ", ".join(f for f in ({"ac": "AC only", "non_ac": "Non-AC only"}.get(ac),
                                    seat_type and ("%s only" % seat_type),
                                    gender and ("%s seats only" % gender)) if f)
    return ExecResult(Outcome.SUCCESS,
                      "🚌 Watching: %s -> %s on %s%s\n\n%s I check AbhiBus every %d minutes. Say \"status\" any time."
                      % (from_city.title(), to_city.title(), date, "\nFilters: %s" % filters if filters else "",
                         goal, SCAN_EVERY // 60))


def status(chat_id, scan):
    """-> report text, or None when this chat has no bus watch."""
    specs = watchspec.load_all(chat_id)
    if not specs:
        return None
    hits, broken = scan or ([], [])
    return live_report(specs, hits, broken)


def stop(chat_id, target=None):
    """-> (stopped watches, matched). A named target that matches nothing stops nothing."""
    if target:
        named = [w for w in watchspec.load_all(chat_id) if _route_matches(w, target)]
        if not named:
            return [], False
        return watchspec.finish(named[0]["id"], "cancelled", chat_id=chat_id), True
    return watchspec.finish(None, "cancelled", chat_id=chat_id), True


def describe_active(chat_id):
    return ["%s -> %s" % (w["from_city"].title(), w["to_city"].title()) for w in watchspec.load_all(chat_id)]
