"""Deciding what a chat message about a bus fare means, and answering it.

Mirrors watcher/movies/brain.py's routing order and reasoning exactly:
deterministic keywords first (must survive Groq being down), then the LLM for
setting up/changing/cancelling a watch, then a grounded chat fallback. The
model decides what you MEANT; whether a fare exists is only ever answered from
parsed site data.
"""

import datetime as dt

from . import watchspec
from .. import llm
from .config import *
from .messages import pretty_date, status_text
from .prompt_template import CHAT_SYSTEM, EXTRACT_SYSTEM, EXTRACT_USER, TROUBLESHOOT_SYSTEM
from ..telegram import send_telegram, wants_report

CANCEL_WORDS = ("cancel", "stop watching", "forget it", "abort", "reset")
OWNER_CONTEXT = "a traveller in Hyderabad watching bus fares"


def _refuse_unresolvable(from_city, to_city):
    """None if the route looks watchable, else a reply explaining why not.

    Checked against AbhiBus's real city data before a watch starts - without
    this, an unbookable city (or a state name typed as a city) only surfaces
    10 minutes later during a scan, misreported as "AbhiBus unreachable"
    instead of the actual problem. Mirrors movies/brain.py refusing a title
    search.find() can't resolve, rather than silently watching a dead route.
    """
    from . import abhibus
    for city in (from_city, to_city):
        _id, note = abhibus.resolve(city)
        if note == "no-direct-hub":
            return ("AbhiBus doesn't sell direct tickets from \"%s\" - try a "
                    "nearby larger city instead." % city)
        if note == "not-found":
            return ("I couldn't find \"%s\" as a city. Check the spelling, or "
                    "it may not be a served location." % city)
        # note is None (found, or a transient lookup failure) - don't block on
        # a network hiccup here; the real scan will just retry it later.
    return None


def _apply_new_watch(spec):
    """Validate an extracted spec and start watching it. Returns the reply text."""
    from_city = (spec.get("from_city") or "").strip()
    to_city = (spec.get("to_city") or "").strip()
    date = spec.get("date")

    if not (from_city and to_city):
        return "Which route should I watch? Give me a \"from\" and \"to\" city."
    if not date:
        return "Found the route. Which date should I watch? e.g. \"20th August\"."
    if from_city.strip().lower() == to_city.strip().lower():
        return "That's the same city twice - where are you actually travelling to?"

    date_code = date.replace("-", "")
    if date_code < dt.datetime.now(IST).strftime("%Y%m%d"):
        # Caught here, not left to run_cycle_bus's own expiry check - a watch
        # that starts already-expired would confirm "🎯 Watching..." and then
        # silently vanish at the next scan with no explanation ever sent.
        try:
            when = pretty_date(date_code)
        except ValueError:
            when = date
        return "%s has already passed - what date did you actually mean?" % when

    target_price = spec.get("target_price")
    if target_price is not None:
        try:
            target_price = float(target_price)
        except (TypeError, ValueError):
            # An LLM extraction is a trust boundary like any other input - a
            # malformed value here must not silently wedge run_cycle_bus's
            # `cheapest["price"] <= target` comparison every cycle forever.
            return "I didn't understand \"%s\" as a price - try a plain number, e.g. 800." % target_price

    refusal = _refuse_unresolvable(from_city, to_city)
    if refusal:
        return refusal

    watch = {
        "from_city": from_city.lower(),
        "to_city": to_city.lower(),
        "date": date_code,
        "target_price": target_price,
    }
    watchspec.start(watch)
    goal = ("I'll alert once a fare hits ₹%s or under, then stop." % watch["target_price"]
            if watch["target_price"] else
            "I'll alert every time I see a new lowest price.")
    return ("🚌 Watching: %s -> %s on %s\n\n%s I check AbhiBus every %d "
            "minutes. Say \"status\" any time."
            % (from_city.title(), to_city.title(), date, goal, SCAN_EVERY // 60))


def handle(message, hits=None, broken=None, tally=None):
    """Work out what one message wants and return the reply text."""
    text = (message or "").strip()
    low = text.lower()
    hits, broken = hits or [], broken or []

    # 1. deterministic first - must survive Groq being unavailable
    if wants_report(low):
        return status_text(watchspec.load(), hits, broken)

    if any(w in low for w in CANCEL_WORDS):
        ended = watchspec.finish("cancelled")
        from .messages import pretty_spec
        return ("Stopped watching %s.\n\nTell me what to watch next."
                % (pretty_spec(ended) if ended else "the bus route"))

    if not llm.available():
        return ("I only understand keywords right now (no GROQ_API_KEY set): "
                "say report, status, check, update or cancel.")

    # 2. the model works out intent
    now = dt.datetime.now(IST)
    spec = llm.extract(text, now.strftime("%Y-%m-%d"), now.strftime("%A"),
                       system=EXTRACT_SYSTEM, user_template=EXTRACT_USER)

    if spec and spec.get("intent") in ("watch", "modify"):
        if spec["intent"] == "modify":
            current = watchspec.load()
            if current:
                merged = dict(current)
                for k, v in spec.items():
                    if v and k in merged:
                        merged[k] = v
                spec = merged
        return _apply_new_watch(spec)

    if spec and spec.get("intent") == "cancel":
        ended = watchspec.finish("cancelled")
        from .messages import pretty_spec
        return "Stopped watching %s." % (pretty_spec(ended) if ended else "the bus route")

    # 3. troubleshooting vs ordinary chat
    facts = watchspec.describe()
    if any(w in low for w in ("not working", "broken", "why", "error", "fix",
                              "problem", "didn't get", "did not get", "stopped")):
        reply = llm.troubleshoot(text, facts, system=TROUBLESHOOT_SYSTEM)
    else:
        reply = llm.chat(text, facts, owner_context=OWNER_CONTEXT, system=CHAT_SYSTEM)

    return reply or ("I didn't catch that. Say \"status\" for a report, or "
                     "describe a route, e.g. \"watch bus from Hyderabad to "
                     "Bangalore on 20 Aug, notify under 800\".")


def reply(message, hits=None, broken=None, tally=None):
    send_telegram(handle(message, hits, broken, tally))
