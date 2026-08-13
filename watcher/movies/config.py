"""Movie-domain settings, layered on top of the shared watcher/config.py."""

import os

from ..config import *   # HOME_CITY, GROQ_MODEL, SCAN_EVERY, LONG_POLL, IST, SHIFTS...


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
