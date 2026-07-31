"""BookMyShow showtime watcher -> Telegram.

Talks to BMS's own mobile-app JSON API instead of scraping the website HTML.
The website returns 403 to anything that isn't a real browser session (that is
what killed the old scraper). The app API answers a well-formed app request
normally, and it is one request per date instead of a full page render.

Config via env vars (all optional, defaults below):
  TELEGRAM_API_TOKEN, TELEGRAM_CHAT_ID   required
  EVENT_CODE   ET00447840  (Spider-Man: Brand New Day)
  REGION_CODE  HYD
  DATES        20260808,20260809
  TIME_FROM    06:00      earliest show start to report
  TIME_TO      20:00      latest show start to report
  VENUES       substring filter, comma separated; empty = all venues
  STATE_FILE   seen.json
"""

import datetime as dt
import json
import os
import random
import sys
import time

import requests

# Local runs read .env; GitHub Actions injects the same names as real env vars.
# ponytail: 4 lines instead of a python-dotenv dependency.
if os.path.exists(".env"):
    for line in open(".env"):
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

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


def squash(s):
    """'4DX 3D' -> '4DX3D'. Kills spacing/punctuation differences before matching."""
    return "".join(c for c in s.upper() if c.isalnum())
MOVIE_SLUG = os.environ.get("MOVIE_SLUG", "spiderman-brand-new-day")
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
            sold_out = show.get("Availability") == "S"
            out.append((key, "%s - %s%s%s" % (
                when, name,
                " [%s]" % " / ".join(x for x in (variant, attrs) if x) if (variant or attrs) else "",
                " (SOLD OUT)" if sold_out else "")))
    return out


