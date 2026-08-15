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

Deep link, captured and verified 14 Aug 2026 (cold, zero cookies - a user
tapping this fresh from Telegram gets the exact bus's seat map, not the
generic search list):

  https://www.abhibus.com/seat-layout-web/?sourceid=<id>&destinationid=<id>
    &jdate=<YYYY-MM-DD>&serviceKey=<serviceId>&operatorId=<operatorId>&prd=mobile

The query param is named serviceKey but the value it actually needs is each
service's serviceId field (a uniform 10-digit number), NOT its serviceKey
field (format varies wildly per operator - hex, free text, tilde-composite -
and does not work here despite the matching name). Confusing naming on
AbhiBus's part: confirmed by testing 8 different operators - 0/8 worked
with serviceKey, 8/8 worked with serviceId once that mixup was found.

Seat-level filtering (gender, sleeper vs seater):

  POST https://www.abhibus.com/wap/GetSeatLayout
       body: {sourceid, destinationid, jdate, serviceKey: <serviceId>,
              operatorId, prd, isReturnJourney, version, clevertapID}
       -> per-seat data, "TotalSeatList.lowerdeck_seat_nos"/"upperdeck_seat_nos",
          each seat a comma-separated string whose fields match the response's
          own "titles" array: seat_no, row, col, seat_type (LB/UB = lower/
          upper berth i.e. sleeper; other codes are seater), availability
          (Y/N), gender (M/F), fare, ... Cross-checked 14 Aug 2026 against a
          real response's "gentsSeats" list - a seat listed there had "M" in
          this position, confirming the mapping.

