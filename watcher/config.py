"""Settings shared by every domain (movies, bus, ...): Telegram/Groq, timing, clock.

Domain-specific settings (movie showtimes, bus routes) live in each domain's
own config.py, layered on top of this one via `from ..config import *`.

Every value has a default here; .env and real environment variables only
override. Import the module rather than its names where a value has to stay
patchable at runtime.
"""

import datetime as dt
import os


# Local runs read .env; a host injects the same names as real env vars.
# Anchored to the repo root rather than the working directory, because a
# scheduler or service can start this from anywhere - and a .env that is not
# found means no Telegram at all, which is a silent failure.
# ponytail: 6 lines instead of a python-dotenv dependency.
DOTENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(DOTENV):
    for _line in open(DOTENV):
        _line = _line.split("#", 1)[0].strip()
        if "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))


# Used when a chat request does not name a city. Domain-agnostic: movies fall
# back to it for District's city page, bus falls back to it when a route names
# only a destination ("watch the cheapest bus to Bangalore").
HOME_CITY = os.environ.get("HOME_CITY", "hyderabad").strip().lower()


# Groq powers understanding messages and phrasing replies - never detection.
# Without a key the bot still works; it just falls back to keyword commands.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
# GROQ_API_KEY is read from the environment at call time, never stored here.


# --serve only: how often a domain's sources are scanned, and how long each
# Telegram long poll is held open. Chat replies are instant either way.
SCAN_EVERY = int(os.environ.get("SCAN_EVERY", "600"))


LONG_POLL = int(os.environ.get("LONG_POLL", "25"))


# India has no DST, so a fixed offset is exactly right and needs no tzdata.
# GitHub Actions runs in UTC; every shift boundary below is your local time.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


SHIFTS = [("Morning", 7, 12), ("Afternoon", 12, 18), ("Evening", 18, 21), ("Night", 21, 24)]


def shift_at(now):
    """('Morning', 7, 12) for an IST datetime, or None between midnight and 7am."""
    return next((s for s in SHIFTS if s[1] <= now.hour < s[2]), None)


# No LLM here, just keyword matching - so accept the words a person would
# actually type rather than demanding an exact command. Shared: "status"/
# "report" means the same thing whichever domain is being asked about.
ASK_WORDS = ("report", "status", "check", "update", "news", "any luck", "open yet")