def send_telegram(text):
    token = os.environ.get("TELEGRAM_API_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("no telegram creds set; message was:\n" + text)
        return
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    for i in range(0, len(text), 3800):  # telegram caps messages at 4096
        r = requests.post(url, json={"chat_id": chat, "text": text[i:i + 3800],
                                     "disable_web_page_preview": True}, timeout=20)
        if r.status_code != 200:
            print("telegram error %s: %s" % (r.status_code, r.text[:200]))


def main():
    seen = set()
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            seen = set(json.load(f))
    # Cron fires on the exact tick for everyone; a random offset keeps this
    # request out of the top-of-the-minute crowd.
    time.sleep(random.uniform(0, 45))

    session = requests.Session()
    fresh, all_keys, broken = [], set(), []
    for i, date_code in enumerate(DATES):
        if i:
            time.sleep(random.uniform(4, 11))  # a person does not fetch 7 dates in 200ms
        data = fetch(session, date_code)
        if data is None:
            broken.append(date_code)
            continue
        # BMS greys out dates it has not scheduled yet; isDisabled mirrors that.
        offered = [d["DateCode"] for d in data.get("ShowDatesArray", []) if not d.get("isDisabled")]
        print("%s: bookable dates on BMS right now -> %s" % (date_code, offered[-3:] or "none"))
        found = shows_for(data, date_code)
        print("%s: %d shows in window" % (date_code, len(found)))
        for key, text in sorted(found):
            all_keys.add(key)
            if key not in seen:
                fresh.append((date_code, text))

    if fresh:
        lines = ["BOOKINGS OPEN - shows between %s and %s:" % (TIME_FROM, TIME_TO)]
        for date_code in DATES:
            day = [t for d, t in fresh if d == date_code]
            if day:
                lines.append("\n%s:" % dt.datetime.strptime(date_code, "%Y%m%d").strftime("%a %d %b"))
                lines.extend("  " + t for t in day)
                lines.append("  book: https://in.bookmyshow.com/movies/%s/buytickets/%s/%s"
                             % (MOVIE_SLUG, EVENT_CODE, date_code))
        send_telegram("\n".join(lines))
    else:
        print("nothing new")

    with open(STATE_FILE, "w") as f:
        json.dump(sorted(all_keys | seen), f)

    if broken:
        # Loud on purpose. "Couldn't reach BMS" looks exactly like "not open yet"
        # from the outside, and silently watching nothing is the one failure that
        # loses the tickets. Non-zero exit -> GitHub marks the run failed and mails you.
        sys.exit("could not reach BMS for %s - watcher is NOT working" % ", ".join(broken))


def test_run():
    """Manual smoke test: prove the whole chain end to end, right now.

    A strict test would find no 4DX 3D shows (none are scheduled anywhere yet),
    send nothing, and prove nothing. So this looks at dates that ARE open and
    relaxes the format filter if it has to, guaranteeing a real message arrives.
    Never touches seen.json, so it cannot suppress the real alert later.
    """
    global FORMAT
    wanted = FORMAT
    session = requests.Session()
    today = dt.date.today().strftime("%Y%m%d")
    probe = fetch(session, today)
    if probe is None:
        sys.exit("TEST FAILED: could not reach BMS - the watcher would not work")

    # Scan open dates until real matching shows turn up - the first open date
    # often has none in this format, which would make for a useless test.
    open_dates = [d["DateCode"] for d in probe.get("ShowDatesArray", [])
                  if not d.get("isDisabled")][:6] or [today]
    print("open dates on BMS: %s" % open_dates)
    payloads, dates, hits = {today: probe}, [], []
    for dc in open_dates:
        if dc not in payloads:
            time.sleep(random.uniform(4, 11))
            payloads[dc] = fetch(session, dc)
        dates.append(dc)
        found = [(dc, t) for _, t in shows_for(payloads.get(dc), dc)]
        print("  %s: %d matching shows" % (dc, len(found)))
        hits += found
        if hits:
            break

    relaxed = False
    if not hits:
        FORMAT, relaxed = "", True      # nothing in this format anywhere - show something real
        hits = [(dc, t) for dc in dates for _, t in shows_for(payloads.get(dc), dc)]

    lines = ["TEST RUN - if this reached your phone, the alert chain works."]
    if relaxed:
        lines.append("\nNo '%s' shows exist on any open date yet, so these are other "
                     "%s shows, listed only to prove delivery." % (wanted, LANGUAGE or "available"))
    lines.append("\nThe real watcher is armed for: %s on %s, %s-%s, %s %s."
                 % (EVENT_CODE, " & ".join(DATES), TIME_FROM, TIME_TO, LANGUAGE, wanted))
    if hits:
        for dc in dates:
            day = [t for d, t in hits if d == dc][:8]
            if day:
                lines.append("\n%s:" % dt.datetime.strptime(dc, "%Y%m%d").strftime("%a %d %b"))
                lines.extend("  " + t for t in day)
    else:
        lines.append("\n(No shows found at all - check EVENT_CODE and REGION_CODE.)")
    send_telegram("\n".join(lines))
    print("test message sent; seen.json untouched")


def demo():
    """Self-check for the real logic: date guard, time window, format filter."""
    global FORMAT, LANGUAGE, VENUES
    VENUES, FORMAT, LANGUAGE = [], "4DX 3D", "English"   # pinned: never read .env
    assert to_minutes("07:10 PM") == 19 * 60 + 10
    assert to_minutes("08:00 AM") == 480
    assert to_minutes("23:45") == 23 * 60 + 45
    assert shows_for({"ShowDetails": [{"Date": "20260801", "Venues": []}]}, "20260808") == [], \
        "must not report today's shows for an unopened date"
    # Shape mirrors the live API: 4DX arrives as its own child event (as
    # ET00502630 does for this movie), and sometimes only as a venue Attribute.
    payload = {"ShowDetails": [{
        "Date": "20260808",
        "Event": {"ChildEvents": [
            {"EventCode": "E4DX", "EventLang": "English", "EventDimension": "4DX 3D"},
            {"EventCode": "E3D", "EventLang": "English", "EventDimension": "3D"},
            {"EventCode": "ETEL", "EventLang": "Telugu", "EventDimension": "4DX 3D"}]},
        "Venues": [
            {"VenueName": "PVR Nexus Mall", "ShowTimes": [
                {"ShowTime": "10:00 AM", "EventCode": "E4DX", "Attributes": "", "Availability": "A"},
                {"ShowTime": "11:30 PM", "EventCode": "E4DX", "Attributes": "", "Availability": "A"},
                {"ShowTime": "07:10 PM", "EventCode": "E4DX", "Attributes": "", "Availability": "S"},
                {"ShowTime": "02:00 PM", "EventCode": "E3D", "Attributes": "", "Availability": "A"},
                {"ShowTime": "03:00 PM", "EventCode": "ETEL", "Attributes": "", "Availability": "A"}]},
            {"VenueName": "AMB Cinemas", "ShowTimes": [
                {"ShowTime": "09:00 AM", "EventCode": "E3D", "Attributes": "ENGLISH 4DX", "Availability": "A"}]}]}]}
    texts = [t for _, t in shows_for(payload, "20260808")]
    assert sorted(texts) == sorted([
        "10:00 AM - PVR Nexus Mall [English 4DX 3D]",
        "07:10 PM - PVR Nexus Mall [English 4DX 3D] (SOLD OUT)",
    ]), texts   # 11:30 PM out of window; plain 3D, Telugu 4DX 3D and bare 4DX all rejected

    # Same payload re-nested and renamed: the tolerant parse must still find it.
    drifted = {"data": {"page": {"cinemaList": [
        {"venueName": "PVR Nexus Mall", "sessions": [
            {"ShowTime": "10:00 AM", "EventCode": "E4DX", "Attributes": "",
             "Availability": "A", "ShowDateCode": "20260808"},
            {"ShowTime": "09:00 AM", "EventCode": "E4DX", "Attributes": "",
             "Availability": "A", "ShowDateCode": "20260807"}]}]}},
        "events": [{"EventCode": "E4DX", "EventLang": "English", "EventDimension": "4DX 3D"}]}
    texts = [t for _, t in shows_for(drifted, "20260808")]
    assert texts == ["10:00 AM - PVR Nexus Mall [English 4DX 3D]"], texts   # wrong day dropped

    # Drifted layout with no date evidence must stay silent, never guess.
    assert shows_for({"x": [{"venueName": "V", "sessions": [
        {"ShowTime": "10:00 AM", "EventCode": "E4DX"}]}],
        "events": [{"EventCode": "E4DX", "EventLang": "English",
                    "EventDimension": "4DX 3D"}]}, "20260808") == []

    FORMAT, LANGUAGE = "", ""       # blank filters = report everything
    assert len(shows_for(payload, "20260808")) == 5
    print("self-check ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        demo()
    elif "--test" in sys.argv:
        test_run()
    else:
        main()
