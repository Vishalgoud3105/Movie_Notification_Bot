"""Deciding what a chat message means, and answering it.

Routing order matters and is deliberate:

  1. Deterministic keywords first (report/status/check/...). These must work
     when Groq is down, out of credit, or slow - they are how you find out
     whether the watcher is alive, so they can never depend on a model.
  2. Then the LLM, for setting up a watch, changing one, cancelling, or just
     talking.
  3. Then a plain fallback, so an unparsed message still gets a useful reply.

The model decides what you MEANT. Whether a show exists is only ever answered
from parsed site data.

Several movies can be active at once (see watchspec.py). "cancel"/"modify"
need to resolve WHICH one when more than one is - see _match_named() and the
modify branch in handle() for the (documented, not perfect) rules.
"""

import datetime as dt

from . import search, watchspec
from .. import llm
from .config import *
from .messages import example_watch_phrase, live_report, pretty_date, pretty_spec
from ..telegram import wants_report

CANCEL_WORDS = ("cancel", "stop watching", "forget it", "abort", "reset")


def _match_named(text, chat_id):
    """The id of `chat_id`'s own active watch named in `text` ("cancel
    spiderman"), or None if none is named - callers treat None as "act on
    every watch this chat can see"."""
    for w in watchspec.load_all(chat_id):
        if (w.get("title") or "").lower() in text:
            return w["id"]
    return None


def _facts(chat_id, hits, broken, bookable, tally):
    """Everything the model is allowed to assert, straight from real data.

    `hits` is {watch_id: {date_code: [(key, show)]}} - see
    movies/runner.py::run_cycle().
    """
    lines = [watchspec.describe(chat_id)]
    watches = watchspec.load_all(chat_id)
    if not watches:
        if broken:
            lines.append("WARNING: could not reach the site for %s" % ", ".join(broken))
        return "\n".join(lines)
    if bookable:
        lines.append("Tickets on sale up to: %s" % pretty_date(bookable))
    for w in watches:
        wh = (hits or {}).get(w["id"], {})
        for date_code in w.get("dates", []):
            dc = date_code.replace("-", "")
            n = len(wh.get(dc, []) or [])
            lines.append("%s - %s: %d matching shows found" % (w.get("title"), pretty_date(dc), n))
    if broken:
        lines.append("WARNING: could not reach the site for %s" % ", ".join(broken))
    if tally:
        lines.append("Checks this shift: %s, errors: %s"
                     % (tally.get("checks"), tally.get("errors")))
    return "\n".join(lines)


