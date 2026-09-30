"""Movie-domain settings, layered on top of the shared watcher/config.py."""

import os

from ..config import *   # HOME_CITY, MISTRAL_MODEL, SCAN_EVERY, LONG_POLL, IST, SHIFTS...


# Both verified to return identical payloads. If BMS retires one, the other is
# tried automatically. A brand new API with a different contract cannot be
# guessed - that case exits non-zero instead of pretending to work.
ENDPOINTS = [
    "https://in.bookmyshow.com/api/movies-data/showtimes-by-event",
    "https://in.bookmyshow.com/api/v2/mobile/showtimes/byevent",
]


# EVENT_CODE / REGION_CODE / MOVIE_SLUG are read ONLY by the BookMyShow reader
# (bms.py, SOURCE=bms) - unused with the default SOURCE=district, and BMS 403s
# every datacenter IP anyway. They stay a single fixed movie, not per-watch.
# EVENT_CODE must be the 4DX 3D child code, not the parent ET00447840: the
# parent query returns only English 2D shows (each format is its own event).
EVENT_CODE = os.environ.get("EVENT_CODE", "ET00502630")


REGION_CODE = os.environ.get("REGION_CODE", "HYD")


# DATES / TIME_FROM / TIME_TO / VENUES / FORMAT / LANGUAGE are PER-WATCH values:
# watchspec._push() overwrites them from each chat-set watch before every scan,
# so what is written here never reaches a real watch. Defaults are deliberately
# neutral (no dates, whole day, any venue/format/language) - they only feed the
# manual `python watch.py --test` probe, which an .env value can still override.
DATES = [d.strip() for d in os.environ.get("DATES", "").split(",") if d.strip()]


TIME_FROM = os.environ.get("TIME_FROM", "00:00")


TIME_TO = os.environ.get("TIME_TO", "23:59")


VENUES = [v.strip().lower() for v in os.environ.get("VENUES", "").split(",") if v.strip()]


# A seat-category name to match against a session's own area labels
# (district.py's "areas" field - e.g. "CLASSIC ROWS"/"PRIME ROWS"/"RECLINER
# ROWS" for one cinema, "SILVER"/"GOLD"/"PLATINUM" for another). Free text,
# not a fixed enum - every cinema chain names its own tiers, so this is
# matched as a substring at report time rather than validated against a set
# list. Blank = any category.
SEAT_CATEGORY = os.environ.get("SEAT_CATEGORY", "").strip().lower()


# Matched against the session's screen format with punctuation stripped, so a
# watch for "4DX 3D" also hits District's "4DX-3D" but not "4DX 2D" (each
# format is its own child event on BookMyShow). Blank = any. Per-watch - see
# the note above DATES.
FORMAT = os.environ.get("FORMAT", "")


LANGUAGE = os.environ.get("LANGUAGE", "")


# Which ticketing site to read. BookMyShow 403s every datacenter IP (verified on
# GitHub Actions and Oracle Cloud), so "district" is the only source that works
# from a server. "bms" still works from a residential connection.
SOURCE = os.environ.get("SOURCE", "district").strip().lower()
SITE = "District" if SOURCE == "district" else "BookMyShow"   # for user-facing text
# DISTRICT_URL / MOVIE_NAME are also per-watch (each chat-set watch resolves its
# own District page). This default is only the sample movie `--test` probes.
DISTRICT_URL = os.environ.get(
    "DISTRICT_URL",
    "https://www.district.in/movies/"
    "spider-man-brand-new-day-movie-tickets-in-hyderabad-MV194537")

MOVIE_SLUG = os.environ.get("MOVIE_SLUG", "spiderman-brand-new-day")


MOVIE_NAME = os.environ.get("MOVIE_NAME", "Spider-Man: Brand New Day")


STATE_FILE = os.environ.get("STATE_FILE", "seen.json")


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


# Screen-brand noise baked into venue names ("PVR Superplex Inorbit: LUXE,
# PXL, 4DX: Cyberabad"). Dropped for readability; the format is in the header.
SCREEN_WORDS = {"LUXE", "PXL", "4DX", "IMAX", "GOLD", "ONYX", "INSIGNIA", "PLAYHOUSE",
                "DIRECTOR'S CUT", "SUPERPLEX", "P[XL]", "ICE", "MX4D", "EPIQ"}
