"""Classifies an incoming chat message so movie and bus can share one Telegram
poll (never run two getUpdates consumers on one bot token - see the deploy
notes). This is the only file that knows both domains exist.
"""

import re

from . import llm
from .bus import runner as bus_runner
from .bus import watchspec as bus_watchspec
from .movies import runner as movies_runner
from .movies import watchspec as movies_watchspec

BUS_WORDS = ("bus", "abhibus", "coach")
MOVIE_WORDS = ("movie", "cinema", "film", "show", "ticket", "4dx", "imax", "screen")
ROUTE_PATTERN = re.compile(r"\bfrom\s+\w+.{0,20}\bto\s+\w+", re.I)


def classify(text):
    """"movie" | "bus" - never raises, always returns something usable."""
    low = text.lower()

    if any(w in low for w in BUS_WORDS) or ROUTE_PATTERN.search(low):
        return "bus"
    if any(w in low for w in MOVIE_WORDS):
        return "movie"

    # Ambiguous wording (e.g. "status", "cancel") - let whichever domain is
    # actually running decide, rather than a keyword list guessing.
    movie_active = bool(movies_watchspec.load())
    bus_active = bool(bus_watchspec.load())
    if movie_active and not bus_active:
        return "movie"
    if bus_active and not movie_active:
        return "bus"

    # Still unclear (both or neither active) - one cheap classification call.
    guess = llm.classify_domain(text) if llm.available() else None
    return guess or "movie"      # movie is the only domain with a standing default


def answer(cmd, movie_ctx, bus_ctx):
    """Route one chat command to the right domain's answer().

    movie_ctx = (hits, broken, bookable, tally), bus_ctx = (hits, broken).
    """
    if classify(cmd) == "bus":
        bus_runner.answer(cmd, *bus_ctx)
    else:
        movies_runner.answer(cmd, *movie_ctx)
