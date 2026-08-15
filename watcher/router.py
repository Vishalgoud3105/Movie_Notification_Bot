"""Classifies an incoming chat message across every registered domain, so
they can all share one Telegram poll (never run two getUpdates consumers on
one bot token - see the deploy notes). This is the only file that knows
every domain exists.

One entry in DOMAINS per domain - adding a future one (concert tickets,
cricket tickets, ...) means adding one entry here, nothing else in this file
changes. Each entry names the words that identify it in a message (so "check
bus", "cancel movie", "bus status" all route deterministically, no LLM
needed) plus its runner/watchspec modules.
"""

import re

from . import llm
from .bus import runner as bus_runner
from .bus import watchspec as bus_watchspec
from .movies import runner as movies_runner
from .movies import watchspec as movies_watchspec

DOMAINS = {
    "movie": {
        "words": ("movie", "movies", "cinema", "film", "show", "ticket", "4dx", "imax", "screen"),
        "runner": movies_runner,
        "watchspec": movies_watchspec,
    },
    "bus": {
        "words": ("bus", "buses", "abhibus", "coach"),
        "runner": bus_runner,
        "watchspec": bus_watchspec,
    },
}

ROUTE_PATTERN = re.compile(r"\bfrom\s+\w+.{0,20}\bto\s+\w+", re.I)


def _mentions(name, low):
    if any(w in low for w in DOMAINS[name]["words"]):
        return True
    return name == "bus" and bool(ROUTE_PATTERN.search(low))


def classify(text, chat_id=None):
    """Every domain this message is actually about - usually one, more than
    one if it explicitly names several (e.g. "watch movie X and also watch
    bus from A to B"), so nothing gets silently dropped from a combined
    request the way a single-winner keyword match would.

    `chat_id` scopes the ambiguous-routing fallback below to THIS chat's own
    watches - without it, a bare "status" typed in the owner's DM could route
    to bus just because some unrelated group has a bus watch active, even
    though the owner has none, the same cross-chat leak class as the
    watchspec-level chat_id fix (see watcher/bus/watchspec.py's docstring).
    """
    low = (text or "").lower()
    named = [name for name in DOMAINS if _mentions(name, low)]
    if named:
        return named

    # Nothing named explicitly (e.g. bare "status"/"cancel") - route to
    # whichever domain actually has a live watch IN THIS CHAT, so generic
    # wording still lands somewhere sensible without saying "movie"/"bus"
    # every time.
    active = [name for name, dom in DOMAINS.items() if dom["watchspec"].load_all(chat_id)]
    if len(active) == 1:
        return active

    # Still unclear (several or none active in this chat) - one cheap
    # classification call.
    guess = llm.classify_domain(text) if llm.available() else None
    return [guess] if guess in DOMAINS else ["movie"]   # movie is the oldest, most familiar default


def answer(chat_id, cmd, ctx_by_domain):
    """Route one chat command to every domain it's actually about.

    ctx_by_domain: {"movie": (hits, broken, bookable, tally), "bus": (hits, broken)}
    """
    for name in classify(cmd, chat_id):
        dom = DOMAINS.get(name)
        if dom:
            dom["runner"].answer(chat_id, cmd, *ctx_by_domain[name])