A bus's advertised "from ₹X" price is the minimum across ALL its seats, so it
does not tell you the price of a specific gender/seat-type - that only comes
from fetching this per-bus layout. AC/non-AC is different: that is whole-bus
(busTypeName already says "AC .../"NON-AC ..."), so it costs nothing extra to
filter on. Gender/seat-type filtering therefore costs one extra request per
bus checked - see search()'s max_checks for how that is bounded, deliberately,
after this project got rate-limited ("too many requests") from testing this
endpoint too fast in a short window.

Government exclusion + quality signal (added 15 Aug 2026, verified live
against a real 297-service, 100-operator Hyderabad->Bangalore result): a
service in the search response has no ownership field (checked the full key
list - no "isGovernment"/"operatorType"), so state RTC/STC operators are
recognized by name instead (_is_government_operator()) and dropped before
they ever become a Hit. Separately, "rating" (1-5) and "noOfRatings" ARE
already on every service in this same response - no second endpoint needed
for a quality signal, contrary to what you'd assume from RedBus-style sites
that put reviews behind their own page. sources.py uses these two fields to
enforce a minimum quality bar on top of the government exclusion here.
"""

import datetime as dt
import re

import requests

from ..config import IST

BASE = "https://www.abhibus.com"
SUGGEST_URL = BASE + "/wap/abus-autocompleter/api/v1/results"
SEARCH_URL = BASE + "/buslist/v3/services"
SEAT_LAYOUT_URL = BASE + "/wap/GetSeatLayout"

# How many of the cheapest buses to open a seat layout for when a gender or
# seat-type filter is set, before giving up for this cycle. Bounded on
# purpose: AbhiBus rate-limited this project ("too many requests") after a
# burst of testing, and a route can return 100+ buses - checking all of them
# every 10-minute scan would be a real risk, not just a slow one.
MAX_SEAT_CHECKS = 5

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

# Government/state RTC operators - excluded entirely, never returned as a
# hit. There's no "isGovernment" flag on a service (checked a real capture,
# 14 Aug 2026 - only fares/timings/flags/safetyCheck/rating, no ownership
# field), so this matches on the operator name instead: every Indian state
# transport corporation trades under a short RTC/STC-style code (TGSRTC,
# APSRTC, KSRTC, MSRTC, TNSTC, ...) or the full "State Road Transport
# Corporation" phrase. Verified against a real 100-operator Hyderabad-
# Bangalore result set: matched exactly the 2 government operators present
# (TGSRTC, KSRTC Karnataka) and zero of the 98 private ones - private names
# that also contain "Transport" (e.g. "Delta Transport Pvt Ltd", "SBM
# Transport") don't match because the pattern requires the RTC/STC token
# itself, not just the word "Transport".
_GOVT_OPERATOR = re.compile(
    r"\b(?:[A-Z]{2,5}RTC|[A-Z]{2,5}STC|STATE\s+ROAD\s+TRANSPORT"
    r"|ROAD\s+TRANSPORT\s+CORPORATION|STATE\s+TRANSPORT\s+CORPORATION)\b",
    re.I,
)


def _is_government_operator(name):
    return bool(_GOVT_OPERATOR.search(name or ""))


# A bus must clear both before it's ever reported as a hit - not just the
# lowest price, some idea the operator is any good. Applied to `prelim`
# below, before MAX_SEAT_CHECKS picks which buses are worth an extra
# seat-layout request - filtering first means a below-bar bus never spends
# one of those scarce requests only to be dropped afterward anyway.
# Thresholds are a judgment call, not measured from data - AbhiBus's own
# rating scale runs 1-5; 3.5 is "clearly above average", 5 ratings is enough
# that one troll review can't tank an otherwise-fine operator. No user-facing
# way to tune these yet.
MIN_RATING = 3.5
MIN_RATING_COUNT = 5


def _meets_quality_bar(hit):
    """A bus with no rating yet (new, or AbhiBus just hasn't scored it) is
    unproven, not vouched-for - treated as failing the bar rather than
    passing by default: the point is to never hand back a price with no
    safety/quality signal behind it."""
    rating = hit.get("rating")
    return (rating is not None and rating >= MIN_RATING
            and (hit.get("no_of_ratings") or 0) >= MIN_RATING_COUNT)


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


def _matches_ac(bus_type_name, ac):
    """ac: "ac" | "non_ac" | None. busTypeName is whole-bus (e.g. "AC Sleeper",
    "NON-AC Seater") so this needs no extra request - check "non-ac"/"non ac"
    first, since "ac" is a substring of both and would otherwise misclassify."""
    if not ac:
        return True
    low = (bus_type_name or "").lower()
    is_non_ac = "non-ac" in low or "non ac" in low
    return is_non_ac if ac == "non_ac" else (ac == "ac" and not is_non_ac)


def _parse_seats(data):
    """The GetSeatLayout response -> flat list of
    {"seat_no", "seat_type", "available", "gender", "fare"}.

    Field order matches the response's own "titles" array; berth codes
    (LB/UB - lower/upper berth) are sleeper seats, anything else is treated
    as a seater - AbhiBus does not appear to use a richer seat-type code set
    in the samples captured, but this is a heuristic, not a confirmed
    enumeration of every possible code.
    """
    seats = []
    total = (data or {}).get("TotalSeatList") or {}
    for group in ("lowerdeck_seat_nos", "upperdeck_seat_nos"):
        for raw in total.get(group) or []:
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) < 7:
                continue
            seat_no, _row, _col, seat_type, avail, gender, fare = parts[:7]
            try:
                fare = float(fare)
            except ValueError:
                continue
            seats.append({
                "seat_no": seat_no,
                "seat_type": "sleeper" if seat_type.upper() in ("LB", "UB") else "seater",
                "available": avail.upper() == "Y",
                "gender": {"M": "male", "F": "female"}.get(gender.upper()),
                "fare": fare,
            })
    return seats


def get_seat_layout(session, src_id, dst_id, jdate, service_id, operator_id):
    """One bus's real per-seat data, or None if unreachable/unparseable.
    See module docstring for the request shape and field mapping."""
    payload = {
        "sourceid": str(src_id), "destinationid": str(dst_id), "jdate": jdate,
        "serviceKey": str(service_id), "operatorId": str(operator_id),
        "prd": "mobile", "isReturnJourney": "0", "version": "58", "clevertapID": "",
    }
    headers = dict(_HEADERS, **{"content-type": "application/json",
                                "x-app-name": "nextgenmweb",
                                "accept": "application/json, text/plain, */*",
                                "referer": BASE + "/seat-layout-web/"})
    try:
        r = session.post(SEAT_LAYOUT_URL, json=payload, headers=headers, timeout=20)
    except requests.RequestException as e:
        print("abhibus seat layout unreachable: %s" % e)
        return None
    if r.status_code != 200:
        print("abhibus seat layout HTTP %s" % r.status_code)
        return None
    try:
        return _parse_seats(r.json())
    except ValueError:
        return None


def _cheapest_matching_seat(seats, gender, seat_type):
    candidates = [s for s in seats if s["available"]
                 and (not gender or s["gender"] == gender)
                 and (not seat_type or s["seat_type"] == seat_type)]
    return min(candidates, key=lambda s: s["fare"]) if candidates else None


def search(from_city, to_city, date_code, ac=None, seat_type=None, gender=None):
    """(from_city, to_city, "YYYYMMDD") -> list[Hit] | None.

    Hit = {"operator", "price", "seat_type", "depart", "arrive", "seats_left",
           "rating", "no_of_ratings", "source", "book_url"}. None means
           AbhiBus was unreachable; a route with genuinely no (private,
           quality-bar-passing) buses is an empty list, never None - see
           sources.py.

    Government/state RTC operators never appear here at all - see
    _is_government_operator(). rating/no_of_ratings are AbhiBus's own values
    (0-5 stars, review count), already present on every service in the
    search response; sources.scan() uses them to drop buses below its
    quality bar before anything picks a "cheapest" hit.
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

    prelim = []
    for svc in data.get("services", []):
        fare = (svc.get("fares") or {}).get("fare")
        if fare is None:
            continue
        operator = svc.get("travelerAgentName") or "?"
        if _is_government_operator(operator):
            continue        # user wants private operators only
        if not _matches_ac(svc.get("busTypeName"), ac):
            continue        # whole-bus property, free to filter here

        timings = svc.get("timings") or {}
        seat_stats = svc.get("seatStats") or {}
        dep_ts = timings.get("startTimestamp")
        dep_date = dt.datetime.fromtimestamp(dep_ts, IST).date() if dep_ts else None

        service_id, operator_id = svc.get("serviceId"), svc.get("operatorId")
        seat_url = None
        if service_id and operator_id:
            # Deep link straight to this bus's seat map - see module docstring
            # for the serviceId-vs-serviceKey field mixup this depends on.
            # Verified 8/8 across different operators once that was fixed -
            # still sent alongside book_url (the search list), not instead of
            # it, since a link that fails silently with no fallback is worse
            # than a slightly redundant second one.
            seat_url = ("%s/seat-layout-web/?sourceid=%s&destinationid=%s&jdate=%s"
                       "&serviceKey=%s&operatorId=%s&prd=mobile"
                       % (BASE, src_id, dst_id, jdate, service_id, operator_id))

        prelim.append({
            "operator": operator,
            "price": fare,
            "seat_type": svc.get("busTypeName") or "seat",
            "depart": timings.get("startTime") or "?",
            "arrive": _fmt_arrival(timings.get("arriveTime"),
                                   timings.get("arriveTimestamp"), dep_date),
            "seats_left": seat_stats.get("availableSeats"),
            "rating": svc.get("rating"),           # already in the search
            "no_of_ratings": svc.get("noOfRatings"),  # response, zero extra cost
            "source": "abhibus",
            "book_url": referer,       # the search results page - always works
            "seat_url": seat_url,      # direct to this bus's seats - best-effort
            "_service_id": service_id,
            "_operator_id": operator_id,
        })

    prelim = [h for h in prelim if _meets_quality_bar(h)]

    if not (gender or seat_type):
        for h in prelim:
            h.pop("_service_id", None)
            h.pop("_operator_id", None)
        return prelim

    # Gender/seat-type need the real per-seat data (a bus's advertised price
    # is the minimum across every seat, not a specific gender/type - see
    # module docstring). Bounded to the cheapest MAX_SEAT_CHECKS candidates:
    # a busy route can return 100+ buses, and checking all of them every scan
    # would be real abuse, not just slow - this project already got
    # rate-limited once from testing this same endpoint too fast.
    prelim.sort(key=lambda h: h["price"])
    refined = []
    for h in prelim[:MAX_SEAT_CHECKS]:
        sid, oid = h.pop("_service_id", None), h.pop("_operator_id", None)
        if not (sid and oid):
            continue
        seat_layout = get_seat_layout(session, src_id, dst_id, jdate, sid, oid)
        if not seat_layout:
            continue
        best = _cheapest_matching_seat(seat_layout, gender, seat_type)
        if best:
            h = dict(h)
            h["price"] = best["fare"]
            h["seat_no"] = best["seat_no"]
            refined.append(h)
    return refined
