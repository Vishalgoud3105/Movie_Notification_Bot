"""Settings, endpoints and request headers.

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


# Both verified to return identical payloads. If BMS retires one, the other is
# tried automatically. A brand new API with a different contract cannot be
# guessed - that case exits non-zero instead of pretending to work.
ENDPOINTS = [
    "https://in.bookmyshow.com/api/movies-data/showtimes-by-event",
    "https://in.bookmyshow.com/api/v2/mobile/showtimes/byevent",
]


# MUST be the 4DX 3D child code, not the parent ET00447840. Verified: the parent
# query returns only English 2D shows - the 4DX shows are invisible from it.
# Each format is its own event as far as this API is concerned.
EVENT_CODE = os.environ.get("EVENT_CODE", "ET00502630")


REGION_CODE = os.environ.get("REGION_CODE", "HYD")


DATES = [d.strip() for d in os.environ.get("DATES", "20260808,20260809").split(",") if d.strip()]


TIME_FROM = os.environ.get("TIME_FROM", "06:00")


TIME_TO = os.environ.get("TIME_TO", "20:00")


VENUES = [v.strip().lower() for v in os.environ.get("VENUES", "").split(",") if v.strip()]


# "4DX 3D" is its own child event (ET00502630 here) and is NOT the same as plain
# 4DX or 4DX 2D. Matched against the child-event dimension and the venue's show
# Attributes, with punctuation stripped so "4DX 3D"/"4DX-3D"/"4DX3D" all hit and
# "4DX 2D" does not. Blank = any.
FORMAT = os.environ.get("FORMAT", "4DX 3D")


LANGUAGE = os.environ.get("LANGUAGE", "English")


# Which ticketing site to read. BookMyShow 403s every datacenter IP (verified on
# GitHub Actions and Oracle Cloud), so "district" is the only source that works
# from a server. "bms" still works from a residential connection.
SOURCE = os.environ.get("SOURCE", "district").strip().lower()
SITE = "District" if SOURCE == "district" else "BookMyShow"   # for user-facing text
DISTRICT_URL = os.environ.get(
    "DISTRICT_URL",
    "https://www.district.in/movies/"
    "spider-man-brand-new-day-movie-tickets-in-hyderabad-MV194537")

# Groq powers understanding messages and phrasing replies - never detection.
# Without a key the bot still works; it just falls back to keyword commands.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
# GROQ_API_KEY is read from the environment at call time, never stored here.

MOVIE_SLUG = os.environ.get("MOVIE_SLUG", "spiderman-brand-new-day")


MOVIE_NAME = os.environ.get("MOVIE_NAME", "Spider-Man: Brand New Day")


STATE_FILE = os.environ.get("STATE_FILE", "seen.json")


# --serve only: how often BookMyShow is scanned, and how long each Telegram long
# poll is held open. Chat replies are instant; BMS is still only hit every 10 min.
SCAN_EVERY = int(os.environ.get("SCAN_EVERY", "600"))


LONG_POLL = int(os.environ.get("LONG_POLL", "25"))


# Stable per-install device identity. A device id that changes every run looks
# far more synthetic than one that stays put, so derive it from the chat id.
BMS_ID = "1.%s.1707213758822" % (os.environ.get("TELEGRAM_CHAT_ID", "21345445")[:12] or "21345445")


HEADERS = {
    "x-bms-id": BMS_ID,
    "x-region-code": REGION_CODE,
    "x-subregion-code": REGION_CODE,
    "x-platform": "AND",
    "x-platform-code": "ANDROID",
    "x-app-code": "MOBAND2",
    "x-app-version": "14.3.4",
    "x-device-make": "Google-Pixel XL",
    "x-screen-height": "2392",
    "x-screen-width": "1440",
    "x-screen-density": "3.5",
    "x-network": "Android | WIFI",
    "user-agent": "Dalvik/2.1.0 (Linux; U; Android 12; Pixel XL Build/SP1A.211105.003)",
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "accept-encoding": "gzip",
}


# India has no DST, so a fixed offset is exactly right and needs no tzdata.
# GitHub Actions runs in UTC; every shift boundary below is your local time.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


SHIFTS = [("Morning", 7, 12), ("Afternoon", 12, 18), ("Evening", 18, 21), ("Night", 21, 24)]


def shift_at(now):
    """('Morning', 7, 12) for an IST datetime, or None between midnight and 7am."""
    return next((s for s in SHIFTS if s[1] <= now.hour < s[2]), None)


# Screen-brand noise baked into venue names ("PVR Superplex Inorbit: LUXE,
# PXL, 4DX: Cyberabad"). Dropped for readability; the format is in the header.
SCREEN_WORDS = {"LUXE", "PXL", "4DX", "IMAX", "GOLD", "ONYX", "INSIGNIA", "PLAYHOUSE",
                "DIRECTOR'S CUT", "SUPERPLEX", "P[XL]", "ICE", "MX4D", "EPIQ"}


# No LLM here, just keyword matching - so accept the words a person would
# actually type rather than demanding an exact command.
ASK_WORDS = ("report", "status", "check", "update", "news", "any luck", "open yet")
