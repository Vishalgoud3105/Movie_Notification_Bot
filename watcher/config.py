"""Settings shared by every domain (movies, bus, ...): Telegram/Mistral, timing, clock.

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


# Mistral powers understanding messages and phrasing replies - never detection.
# Without a key the bot still works; it just falls back to keyword commands.
#
# Was Groq (llama-3.3-70b-versatile) until 29 Aug 2026: that model was
# deprecated (shutdown 16 Aug 2026), every call silently returned None
# (llm._call() swallows the error), which looked exactly like the bot
# ignoring every message rather than an LLM outage - see
# [[project-bms-gotchas]]. Groq's suggested replacement (openai/gpt-oss-120b)
# turned out to be a reasoning model that burns tokens on hidden reasoning
# before any visible output, needing every max_tokens budget in llm.py
# retuned - switched providers entirely instead of chasing that per-call.
#
# Model choice, settled empirically 30 Sep 2026 against a FREE-tier key (the
# dashboard's "Completion rate limits per model" page, plus real calls):
#   mistral-medium-latest  -> 429 every time (limit shown: 1 req/s, 20k tok/min)
#   mistral-small-latest   -> 429
#   mistral-large-latest   -> 403 "not available in your subscription tier"
#   the ministral family (3b/8b/14b, open-mistral-nemo = 8b) -> 200
# The errors looked like "account/key/IP problem" (rotated keys, a fresh
# account, two networks all failed identically) - it was the model's free-tier
# availability all along. So: a primary plus a fallback chain (llm._call()
# walks it), not a single hardcoded model.
#
# Ranking of the models that DO work, measured 30 Sep 2026 on this bot's real
# extract/classify/chat jobs: 3 passes x 25 golden messages (bus + movie,
# incl. Telugu-English, typos, self-correction, prompt injection), ORIGINAL
# unpadded prompts so it is the model's own ability:
#   ministral-8b-2512   94.0% fields, 15/15 domain, 1.7s  <- primary
#   ministral-14b-2512  91.3% fields, 15/15 domain, 3.0s  <- fallback (0.5 req/s cap)
#   ministral-3b-2512   88.9% fields, 12/15 domain, 1.4s  <- NOT in the chain:
#       it misroutes "watch hyd to vizag" to movies and gets "on 18th" wrong
#       every time; a confident wrong date silently watches the wrong day,
#       which is worse than "trouble reaching my AI". Add it via
#       MISTRAL_FALLBACK_MODELS only if you'd rather have that than silence.
# Dated ids are pinned on purpose (that's what was measured); a retired id
# 404s and the chain moves on - `--diagnose-llm` shows it. If billing is ever
# added, put mistral-medium-latest first via MISTRAL_MODEL.
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "ministral-8b-2512")
MISTRAL_FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "MISTRAL_FALLBACK_MODELS", "ministral-14b-2512").split(",") if m.strip()]
# Which model TypeSafe Jev sends a message to (see watcher/jev.py + llm._preferred).
# "simple" messages go to the fast model, "complex" ones to the accurate one;
# the other is still the automatic fallback. Only used when Jev is enabled.
JEV_SIMPLE_MODEL = os.environ.get("JEV_SIMPLE_MODEL", "ministral-8b-2512")
JEV_COMPLEX_MODEL = os.environ.get("JEV_COMPLEX_MODEL", "ministral-14b-2512")
# MISTRAL_API_KEY / TYPESAFE_API_KEY are read from the environment at call
# time, never stored here.


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
