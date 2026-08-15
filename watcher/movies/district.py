"""Reading showtimes from District (district.in), Zomato's ticketing platform.

Why not BookMyShow: BMS returns 403 to every datacenter IP, verified on GitHub
Actions (Azure) and Oracle Cloud (Hyderabad). District answers normally from the
same Oracle VM, which is what makes hosting this off a home connection possible.

District is also a better source than BMS was:
  - the screen format is an explicit field (`scrnFmt: "4DX-3D"`), not hidden
    behind a separate child event code the way BMS hid 4DX
  - a requested date is echoed back as `searchDate`, so an unopened date cannot
    silently masquerade as today's shows the way it does on BMS
  - sessions carry real seat counts (`avail` / `total`), not just a sold-out flag
  - each session's own "areas" list carries real per-seat-category data (a
    label like "PRIME ROWS"/"RECLINER ROWS" - naming is cinema-specific, not
    a fixed tier set - plus price and live availability per category) -
    added 15 Aug 2026 after live-checking a real session object; this was
    always in the page's own __NEXT_DATA__, just never extracted before, so
    every alert reported no price at all ("not exposed per session on this
    page" was wrong - it's right there). See _cheapest_area() and
    SEAT_CATEGORY in config.py for how this feeds an optional category filter.
  - a session's own sid/pid/cid/mid fields (sessionId/providerId/cinemaId/
    moviecode) are exactly what District's own seat-select API
    (gw/consumer/movies/v1/select-seat) needs for real per-seat data - not
    used yet (areas already gives category-level price+availability for
    free), but confirmed live 15 Aug 2026: that endpoint returns real seat
    data with a *self-minted* x-guest-token (not server-validated), so a
    genuine per-seat drill-down is a real option later if needed.

The page is server-rendered: everything needed sits in the __NEXT_DATA__ blob.
Note the page ALSO carries schema.org JSON-LD showtimes - do not parse those.
They are capped (1 of the 15 real 4DX sessions on 4 Aug appeared there), so
using them would silently under-report.
"""

import datetime as dt
import json
import random
import re
import time

import requests

from .bms import squash, to_minutes    # bms imports district lazily, so no cycle
from .config import *

# Times in `showTime` carry no timezone and are UTC; India is UTC+5:30.
IST_OFFSET = dt.timedelta(hours=5, minutes=30)

NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)

BROWSER_HEADERS = {
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "accept-encoding": "gzip, deflate",
}


def to_iso(date_code):
    """'20260808' -> '2026-08-08', the form District's ?fromdate= wants."""
    return "%s-%s-%s" % (date_code[:4], date_code[4:6], date_code[6:8])


def fetch(session, date_code):
    """One date's page, parsed to the __NEXT_DATA__ dict.

    Returns None if District could not be reached at all - kept distinct from
    an empty result so main() can exit non-zero rather than sit quietly on a
    broken watcher.
    """
    url = "%s?fromdate=%s" % (DISTRICT_URL, to_iso(date_code))
    for attempt in range(3):
        try:
            r = session.get(url, headers=BROWSER_HEADERS, timeout=30)
            if r.status_code == 200:
                m = NEXT_DATA.search(r.text)
                if not m:
                    print("no __NEXT_DATA__ in the page for %s - layout changed "
                          "or a challenge page was served" % date_code)
                    return {}
                return json.loads(m.group(1))
            print("HTTP %s for %s (attempt %d)" % (r.status_code, date_code, attempt + 1))
        except (requests.RequestException, ValueError) as e:
            print("request error for %s: %s" % (date_code, e))
        time.sleep(5 * (attempt + 1) + random.uniform(0, 4))
    return None


def _sessions_block(data):
    """The movieSessions entry, whatever key it was stored under."""
    try:
        state = data["props"]["pageProps"]["data"]["serverState"]
    except (KeyError, TypeError):
        return None
    blocks = state.get("movieSessions") or {}
    for value in blocks.values():          # keyed like 'rrfdpndypd2026-08-08'
        if isinstance(value, dict):
            return value
    return None


def show_dates(data):
    """Every date District is currently selling, e.g. ['2026-08-04', ...]."""
    try:
        movies = data["props"]["pageProps"]["data"]["serverState"]["mdpV2MovieData"]
    except (KeyError, TypeError):
        return []
    for entry in movies.values():
        if isinstance(entry, dict) and entry.get("showDates"):
            return entry["showDates"]
    return []


def _cheapest_area(areas, wanted_category):
    """The area to report for one session: cheapest available area matching
    `wanted_category` (a substring match against label/code, case-
    insensitive), or cheapest available area overall when no category is
    wanted. None if nothing available matches - callers must treat that as
    "this session doesn't have what was asked for", not "free"/unpriced.
    """
    areas = areas or []
    if wanted_category:
        areas = [a for a in areas
                 if wanted_category in (a.get("label") or "").lower()
                 or wanted_category in (a.get("code") or "").lower()]
    priced = [a for a in areas
             if a.get("price") is not None and (a.get("sAvail") or 0) > 0]
    return min(priced, key=lambda a: a["price"]) if priced else None


def shows_for(data, date_code):
    """[(key, show)] for sessions matching the format, language, time window
    and (when set) seat category.

    Empty until 4DX 3D is actually scheduled for that date. District echoes the
    date it searched, so a mismatch means the answer is about a different day
    and must not be reported.
    """
    block = _sessions_block(data or {})
    if not block:
        return []

    wanted_date = to_iso(date_code)
    searched = block.get("searchDate")
    if searched and searched != wanted_date:
        print("%s: District answered for %s - ignoring" % (date_code, searched))
        return []

    lo, hi = to_minutes(TIME_FROM), to_minutes(TIME_TO)
    out = []
    for cinema in block.get("arrangedSessions") or []:
        venue = cinema.get("entityName") or "?"
        if VENUES and not any(v in venue.lower() for v in VENUES):
            continue
        for s in cinema.get("sessions") or []:
            fmt = (s.get("scrnFmt") or "").strip()
            if FORMAT and squash(FORMAT) not in squash(fmt):
                continue

            raw = s.get("showTime") or ""
            try:                      # '2026-08-04T04:40', UTC, no offset
                when_ist = dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M") + IST_OFFSET
            except ValueError:
                continue
            if when_ist.strftime("%Y-%m-%d") != wanted_date:
                continue              # UTC->IST can roll a late show onto the next day
            mins = when_ist.hour * 60 + when_ist.minute
            if not lo <= mins <= hi:
                continue

            # A session's own "areas" list carries real per-category price +
            # live availability (see module docstring) - if a seat category
            # was asked for and this session doesn't have one matching with
            # actual seats free, it's not a match: reporting it "found"
            # anyway would be a false-positive alert for a category that
            # isn't actually bookable here.
            area = _cheapest_area(s.get("areas"), SEAT_CATEGORY)
            if SEAT_CATEGORY and not area:
                continue

            avail = s.get("avail")
            out.append((
                "|".join([date_code, venue, raw, fmt, str(s.get("audi", ""))]),
                {
                    "venue": venue,
                    "time": when_ist.strftime("%I:%M %p").lstrip("0"),
                    "mins": mins,
                    "sold": avail == 0,
                    "format": fmt,
                    "price": area["price"] if area else "",
                    "seat_category": area.get("label") if area else None,
                    "seats": avail,
                },
            ))
    return out
