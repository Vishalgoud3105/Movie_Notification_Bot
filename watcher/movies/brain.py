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
"""

import datetime as dt

from . import search, watchspec
from .. import llm
from .config import *
from .messages import live_report, pretty_date, pretty_spec
from ..telegram import send_telegram, wants_report

CANCEL_WORDS = ("cancel", "stop watching", "forget it", "abort", "reset")


def _facts(hits, broken, bookable, tally):
    """Everything the model is allowed to assert, straight from real data."""
    lines = [watchspec.describe()]
    if not watchspec.load():
        # DATES/etc still hold the .env field-name defaults (see
        # run_cycle()'s docstring) but nothing was actually scanned for them -
        # reporting a per-date breakdown here would tell the model there is a
        # live watch on those dates when there is not.
        if broken:
            lines.append("WARNING: could not reach the site for %s" % ", ".join(broken))
        return "\n".join(lines)
    if bookable:
        lines.append("Tickets on sale up to: %s" % pretty_date(bookable))
    for date_code in DATES:
        n = len(hits.get(date_code, []) or [])
        lines.append("%s: %d matching shows found" % (pretty_date(date_code), n))
    if broken:
        lines.append("WARNING: could not reach the site for %s" % ", ".join(broken))
    if tally:
        lines.append("Checks this shift: %s, errors: %s"
                     % (tally.get("checks"), tally.get("errors")))
    return "\n".join(lines)


def _apply_new_watch(spec):
    """Resolve a extracted spec to a real page and start watching it.

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
    }
    watchspec.start(watch)
    note = ""
    if assumed_city:
        note = ("\n\n(You didn't name a city, so I assumed %s - say \"watch it "
                "in Mumbai\" to change that.)" % city.title())
    return ("🎯 Watching: %s\n\nI'll scan every %d minutes and ping you the "
            "moment it appears. Say \"status\" any time.%s"
            % (pretty_spec(watch), SCAN_EVERY // 60, note))


def handle(message, hits, broken, bookable, tally=None):
    """Work out what one message wants and return the reply text."""
    text = (message or "").strip()
    low = text.lower()
    facts = _facts(hits, broken, bookable, tally)

    # 1. deterministic first - these must survive Groq being unavailable
    if wants_report(low):
        return live_report(hits, broken, bookable,
                           checks=(tally or {}).get("checks", 1))

    if any(w in low for w in CANCEL_WORDS):
        ended = watchspec.finish("cancelled")
        return ("Stopped watching %s.\n\nTell me what to watch next."
                % (pretty_spec(ended) if ended else "the default watch"))

    if not llm.available():
        return ("I only understand keywords right now (no GROQ_API_KEY set): "
                "say report, status, check, update or cancel.")

    # 2. the model works out intent
    now = dt.datetime.now(IST)
    spec = llm.extract(text, now.strftime("%Y-%m-%d"), now.strftime("%A"))

    if spec and spec.get("intent") in ("watch", "modify"):
        if spec["intent"] == "modify":
            current = watchspec.load()
            if current:                     # fill the gaps from what is running
                merged = dict(current)
                for k, v in spec.items():
                    if v and k in merged:
                        merged[k] = v
                spec = merged
                spec["intent"] = "watch"
        return _apply_new_watch(spec)

    if spec and spec.get("intent") == "cancel":
        ended = watchspec.finish("cancelled")
        return "Stopped watching %s." % (pretty_spec(ended) if ended else "the default")

    # 3. troubleshooting vs ordinary chat
    if any(w in low for w in ("not working", "broken", "why", "error", "fix",
                              "problem", "didn't get", "did not get", "stopped")):
        reply = llm.troubleshoot(text, facts)
    else:
        reply = llm.chat(text, facts)

    return reply or ("I didn't catch that. Say \"status\" for a report, or "
                     "describe what to watch, e.g. \"watch Spider-Man 4DX 3D at "
                     "Irrum Manzil on 8 and 9 Aug, morning to evening\".")


def reply(message, hits, broken, bookable, tally=None):
    send_telegram(handle(message, hits, broken, bookable, tally))
