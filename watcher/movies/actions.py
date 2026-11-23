"""What the assistant DOES for movie watches: start/change, status, stop.

These are the executors behind the `watch_movie`, `show_status` and `stop_watch` capabilities
(watcher/assistant/capabilities.py). They hold the business rules that used to live in brain.py -
resolve the title on District, refuse a live event, de-duplicate, scope everything to the asking
chat - minus all the routing, which is now the assistant pipeline's job.

Every function takes the chat id from the server-built Context; no argument can name another
chat. Results are built by code from real data, never by a model.
"""

import re

from . import search, watchspec
from .config import *
from .messages import live_report, pretty_date, pretty_spec
from ..assistant.outcomes import Outcome
from ..assistant.registry import ExecResult

# Words that only ever name a screen/seat format. A "title" made ONLY of these is a format the model misfiled
# (live matrix: "infinity vision 4dx3d at irrum manzil" became a watch titled "Infinity Vision 4Dx3D"). A prompt
# rule was not enough; this is the guarantee. # ponytail: "ice"/"gold" are left out - they are also real titles.
_FORMAT_ONLY = frozenset("4dx 4dx3d 3d 2d imax dolby atmos screenx mx4d epiq infinity vision icity luxe pxl onyx insignia "
                         "playhouse superplex".split())

# the fields a "change my watch" request may overwrite on an existing watch
_EDITABLE = ("dates", "format", "language", "venues", "time_from", "time_to", "seat_category", "city")


def got_text(args):
    """'Got the dates (Sat, 26 Dec) and venue irrum manzil. ' - tells the user what was kept."""
    parts = []
    if args.get("title"):
        parts.append("the movie (%s)" % args["title"])
    if args.get("dates"):
        parts.append("the dates (%s)" % ", ".join(pretty_date(d.replace("-", "")) for d in args["dates"]))
    for key, label in (("venues", "venue"), ("format", "format")):
        if args.get(key):
            v = args[key]
            parts.append("%s %s" % (label, ", ".join(v) if isinstance(v, list) else v))
    return "Got %s. " % " and ".join(parts) if parts else ""


def _named(chat_id, title):
    """This chat's own watches whose title contains `title` (case-insensitive)."""
    t = (title or "").strip().lower()
    return [w for w in watchspec.load_all(chat_id) if t and t in (w.get("title") or "").lower()]


def _change_target(chat_id, args):
    """-> (watch | None, clarification ExecResult | None) for a 'change my watch' request."""
    active = watchspec.load_all(chat_id)
    named = _named(chat_id, args.get("title"))
    if named:
        return named[0], None
    if len(active) == 1 and not args.get("title"):
        return active[0], None
    if len(active) > 1 or (active and args.get("title")):
        names = "; ".join(w.get("title") or "?" for w in active)
        return None, ExecResult(
            Outcome.NEEDS_CLARIFICATION,
            "Which movie should I change? You're watching: %s." % names,
            pending={"args": {k: v for k, v in args.items() if v and k != "title"}, "missing": ["title"]})
    return None, None                                  # nothing to change: treat as a new watch


def watch(chat_id, args):
    """Start watching a movie, or change one already being watched."""
    title = (args.get("title") or "").strip()
    words = re.findall(r"[a-z0-9]+", title.lower())
    if words and all(w in _FORMAT_ONLY for w in words):
        args, title = dict(args, title="", format=args.get("format") or title), ""
    if args.get("change_existing"):
        current, ask = _change_target(chat_id, args)
        if ask:
            return ask
        if current:
            updated = dict(current)
            updated.update({k: args[k] for k in _EDITABLE if args.get(k)})
            watchspec.start(updated)
            return ExecResult(Outcome.SUCCESS, "✏️ Updated: %s\n\nStill scanning every %d minutes. Say \"status\" any time."
                              % (pretty_spec(updated), SCAN_EVERY // 60))

    if not title:
        return ExecResult(Outcome.NEEDS_CLARIFICATION,
                          "%sWhich movie or show should I watch? Give me a name and I'll find it." % got_text(args),
                          pending={"args": args, "missing": ["title"]})
    if search.looks_like_event(title):
        return ExecResult(Outcome.UNSUPPORTED_QUERY,
                          "That looks like a live event rather than a film. I can only watch movie showtimes right "
                          "now - District's event pages use a different structure I haven't taught the watcher to read yet.")

    city = (args.get("city") or HOME_CITY).lower()
    assumed_city = not args.get("city")
    hit = search.find(title, city)
    if not hit:
        # keep everything except the title that didn't resolve: retyping the right name must not
        # mean giving the dates and the venue again
        return ExecResult(Outcome.NEEDS_CLARIFICATION,
                          "I couldn't find \"%s\" on District. Check the spelling, or it may not be listed yet." % title,
                          pending={"args": {k: v for k, v in args.items() if v and k != "title"}, "missing": ["title"]})

    dates = args.get("dates") or []
    if not dates:
        return ExecResult(Outcome.NEEDS_CLARIFICATION,
                          "Found %s. Which dates should I watch?" % hit["title"],
                          pending={"args": dict(args, title=hit["title"]), "missing": ["dates"]})

    new = {
        "title": hit["title"], "url": hit["url"], "city": hit["city"] or args.get("city"),
        "movie_id": hit["movie_id"], "dates": dates,
        "format": args.get("format") or "", "language": args.get("language") or "",
        "venues": args.get("venues") or [],
        "time_from": args.get("time_from") or "00:00", "time_to": args.get("time_to") or "23:59",
        # free text: every cinema names its own seat tiers, matched as a substring at report time
        "seat_category": (args.get("seat_category") or "").strip().lower(),
        "chat_id": chat_id,
    }
    # Watching the same movie+dates again reinforces the existing entry instead of starting a
    # duplicate that double-scans it - scoped to THIS chat's watches only.
    existing = next((w for w in watchspec.load_all(chat_id)
                     if w.get("movie_id") == hit["movie_id"] and set(w.get("dates", [])) == set(dates)), None)
    if existing:
        new["id"] = existing["id"]
    watchspec.start(new)
    note = ("\n\n(You didn't name a city, so I assumed %s - say \"watch it in Mumbai\" to change that.)"
            % city.title()) if assumed_city else ""
    return ExecResult(Outcome.SUCCESS,
                      "🎯 Watching: %s\n\nI'll scan every %d minutes and ping you the moment it appears. "
                      "Say \"status\" any time.%s" % (pretty_spec(new), SCAN_EVERY // 60, note))


def status(chat_id, scan):
    """-> report text, or None when this chat has no movie watch."""
    if not watchspec.load_all(chat_id):
        return None
    hits, broken, bookable, tally = scan or ({}, [], None, None)
    return live_report(hits, broken, bookable, chat_id, checks=(tally or {}).get("checks", 1))


def stop(chat_id, target=None):
    """-> (stopped watches, matched). A NAMED target that matches nothing stops nothing (the old
    behaviour cancelled every movie watch in the chat when "cancel spiderman" matched none)."""
    if target:
        named = _named(chat_id, target)
        if not named:
            return [], False
        return watchspec.finish(named[0]["id"], "cancelled", chat_id=chat_id), True
    return watchspec.finish(None, "cancelled", chat_id=chat_id), True


def describe_active(chat_id):
    return [w.get("title") or "?" for w in watchspec.load_all(chat_id)]
