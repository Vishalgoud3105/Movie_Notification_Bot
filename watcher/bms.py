"""Talking to BookMyShow and turning its JSON into showtimes.

Uses the JSON API the BMS Android app calls. The website returns 403 to any
non-browser client, which is what killed the original scraper.
"""

import datetime as dt
import random
import time

import requests

from .config import *


def squash(s):
    """'4DX 3D' -> '4DX3D'. Kills spacing/punctuation differences before matching."""
    return "".join(c for c in s.upper() if c.isalnum())


def to_minutes(show_time):
    """'07:10 PM' or '19:10' -> minutes since midnight."""
    show_time = show_time.strip()
    fmt = "%I:%M %p" if show_time[-2:].upper() in ("AM", "PM") else "%H:%M"
    t = dt.datetime.strptime(show_time, fmt)
    return t.hour * 60 + t.minute


def fetch(session, date_code):
    """One date's showtimes, or None if BMS could not be reached at all.

    None and {} must stay distinguishable: an empty result means "date not open
    yet" (stay quiet), None means the watcher is broken (must be loud, see main).
    """
    params = {
        "appCode": "MOBAND2",
        "appVersion": "14304",
        "language": "en",
        "eventCode": EVENT_CODE,
        "regionCode": REGION_CODE,
        "subRegion": REGION_CODE,
        "bmsId": BMS_ID,
        "token": "67x1xa33f3sf",
        "query": "",
        "dateCode": date_code,
    }
    for attempt in range(3):
        for url in ENDPOINTS:
            try:
                r = session.get(url, params=params, headers=HEADERS, timeout=25)
                if r.status_code == 200:
                    data = r.json()
                    if url is not ENDPOINTS[0]:
                        print("note: primary endpoint failed, served by %s" % url)
                    return data
                print("HTTP %s for %s from %s (attempt %d)"
                      % (r.status_code, date_code, url.rsplit("/", 1)[-1], attempt + 1))
            except (requests.RequestException, ValueError) as e:
                print("request error for %s from %s: %s" % (date_code, url.rsplit("/", 1)[-1], e))
        time.sleep(5 * (attempt + 1) + random.uniform(0, 4))
    return None


def walk(node):
    """Every dict anywhere in a JSON structure, at any depth."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            for d in walk(v):
                yield d
    elif isinstance(node, list):
        for v in node:
            for d in walk(v):
                yield d


def find_venues(data):
    """[(venue_name, [show dicts])] found by shape rather than by fixed path.

    Fallback for when BMS renames or re-nests its response: look for any object
    pairing a venue-name string with a list of showtime records. Survives
    restructuring. It cannot survive a genuinely new API with a new contract -
    that case must fail loudly rather than quietly report nothing.
    """
    found = []
    for node in walk(data):
        name = next((v for k, v in node.items()
                     if "VENUE" in k.upper() and "NAME" in k.upper() and isinstance(v, str)), None)
        shows = next((v for k, v in node.items()
                      if isinstance(v, list) and v and isinstance(v[0], dict)
                      and any(kk.upper() == "SHOWTIME" for kk in v[0])), None)
        if name and shows:
            found.append((name, shows))
    return found


def find_variants(data):
    """{event code: 'English 4DX 3D'} for every child event anywhere in the payload."""
    return {d["EventCode"]: "%s %s" % (d.get("EventLang", ""), d.get("EventDimension", ""))
            for d in walk(data)
            if d.get("EventCode") and ("EventLang" in d or "EventDimension" in d)}


def shows_for(data, date_code):
    """Flatten the API payload to [(key, text)] for shows inside the time window.

    Empty until bookings for date_code actually open. BMS does NOT 404 an
    unopened date - it quietly serves today's shows instead - so the Date in
    the response must be checked or every run reports a false opening.
    """
    lo, hi = to_minutes(TIME_FROM), to_minutes(TIME_TO)
    details = (data or {}).get("ShowDetails") or []

    if details:
        if str(details[0].get("Date")) != date_code:
            print("%s not open yet (API served %s)" % (date_code, details[0].get("Date")))
            return []
        venues = [(v.get("VenueName", "?"), v.get("ShowTimes") or [])
                  for v in details[0].get("Venues") or []]
    else:
        # Expected layout gone. Try to read it by shape before giving up.
        venues = find_venues(data)
        if venues:
            print("WARNING: BMS response layout changed - using tolerant parse. "
                  "Verify the alert against the site before trusting it.")

    # showtimes carry their own EventCode -> language/format lives on the child event
    variants = find_variants(data)

    out = []
    for name, showtimes in venues:
        if VENUES and not any(v in name.lower() for v in VENUES):
            continue
        for show in showtimes:
            # Per-show date guard. Matters most on the tolerant path, where the
            # top-level Date check above was not available to run.
            stamp = next((v for k, v in show.items() if k.upper() == "SHOWDATECODE"), None)
            if stamp and str(stamp) != date_code:
                continue
            if not details and not stamp:
                continue    # drifted layout with no date evidence at all - refuse to guess
            when = show.get("ShowTime", "")
            try:
                mins = to_minutes(when)
            except ValueError:
                continue
            if not lo <= mins <= hi:
                continue
            variant = variants.get(show.get("EventCode"), "").strip()
            attrs = (show.get("Attributes") or "").strip()
            label = squash("%s %s" % (variant, attrs))
            if FORMAT and squash(FORMAT) not in label:
                continue
            if LANGUAGE and squash(LANGUAGE) not in label:
                continue
            key = "|".join([date_code, name, when, show.get("EventCode", ""), attrs])
            out.append((key, {
                "venue": name,
                "time": when,
                "mins": mins,
                "sold": show.get("Availability") == "S",
                "format": " / ".join(x for x in (variant, attrs) if x),
                "price": show.get("MinPrice") or "",
            }))
    return out
