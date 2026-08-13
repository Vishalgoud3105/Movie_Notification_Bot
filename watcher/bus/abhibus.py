"""AbhiBus fare search.

Captured 13 Aug 2026 via DevTools, both verified live (this machine and the
Oracle VM, real bus data, real prices):

  GET  https://www.abhibus.com/wap/abus-autocompleter/api/v1/results?s=<text>
       -> city name/alias -> AbhiBus's internal numeric city id, needed
          because the search call below takes ids, not free text.
  POST https://www.abhibus.com/buslist/v3/services
       -> the real "services" array: fares/timings/operators, no HTML
          parsing needed - the site is a React SPA but this is its real data
          API, same relationship District had to BookMyShow's web page.

City ids are resolved live and cached per-process (_cache) rather than kept
as a static list - a fixed list would only ever cover the handful of cities
someone happened to test, the same reason movies/search.py resolves titles
against District's real catalogue instead of a fixed movie list.
"""

import datetime as dt

import requests

from ..config import IST

BASE = "https://www.abhibus.com"
SUGGEST_URL = BASE + "/wap/abus-autocompleter/api/v1/results"
SEARCH_URL = BASE + "/buslist/v3/services"

_HEADERS = {
    "accept": "*/*",
    "origin": BASE,
    "x-app-name": "nextgenweb",
    "user-agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36",
}

# name (lowercased) -> (id|None, note|None). Never expires within a process -
# city ids don't change - but is not persisted to disk, so a restart
# re-resolves once per city, cheaply.
_cache = {}


def _fmt_arrival(raw, arrive_ts, dep_date):
    """AbhiBus's bare "08:10 PM" is misleading on long routes that arrive a
    day (or more) after departure - add the date when arriveTimestamp (a real
    Unix epoch AbhiBus already sends) shows it differs from the departure day.
    """
    if not (arrive_ts and dep_date):
        return raw or "?"
    arrive_date = dt.datetime.fromtimestamp(arrive_ts, IST).date()
    if arrive_date == dep_date:
        return raw or "?"
    return "%s, %s" % (arrive_date.strftime("%a %d %b"), raw or "?")


def _lookup(session, key):
    """key (already lowercased) -> (id|None, note|None).

    note explains a miss: "no-direct-hub" (the name exists in AbhiBus's data
    but has no boarding points - see Amalapuram in a real capture, a real
    town AbhiBus simply doesn't run direct buses from) vs "not-found"
    (nothing matching at all). note is None both on a hit AND on a transient
    lookup failure - callers must not treat "couldn't check right now" the
    same as "confirmed this doesn't exist".
    """
    if key in _cache:
        return _cache[key]

    try:
        r = session.get(SUGGEST_URL, params={"s": key},
                        headers=dict(_HEADERS, accept="application/json, text/plain, */*",
                                     referer=BASE + "/"), timeout=15)
        candidates = r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        candidates = None
    if candidates is None:
        return None, None      # transient - do not cache, do not blame the city

    # stn_rfn=1 means it actually has boarding points (a real, bookable hub) -
    # that is the "is this real" signal, NOT alias_type: common names like
    # "Bangalore" are stored as alias_type="Alias" of the canonical city
    # ("Bengaluru", alias_type="City") but are just as bookable (same id,
    # same boarding points). Filtering on alias_type=="City" would silently
    # fail every search for the name people actually type. What still needs
    # excluding is stn_rfn=0 - nearby villages/sub-areas with no real hub
    # (see Thiruverkadu/Chinnaiyampalayam alongside Chennai in a real capture).
    real = [c for c in candidates if c.get("stn_rfn") == 1]
    match = next((c for c in real if c.get("city", "").lower() == key
                 or c.get("label", "").lower() == key), None) or (real[0] if real else None)

    if match:
        result = (match["id"], None)
    elif candidates:
        result = (None, "no-direct-hub")
    else:
        result = (None, "not-found")
    _cache[key] = result
    return result


def _resolve_city(session, name):
    """A city name/alias -> AbhiBus's numeric id, or None if unusable. Used by
    search() itself, which only needs the id, not why a miss happened."""
    return _lookup(session, name.strip().lower())[0]


def resolve(name):
    """Stand-alone city lookup with its own session, for validating a route
    BEFORE a watch starts (see bus/brain.py) rather than only discovering a
    bad city name during a scan cycle. Returns (id, note) - see _lookup().
    """
    session = requests.Session()
    try:
        session.get(BASE + "/", headers=_HEADERS, timeout=20)
    except requests.RequestException:
        return None, None
    return _lookup(session, name.strip().lower())


def search(from_city, to_city, date_code):
    """(from_city, to_city, "YYYYMMDD") -> list[Hit] | None.

    Hit = {"operator", "price", "seat_type", "depart", "arrive", "seats_left",
           "source", "book_url"}. None means AbhiBus was unreachable; a route
    with genuinely no buses is an empty list, never None - see sources.py.
    """
    session = requests.Session()
    try:
        # Picks up Cloudflare's __cf_bm/AWSALBTG cookies the way a real page
        # load would, before the calls below that expect them to already exist.
        session.get(BASE + "/", headers=_HEADERS, timeout=20)
    except requests.RequestException as e:
        print("abhibus unreachable: %s" % e)
        return None

    src_id = _resolve_city(session, from_city)
    dst_id = _resolve_city(session, to_city)
    if src_id is None or dst_id is None:
        print("abhibus: could not resolve a city id for %r -> %r"
              % (from_city, to_city))
        return None

    jdate = "%s-%s-%s" % (date_code[:4], date_code[4:6], date_code[6:8])
    ddmmyyyy = "%s-%s-%s" % (date_code[6:8], date_code[4:6], date_code[:4])

    try:
        referer = "%s/bus_search/%s/%s/%s/%s/%s/O" % (
            BASE, from_city.title(), src_id, to_city.title(), dst_id, ddmmyyyy)
        payload = {
            "source": from_city.title(), "sourceid": src_id,
            "destination": to_city.title(), "destinationid": dst_id,
            "jdate": jdate, "prd": "mobile", "isReturnJourney": "0",
            "filters": "1", "version": "105",
        }
        r = session.post(SEARCH_URL, json=payload,
                         headers=dict(_HEADERS, **{"content-type": "application/json"},
                                      referer=referer), timeout=20)
    except requests.RequestException as e:
        print("abhibus unreachable: %s" % e)
        return None

    if r.status_code != 200:
        print("abhibus HTTP %s: %s" % (r.status_code, r.text[:200]))
        return None

    try:
        data = r.json()
    except ValueError:
        print("abhibus: non-JSON response, layout may have changed")
        return None

    hits = []
    for svc in data.get("services", []):
        fare = (svc.get("fares") or {}).get("fare")
        if fare is None:
            continue
        timings = svc.get("timings") or {}
        seats = svc.get("seatStats") or {}
        dep_ts = timings.get("startTimestamp")
        dep_date = dt.datetime.fromtimestamp(dep_ts, IST).date() if dep_ts else None
        hits.append({
            "operator": svc.get("travelerAgentName") or "?",
            "price": fare,
            "seat_type": svc.get("busTypeName") or "seat",
            "depart": timings.get("startTime") or "?",
            "arrive": _fmt_arrival(timings.get("arriveTime"),
                                   timings.get("arriveTimestamp"), dep_date),
            "seats_left": seats.get("availableSeats"),
            "source": "abhibus",
            "book_url": referer,   # the search results page - pick the bus, book from there
        })
    return hits