def _apply_new_watch(spec, chat_id):
    """Resolve a extracted spec to a real page and start watching it, scoped
    to `chat_id`.

    Returns the reply to send. Refuses rather than guessing when the title
    cannot be found - a watch pointed at a URL that does not exist would look
    perfectly healthy and never fire.
    """
    title = spec.get("title")
    if not title:
        return "Which movie or show should I watch? Give me a name and I'll find it."

    if search.looks_like_event(title):
        return ("That looks like a live event rather than a film. I can only "
                "watch movie showtimes right now - District's event pages use a "
                "different structure I haven't taught the watcher to read yet.")

    city = (spec.get("city") or HOME_CITY).lower()
    assumed_city = not spec.get("city")
    hit = search.find(title, city)
    if not hit:
        return ("I couldn't find \"%s\" on District. Check the spelling, or it "
                "may not be listed yet." % title)

    dates = spec.get("dates") or []
    if not dates:
        return ("Found %s. Which dates should I watch? e.g. \"8th and 9th August\"."
                % hit["title"])

    watch = {
        "title": hit["title"],
        "url": hit["url"],
        "city": hit["city"] or spec.get("city"),
        "movie_id": hit["movie_id"],
        "dates": dates,
        "format": spec.get("format") or "",
        "language": spec.get("language") or "",
        "venues": spec.get("venues") or [],
        "time_from": spec.get("time_from") or "00:00",
        "time_to": spec.get("time_to") or "23:59",
        # free text, no fixed valid-value set - every cinema names its own
        # seat tiers, matched as a substring at report time (district.py)
        "seat_category": (spec.get("seat_category") or "").strip().lower(),
        "chat_id": chat_id,
    }
    if spec.get("id"):
        watch["id"] = spec["id"]
    else:
        # Watching the same movie+dates again reinforces the existing entry
        # instead of starting a wasteful duplicate that double-scans it.
        # Scoped to this chat's own watches only - the owner watching the
        # same movie as a group must get their own independent watch.
        existing = next((w for w in watchspec.load_all(chat_id)
                         if w.get("movie_id") == hit["movie_id"]
                         and set(w.get("dates", [])) == set(dates)), None)
        if existing:
            watch["id"] = existing["id"]
    watchspec.start(watch)
    note = ""
    if assumed_city:
        note = ("\n\n(You didn't name a city, so I assumed %s - say \"watch it "
                "in Mumbai\" to change that.)" % city.title())
    return ("🎯 Watching: %s\n\nI'll scan every %d minutes and ping you the "
            "moment it appears. Say \"status\" any time.%s"
            % (pretty_spec(watch), SCAN_EVERY // 60, note))


def handle(message, chat_id, hits, broken, bookable, tally=None):
    """Work out what one message wants and return the reply text.

    Every watchspec lookup here is scoped to `chat_id` - a watch belongs to
    the chat that created it, so "status"/"cancel"/"modify" typed in the
    owner's DM must never see, alert into, or touch a group's own watch, and
    vice versa (see watchspec.load_all()'s chat_id filtering).
    """
    text = (message or "").strip()
    low = text.lower()
    facts = _facts(chat_id, hits, broken, bookable, tally)

    # 1. deterministic first - these must survive Groq being unavailable
    if wants_report(low):
        return live_report(hits, broken, bookable, chat_id,
                           checks=(tally or {}).get("checks", 1))

    if any(w in low for w in CANCEL_WORDS):
        stopped = watchspec.finish(_match_named(low, chat_id), "cancelled", chat_id=chat_id)
        if not stopped:
            return "Nothing to cancel - no movie is being watched."
        return ("Stopped watching: %s.\n\nTell me what to watch next."
                % "; ".join(pretty_spec(w) for w in stopped))

    if not llm.available():
        return ("I only understand keywords right now (no GROQ_API_KEY set): "
                "say report, status, check, update or cancel.")

    # 2. the model works out intent
    now = dt.datetime.now(IST)
    spec = llm.extract(text, now.strftime("%Y-%m-%d"), now.strftime("%A"))

    if spec and spec.get("intent") in ("watch", "modify"):
        if spec["intent"] == "modify":
            # Only unambiguous when exactly one movie is active in THIS chat -
            # with several, guessing which one a bare "make it evening only"
            # refers to risks silently mutating the wrong one, so it falls
            # through and starts a new watch instead (deduped against an
            # identical existing one by _apply_new_watch() anyway).
            active = watchspec.load_all(chat_id)
            if len(active) == 1:
                current = active[0]
                merged = dict(current)
                for k, v in spec.items():
                    if v and k in merged:
                        merged[k] = v
                spec = merged
                spec["intent"] = "watch"
                spec["id"] = current["id"]
        return _apply_new_watch(spec, chat_id)

    if spec and spec.get("intent") == "cancel":
        stopped = watchspec.finish(_match_named(low, chat_id), "cancelled", chat_id=chat_id)
        if not stopped:
            return "Nothing to cancel - no movie is being watched."
        return "Stopped watching: %s." % "; ".join(pretty_spec(w) for w in stopped)

    # 3. troubleshooting vs ordinary chat
    if any(w in low for w in ("not working", "broken", "why", "error", "fix",
                              "problem", "didn't get", "did not get", "stopped")):
        reply = llm.troubleshoot(text, facts)
    else:
        reply = llm.chat(text, facts)

    return reply or ("I didn't catch that. Say \"status\" for a report, or "
                     "describe what to watch, e.g. \"%s\"." % example_watch_phrase())
